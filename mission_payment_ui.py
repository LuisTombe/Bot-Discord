"""Mission payment dossiers with approved identities and an explicit image review."""
from i18n import format_fragment, number, t
import asyncio
import io
import uuid
from typing import Literal
import discord
from discord import app_commands
from evidence import MAX_BYTES,safe_path
from economy_domain import RANKS
from payment_evidence import CAPTURE_GUIDE,identity,validate_image,file_blob

def clean(value):return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))
def money(n):return number(n)+' Ryo'

class MissionPayments:
    def __init__(self,economy):
        self.eco=economy;self.ctx=economy.ctx;self.s=economy.store;self.bot=economy.bot
        self.install()
    def rank_label(self,key):
        config=getattr(self.ctx['MISSIONS'],'config',{})
        return config.get('ranks',{}).get(key,{}).get('label',RANKS.get(key,(key,))[0])
    def card(self,report,village,details,recipients=None,tx=None,reversed=False):
        people=details['identities'];n=details['amount'];mode=details['mode']
        total=n*len(people) if mode=='participante' else n
        if recipients is None:
            from worldx_v7 import split_reward
            recipients=split_reward(n,people)
        title=''.join([format_fragment(t('📜 Ficha de pago · Misión #'), '', -1), format_fragment(report, '', -1)])
        e=discord.Embed(title=title,color=0x388E63 if tx else 0xD69A38,
                        description=(t('**PAGO REVERTIDO**') if reversed else ''.join([format_fragment(t('**PAGADA · Comprobante #'), '', -1), format_fragment(tx, '', -1), '**']) if tx else t('**PENDIENTE DE CONFIRMACIÓN · todavía no se han transferido Ryo**')))
        e.add_field(name=t('Aldea pagadora'),value=clean(self.s.village(self.ctx['GUILD'],village)['name']))
        e.add_field(name='Recompensa',value=''.join([format_fragment(money(n), '', -1), ' · ', format_fragment(mode, '', -1), format_fragment(t('\nTotal: **'), '', -1), format_fragment(money(total), '', -1), '**']))
        e.add_field(name=t('Misión / fecha declarada'),value=''.join([format_fragment(t('Rango '), '', -1), format_fragment(details.get('mission_rank', '?'), '', -1), '\n', format_fragment(clean(details.get('completed_at') or t('No indicada')), '', -1), ' · Colombia']))
        for p in people:
            lines=[''.join([format_fragment(t('**Nombre del personaje:** '), '', -1), format_fragment(clean(p['name']), '', -1)]),
                   ''.join([format_fragment(t('**Rango autorizado en la misión:** '), '', -1), format_fragment(clean(self.rank_label(p['rank'])), '', -1)]),
                   ''.join([format_fragment(t('**Edad del personaje:** '), '', -1), format_fragment(clean(p['age']) or t('No registrada'), '', -1)]),
                   f"**Discord username:** {clean(p['discord_username'])}",
                   ''.join([format_fragment(t('**Displayname del registro:** '), '', -1), format_fragment(clean(p['displayname']), '', -1)]),
                   ''.join([format_fragment(t('**ID Discord:** '), '', -1), format_fragment(p['user'], '', -1), format_fragment(t(' · ficha #'), '', -1), format_fragment(p['registration'], '', -1)])]
            if p.get('roblox_username'):lines.append("**Username Roblox:** "+clean(p['roblox_username']))
            if p.get('discord_display'):lines.append(''.join([format_fragment(t('**Nombre visible en Discord:** '), '', -1), format_fragment(clean(p['discord_display']), '', -1)]))
            if recipients is not None:lines.append(t('**Ryo recibidos:** ')+money(recipients[str(p['user'])]))
            e.add_field(name=(t('🥷 Shinobi seleccionado') if p['user']==details.get('selected') else t('🥷 Integrante de squad')),value='\n'.join(lines)[:1024],inline=False)
        e.set_footer(text=t('Identidad tomada de la ficha aprobada · Imagen revisada manualmente · Un pago por reporte'))
        return e
    def receipt_card(self,receipt):
        return self.card(receipt['report'],receipt['village'],receipt['details'],receipt['recipients'],receipt['tx'],bool(receipt['reversed']))
    async def start(self,i,report,village,n,mode,selected=None):
        self.eco.kage(i,village);self.s.live(i.guild_id);mode='squad'
        row=self.ctx['MISSIONS'].store.get(report)
        if not row or row['guild']!=i.guild_id:raise ValueError(t('Reporte inexistente.'))
        if row['kind']!='mission' or row['state']!='approved':raise ValueError(t('Primero debe aprobarse el reporte de la misión en /misiones. La captura sola no demuestra que haya terminado.'))
        if self.s.db.execute('SELECT 1 FROM eco_mission_payments WHERE report=?',(report,)).fetchone():raise ValueError(t('Esta misión ya fue pagada. Consulta /pagos ver.'))
        users=sorted(x['user'] for x in row['participants'])
        selected=selected if selected is not None else row['owner']
        if selected not in users:raise ValueError(t('El shinobi seleccionado no figura entre los participantes aprobados de esta misión.'))
        await i.response.defer(ephemeral=True)
        roster={x['user']:x for x in row['data'].get('roster',[])};snapshots=[]
        for u in users:
            await self.eco.sync(i.guild_id,u);profile=self.s.player(i.guild_id,u)
            member=await i.guild.fetch_member(u);reg=self.ctx['STORE'].find(i.guild_id,u)
            snap=identity(reg,roster.get(u,{}).get('rank',profile['rank']),member.name,member.display_name)
            if not all(str(snap[k]).strip() for k in ('name','age','displayname','discord_username')):raise ValueError(''.join([format_fragment(t('La ficha de '), '', -1), format_fragment(u, '', -1), format_fragment(t(' tiene datos incompletos. Corrige y aprueba su registro antes del pago.'), '', -1)]))
            snapshots.append(snap)
        if not any(self.s.profile(i.guild_id,u)['village']==village for u in users):raise ValueError(t('La aldea pagadora debe tener al menos un participante acreditado en esta misión.'))
        details={'identities':snapshots,'selected':selected,'amount':n,'mode':mode,'mission_rank':row['data'].get('mission_rank'),'completed_at':row['data'].get('end','')}
        view=self.wizard(i.user.id,row,village,details)
        await self.show(i,view)
    async def show(self,i,view):
        e=self.card(view.row['id'],view.village,view.details)
        kwargs={'embed':e,'view':view}
        # Always show the complete approved evidence set, even when a supplementary image is uploaded.
        proofs=view.row['data'].get('proofs',[])
        extra=[]
        for index,proof in enumerate(proofs):
            extra.append(discord.File(safe_path(self.ctx['STORE'].root,proof['path']),filename=f'prueba-{index+1}'+safe_path(self.ctx['STORE'].root,proof['path']).suffix))
        file=None
        if view.blob:
            extension,_=validate_image(view.blob);filename='captura-mision'+extension
            file=discord.File(io.BytesIO(view.blob),filename=filename);e.set_image(url='attachment://'+filename);extra.insert(0,file)
        if len(extra)==1:kwargs['file']=extra[0]
        elif extra:kwargs['files']=extra
        try:await self.eco.reply(i,t(CAPTURE_GUIDE) if not view.blob else t('Revisa **todos los datos y la captura**. Solo confirma si la misión se completó y corresponde al reporte aprobado.'),**kwargs)
        finally:
            for f in extra:f.close()
    def wizard(self,actor,row,village,details,blob=None,source='upload'):
        system=self
        class PaymentView(self.ctx['SafeView']):
            def __init__(self):
                super().__init__(timeout=900);self.row=row;self.village=village;self.details=details
                self.blob=blob;self.source=source;self.done=False
                self.pay.disabled=not bool(blob)
                self.existing.disabled=not bool(row.get('proof'))
            def check(self,i):
                if i.user.id!=actor:raise ValueError(t('Esta ficha de pago pertenece a otro revisor. Abre la tuya con /pagos mision.'))
                system.eco.kage(i,village)
                if self.done:raise ValueError(t('Esta ficha ya fue resuelta.'))
                fresh=system.ctx['MISSIONS'].store.get(row['id'])
                if fresh['state']!='approved' or fresh['revision']!=row['revision']:raise ValueError(t('El reporte cambió. Abre nuevamente la ficha de pago.'))
                if system.s.db.execute('SELECT 1 FROM eco_mission_payments WHERE report=?',(row['id'],)).fetchone():raise ValueError(t('El reporte ya fue pagado. Usa /pagos ver.'))
            async def interaction_check(self,i):self.check(i);return True
            async def on_timeout(self):self.blob=None
            @discord.ui.button(label='Adjuntar captura',style=discord.ButtonStyle.primary)
            async def upload(self,i,b):
                self.check(i);parent=self
                class Capture(system.ctx['SafeModal']):
                    def __init__(self):
                        super().__init__(title=t('Captura completa de la misión'),timeout=900)
                        self.image=discord.ui.FileUpload(required=True,min_values=1,max_values=1)
                        self.add_item(discord.ui.Label(text=t('Pantalla completa de Open World'),description=t('Resultado y panel legibles; fecha y hora visibles. PNG/JPG/WEBP, hasta 8 MiB.'),component=self.image))
                    async def on_submit(modal,inter):
                        parent.check(inter)
                        if len(modal.image.values)!=1:raise ValueError(t('Adjunta exactamente una captura.'))
                        a=modal.image.values[0]
                        if not 0<a.size<=MAX_BYTES or a.content_type not in ('image/png','image/jpeg','image/webp') or not a.width or not a.height:raise ValueError(t('Adjunta una captura PNG/JPG/WEBP de hasta 8 MiB.'))
                        await inter.response.defer(ephemeral=True)
                        try:payload=await asyncio.wait_for(a.read(),timeout=90)
                        except TimeoutError:raise ValueError(t('La subida tardó demasiado. Inténtalo de nuevo.'))
                        validate_image(payload);parent.check(inter)
                        await system.show(inter,system.wizard(actor,row,village,details,payload,'upload'))
                await i.response.send_modal(Capture())
            @discord.ui.button(label='Usar captura del reporte',style=discord.ButtonStyle.secondary)
            async def existing(self,i,b):
                self.check(i);payload=file_blob(system.ctx['STORE'].root,row['proof'])
                await system.show(i,system.wizard(actor,row,village,details,payload,'report'))
            @discord.ui.button(label='Confirmar misión cumplida y pagar',style=discord.ButtonStyle.success)
            async def pay(self,i,b):
                self.check(i)
                if not self.blob:raise ValueError(t('Adjunta o selecciona la captura antes de confirmar.'))
                await i.response.defer(ephemeral=True)
                for p in details['identities']:await system.eco.sync(i.guild_id,p['user'])
                async with system.eco.lock:
                    self.check(i)
                    if not any(system.s.profile(i.guild_id,p['user'])['village']==village for p in details['identities']):raise ValueError(t('Cambió la afiliación de los participantes. Revisa la aldea pagadora.'))
                    extension,_=validate_image(self.blob);relative=f'capturas/pagos/{uuid.uuid4().hex}{extension}'
                    path=safe_path(system.ctx['STORE'].root,relative);path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_bytes(self.blob)
                    try:
                        receipt={'identities':details['identities'],'selected':details['selected'],'proof':relative,'source':self.source}
                        tx,recipients=system.s.pay_mission(i.guild_id,i.user.id,row['id'],village,details['amount'],details['mode'],row['revision'],receipt)
                    except BaseException:path.unlink(missing_ok=True);raise
                    self.done=True;self.blob=None
                    for child in self.children:child.disabled=True
                receipt=system.s.mission_receipt(i.guild_id,row['id']);e=system.receipt_card(receipt)
                await system.eco.reply(i,''.join([format_fragment(t('Pago #'), '', -1), format_fragment(tx, '', -1), format_fragment(t(' completado. Datos y captura guardados. Consulta /pagos ver.'), '', -1)]),embed=e)
            @discord.ui.button(label='Cancelar',style=discord.ButtonStyle.danger)
            async def cancel(self,i,b):
                self.check(i);self.done=True;self.blob=None
                for child in self.children:child.disabled=True
                await i.response.edit_message(content=t('Ficha de pago cancelada. No se movieron Ryo.'),embed=None,attachments=[],view=self)
        return PaymentView()
    async def view_receipt(self,i,report):
        self.eco.guild(i);receipt=self.s.mission_receipt(i.guild_id,report)
        if not receipt:
            old=self.s.rows('SELECT * FROM eco_mission_payments WHERE guild=? AND report=?',(i.guild_id,report))
            if old:raise ValueError(t('Este pago es anterior a v6.1 y no tiene ficha de captura archivada. Conserva su comprobante en /economia historial; no vuelvas a pagarlo.'))
            raise ValueError(t('No hay un pago registrado para ese reporte.'))
        members={x['user'] for x in receipt['details']['identities']}
        if i.user.id not in members and not self.ctx['staff'](i):self.eco.kage(i,receipt['village'])
        e=self.receipt_card(receipt);path=safe_path(self.ctx['STORE'].root,receipt['proof'])
        if not path.is_file():raise ValueError(t('No se encuentra la captura archivada. Revisa el respaldo del bot; el pago sigue registrado.'))
        filename='captura-mision'+path.suffix;e.set_image(url='attachment://'+filename)
        file=discord.File(path,filename=filename)
        try:await self.eco.reply(i,embed=e,file=file)
        finally:file.close()
    def install(self):
        group=app_commands.Group(name='pagos',description='Fichas de pago de misión con identidad y captura')
        @group.command(name='mision',description='Crear ficha de pago de un shinobi con datos del registro y captura')
        async def mission(i:discord.Interaction,shinobi:discord.Member,reporte:int,aldea:int,importe:app_commands.Range[int,1,1000000000000],modalidad:Literal['squad']='squad'):
            await self.start(i,reporte,aldea,importe,modalidad,shinobi.id)
        @mission.autocomplete('aldea')
        async def villages(i:discord.Interaction,current:str):
            return [app_commands.Choice(name=f"#{v['id']} {v['name']}"[:100],value=v['id']) for v in self.s.villages(i.guild_id) if str(current).lower() in v['name'].lower()][:25]
        @group.command(name='ver',description='Consultar un pago guardado con los datos y la captura de su misión')
        async def receipt(i:discord.Interaction,reporte:int):await self.view_receipt(i,reporte)
        self.bot.tree.add_command(group)
