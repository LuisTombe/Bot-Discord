"""Perfil por formulario y evidencia de compra desde botones, sin archivos JSON."""
import io
import json
import discord
from discord import app_commands
from evidence import safe_path
from wx_market.ui import SafeView, ryo
from wx_market.domain import satisfies, dump, purchase_manual_requirement, stat_or_level_requirement, satisfies_rank_policy, rank_label, RANK_ALLOWED, is_medical_item
from simple_flow_ui import SafeModal, upload_files
from simple_flow_domain import SHINOBI_RANKS
from simple_transactions import atomic

class ProfileUI:
    def __init__(self,ui):
        self.ui=ui;self.market=ui.market;self.m=self.market.store
        self.original_detail=self.market.detail;self.market.detail=self.detail
        # Los accesos antiguos de tienda/inventario ya no llevan a una tienda vacía.
        async def shop(i,*args,**kwargs):await self.market.shop(i)
        async def inventory(i,page=1,*args,**kwargs):await self.market.listing(i,'inventory',page)
        ui.eco.shop=shop;ui.eco.inventory=inventory
        self.install()

    async def show(self,i):
        self.market.guild(i);await i.response.defer(ephemeral=True);await self.market.refresh(i.guild_id,i.user.id)
        seed=self.ui.flow.seed_profile(i.guild_id,i.user.id)
        detected=self.ui.flow.detect_member_shinobi_rank(i.guild_id,i.user)
        if detected:seed['shinobi_rank']=detected
        pending=self.m.one("SELECT * FROM simple_profile_requests WHERE guild=? AND user=? ORDER BY id DESC LIMIT 1",(i.guild_id,i.user.id))
        try:
            p=self.m.profile(i.guild_id,i.user.id);status='✅ Perfil verificado y autorizado'
        except ValueError as e:p=seed;status=str(e)
        rkey=p.get('shinobi_rank',seed.get('shinobi_rank','genin'))
        rlabel=SHINOBI_RANKS.get(rkey,(rkey.title(),1))[0]
        text=(f'**{status}**\n'
              f'Rango Shinobi: **{rlabel}** · Aldea: **{p.get("village","Por verificar")}**\n'
              f'Bloodlines (ficha): '+(', '.join(p.get('bloodlines',[])) or 'Ninguno')+'\n'
              f'Elementos (ficha): '+(', '.join(p.get('elements',[])) or 'Ninguno'))
        if pending:text+=f'\nÚltima solicitud: **{pending["state"]}**.'
        view=SafeView(timeout=600)
        btn=discord.ui.Button(label='Autorizar / Actualizar mi perfil (2 capturas)',emoji='📷',style=discord.ButtonStyle.success,row=0)
        async def open_update(inter):
            if inter.user.id!=i.user.id:raise ValueError('Abre tu propio perfil.')
            if pending and pending['state']=='pending':raise ValueError('Espera a que el staff revise tu solicitud pendiente.')
            await inter.response.send_modal(ProfileModal(self,seed))
        btn.callback=open_update;view.add_item(btn)
        await i.followup.send(text[:1900]+'\n\nPulsa el botón verde para subir **solo 2 capturas**:\n1. Tus **roles en Discord**.\n2. Tus **stats en Shindo Life 2 (menú M)**.',view=view,ephemeral=True)

    def profile_review(self,r):
        ui=self.ui;system=self;view=SafeView(timeout=None)
        p_data=json.loads(r['data']) if isinstance(r.get('data'),str) else {}
        selected_rank=[p_data.get('shinobi_rank','genin')]
        rank_select=discord.ui.Select(
            placeholder='Corregir Rango Shinobi (solo si está mal)',
            custom_id=f'wx:simple:profrank:{r["id"]}',
            row=0,
            options=[discord.SelectOption(label=label,value=key,default=key==selected_rank[0]) for key,(label,_) in SHINOBI_RANKS.items()]
        )
        async def on_rank(i):
            system.market.staff(i)
            await i.response.defer(ephemeral=True)
            selected_rank[0]=rank_select.values[0]
            with ui.ms.db:
                fresh_r=system.m.one('SELECT data FROM simple_profile_requests WHERE id=?',(r['id'],))
                if fresh_r:
                    d=json.loads(fresh_r['data']);d['shinobi_rank']=selected_rank[0]
                    ui.ms.db.execute('UPDATE simple_profile_requests SET data=? WHERE id=?',(dump(d),r['id']))
            await i.followup.send(f'Rango Shinobi actualizado a **{SHINOBI_RANKS[selected_rank[0]][0]}**. Ahora pulsa **Aprobar** o **Rechazar**.',ephemeral=True)
        rank_select.callback=on_rank;view.add_item(rank_select)
        for label,approved in [('Aprobar',True),('Rechazar',False)]:
            b=discord.ui.Button(label=label,emoji='✅' if approved else '❌',custom_id=f'wx:simple:profile:{r["id"]}:{int(approved)}',row=1,style=discord.ButtonStyle.success if approved else discord.ButtonStyle.danger)
            async def cb(i,approved=approved):
                system.market.staff(i)
                if i.channel_id!=system.market.channel_id('purchase'):raise ValueError('Usa el canal de revisión de compras.')
                await i.response.defer(ephemeral=True)
                fresh_r=system.m.one('SELECT data FROM simple_profile_requests WHERE id=?',(r['id'],))
                chosen=json.loads(fresh_r['data']).get('shinobi_rank',selected_rank[0]) if fresh_r else selected_rank[0]
                await system.market.refresh(i.guild_id,r['user'])
                note='Aprobado por Staff' if approved else 'Rechazado por Staff'
                ui.flow.review_profile(i.guild_id,i.user.id,r['id'],approved,note,shinobi_rank=chosen,allow_self=(i.user.id==ui.ctx.get('BOT_OWNER')))
                rname=SHINOBI_RANKS.get(chosen,(chosen,1))[0]
                try:
                    if i.message:await i.message.edit(view=None)
                except discord.HTTPException:pass
                await i.followup.send(f'✅ Perfil aprobado con rango **{rname}**.' if approved else '❌ Perfil rechazado. El jugador fue notificado.',ephemeral=True)
                try:
                    member=await ui.bot.fetch_user(r['user']);await member.send(f'World X · tu perfil fue **{"aprobado ("+rname+")" if approved else "rechazado"}**.')
                except discord.HTTPException:pass
            b.callback=cb;view.add_item(b)
        return view

    def restore(self):
        for r in self.m.rows("SELECT * FROM simple_profile_requests WHERE state='pending' AND message IS NOT NULL"):
            self.ui.bot.add_view(self.profile_review(r),message_id=r['message'])

    async def deliver(self):
        for r in self.m.rows("SELECT * FROM simple_profile_requests WHERE guild=? AND state='pending' AND message IS NULL ORDER BY id LIMIT 5",(self.ui.ctx['GUILD'],)):
            try:
                ch=self.market.channel(r['guild'],'purchase');p=json.loads(r['data'])
                rkey=p.get('shinobi_rank','genin');rname=SHINOBI_RANKS.get(rkey,(rkey,1))[0]
                text=f'Jugador <@{r["user"]}>\nRango Shinobi detectado: **{rname}** (`{rkey}`)\nAldea: {p.get("village","?")} · Raza: {p.get("race","humano")}\nBloodlines (ficha): '+ (', '.join(p.get('bloodlines',[])) or 'Ninguno')+'\nElementos (ficha): '+ (', '.join(p.get('elements',[])) or 'Ninguno')+'\n*(Verifica en las capturas el menú de stats y los roles de Discord; elige su Rango Shinobi abajo y aprueba o rechaza)*'
                em=discord.Embed(title=f'Verificar perfil #{r["id"]}',description=text[:3900],color=0xC4A76B)
                files=[discord.File(safe_path(self.ui.ms.root,p['path']),filename=f'perfil-{n}.png') for n,p in enumerate(json.loads(r['proof']))]
                try:msg=await ch.send(embed=em,files=files,view=self.profile_review(r),allowed_mentions=discord.AllowedMentions.none())
                finally:
                    for f in files:f.close()
                with self.ui.ms.db:self.ui.ms.db.execute('UPDATE simple_profile_requests SET channel=?,message=? WHERE id=?',(ch.id,msg.id,r['id']))
            except (discord.HTTPException,ValueError,OSError):
                import logging
                logging.getLogger('worldx.simple').exception('Entrega de verificación de perfil')

    async def detail(self,i,ident):
        self.market.guild(i);await i.response.defer(ephemeral=True);await self.market.refresh(i.guild_id,i.user.id)
        detected_rank,has_med=self.ui.flow.sync_member_rank(i.guild_id,i.user)
        it=self.m.item(ident);view=SafeView(timeout=600)
        em=discord.Embed(title=(it['emoji']+' '+it['name'])[:250],description=it.get('notes',it['source'])[:2000],color=0xC4A76B)
        error=None;q=None
        try:p=self.m.profile(i.guild_id,i.user.id)
        except ValueError as e:p={};error=str(e)
        lines=[]
        for r in it['requirements']:
            if stat_or_level_requirement(r):continue
            c=r['condition']
            if c=={'field':'tags','op':'all','value':['unlock:'+it['id']]}:
                lines.append('📷 Desbloqueo de la técnica · staff revisa al comprar')
            elif r['policy']=='x3' and c.get('field')=='rank_level':
                ok=satisfies_rank_policy(p,it,r) if p else (has_med if is_medical_item(it) else (it['rank'] in RANK_ALLOWED.get(detected_rank,())))
                lines.append(('✅ ' if ok else '⚠️ ')+f'Rango requerido: **{it.get("rank_label") or rank_label(it["rank"])}** (según tu rango Shinobi Discord)'+('' if ok else ' · recargo ×3'))
            else:
                ok=satisfies(p,c) if p else False
                lines.append(('✅ ' if ok else '❌ ')+r['label']+(' · obligatorio' if r['policy']=='hard' else ' · ×3 si falta'))
        em.add_field(name='Requisitos',value=('\n'.join(lines) or 'Solo perfil autorizado y rango Shinobi')[:1024],inline=False)
        if not error:
            try:q=self.m.quote(i.guild_id,i.user.id,it);self.m.check_unique(i.guild_id,i.user.id,it)
            except ValueError as e:error=str(e)
        available=self.m.available(i.guild_id,i.user.id)
        price_x3=int(it.get('price_x3') or int(it['price'])*3)
        rank_ok=(has_med if is_medical_item(it) else (it['rank'] in RANK_ALLOWED.get(detected_rank,())))
        detected_label=SHINOBI_RANKS.get(detected_rank,(detected_rank.title(),1))[0]
        req_label=it.get('rank_label') or rank_label(it['rank'])
        em.add_field(name='Dinero disponible',value=ryo(available));em.add_field(name='Reservado',value=ryo(self.m.reserved(i.guild_id,i.user.id)))
        em.add_field(name='Precio normal / ×3',value=f'Normal: **{ryo(q["price"] if q else it["price"])}**\nSin rango (×3): **{ryo(price_x3)}**')
        if not rank_ok:
            extra_msg='Requiere rol **Medical Corps**.' if is_medical_item(it) else f'Tu rango actual en Discord (**{detected_label}**) no cumple con **{req_label}**.'
            em.add_field(
                name='⚠️ Aviso de Rango Shinobi',
                value=f'{extra_msg}\nPuedes comprarlo sin rango pagando **×3 ({ryo(price_x3)})** con el botón **Comprar sin rango (×3)**.',
                inline=False
            )
        if q and available<q['price']:error='Saldo disponible insuficiente. No se puede solicitar la compra normal.'
        if error:em.add_field(name='Antes de comprar (normal)',value=error[:1024],inline=False)
        if it.get('image_url'):em.set_thumbnail(url=it['image_url'])
        b=discord.ui.Button(label='Adjuntar rango y solicitar compra',style=discord.ButtonStyle.success,disabled=bool(error),row=0)
        async def buy(inter):
            if inter.user.id!=i.user.id:raise ValueError('Abre tu propia compra.')
            self.market.guild(inter);await inter.response.send_modal(PurchaseModal(self,ident,q['price']))
        b.callback=buy;view.add_item(b)
        already_owned=bool(it.get('unique') and self.m.owns(i.guild_id,i.user.id,it['id']))
        bx3=discord.ui.Button(label=f'Comprar sin rango (×3 · {ryo(price_x3)})'[:80],emoji='💸',style=discord.ButtonStyle.primary,disabled=already_owned,row=0)
        async def buy_x3(inter):
            if inter.user.id!=i.user.id:raise ValueError('Abre tu propia compra.')
            self.market.guild(inter)
            await inter.response.defer(ephemeral=True)
            res=self.m.purchase_x3_direct(inter.guild_id,inter.user.id,ident,str(inter.id))
            await inter.followup.send(
                f'✅ **Compra sin rango (×3) autorizada y entregada.**\n'
                f'• Artículo: **{it["name"]}**\n'
                f'• Pagado: **{ryo(res["price"])}**\n'
                f'• Ya está disponible en tu **Inventario**.',
                ephemeral=True
            )
        bx3.callback=buy_x3;view.add_item(bx3)
        pbutton=discord.ui.Button(label='Ver / actualizar mi perfil',row=1)
        async def profile(inter):
            if inter.user.id!=i.user.id:raise ValueError('Abre tu propio perfil.')
            await self.show(inter)
        pbutton.callback=profile;view.add_item(pbutton)
        await i.followup.send(embed=em,view=view,ephemeral=True)

    async def orders(self,i,page=0):
        self.market.guild(i)
        rows=self.m.rows('SELECT * FROM market_requests WHERE guild=? AND (actor=? OR target=?) ORDER BY id DESC LIMIT 8 OFFSET ?',(i.guild_id,i.user.id,i.user.id,page*8))
        view=SafeView(timeout=600)
        if rows:
            select=discord.ui.Select(placeholder='Abrir solicitud',options=[discord.SelectOption(label=f'#{r["id"]} · {r["kind"]} · {r["state"]}',value=str(r['id'])) for r in rows])
            async def selected(inter):
                if inter.user.id!=i.user.id:raise ValueError('Abre tus compras.')
                r=self.m.req(inter.guild_id,int(select.values[0]));v=SafeView(timeout=600)
                if r['state'] in ('pending','offered'):
                    b=discord.ui.Button(label='Añadir / corregir capturas',style=discord.ButtonStyle.primary)
                    async def proof(ii):
                        if ii.user.id!=i.user.id:raise ValueError('Solicitud ajena.')
                        await ii.response.send_modal(PurchaseModal(self,r['item'],r['price'],r['id']))
                    b.callback=proof;v.add_item(b)
                    b=discord.ui.Button(label='Cancelar solicitud',style=discord.ButtonStyle.danger)
                    async def cancel(ii):
                        self.market.guild(ii);self.m.cancel(ii.guild_id,ii.user.id,r['id']);await ii.response.send_message('Cancelada. Dinero liberado.',ephemeral=True)
                    b.callback=cancel;v.add_item(b)
                await inter.response.send_message(embed=self.market.request_embed(r),view=v,ephemeral=True)
            select.callback=selected;view.add_item(select)
        for label,delta in [('Anterior',-1),('Siguiente',1)]:
            b=discord.ui.Button(label=label,disabled=page==0 and delta<0 or len(rows)<8 and delta>0)
            async def turn(inter,delta=delta):
                if inter.user.id!=i.user.id:raise ValueError('Abre tus compras.')
                await self.orders(inter,page+delta)
            b.callback=turn;view.add_item(b)
        await self.market.reply(i,content='Tus solicitudes. Abre una para ver su estado, corregir pruebas o cancelar.',view=view)

    def install(self):
        group=self.ui.bot.tree.get_command('mercado')
        group.remove_command('ver_perfil')
        group.remove_command('solicitudes')
        @group.command(name='solicitudes',description='Abrir tus compras y corregir evidencia con botones')
        async def orders(i:discord.Interaction):await self.orders(i)
        @group.command(name='ver_perfil',description='Consultar o actualizar tu perfil mediante formulario')
        async def show(i:discord.Interaction):await self.show(i)
        # Agrega corrección al panel de staff existente; no cambia dinero ni caducidad.
        b=discord.ui.Button(label='Pedir corrección',custom_id='wx:simple:purchase:correct')
        async def correct(i):
            self.market.staff(i);r=self.market.view.request(i)
            if i.channel_id!=self.market.channel_id(r['kind']):raise ValueError('Usa el canal de revisión.')
            system=self
            class Correction(SafeModal,title='Pedir otra evidencia'):
                reason=discord.ui.TextInput(label='Qué debe corregir',style=discord.TextStyle.paragraph,max_length=500)
                async def on_submit(modal,inter):
                    system.market.staff(inter)
                    if inter.channel_id!=system.market.channel_id(r['kind']):raise ValueError('Canal incorrecto.')
                    with system.m.transaction():
                        fresh=system.m.req(inter.guild_id,r['id'])
                        if fresh['state']!='pending' or fresh['expires']<=system.m.now():raise ValueError('Solicitud cerrada o vencida.')
                        if inter.user.id in (fresh['actor'],fresh['target']):raise ValueError('Otro supervisor debe revisar tu compra.')
                        system.m.db.execute('UPDATE market_requests SET reason=?,dirty=1 WHERE id=?',('CORREGIR: '+str(modal.reason),r['id']))
                        system.m.audit(inter.guild_id,inter.user.id,'correction_requested',{'reason':str(modal.reason)},r['id'])
                    await inter.response.send_message('Corrección solicitada; reserva y vencimiento originales conservados.',ephemeral=True)
                    try:
                        user=await system.ui.bot.fetch_user(r['actor']);await user.send('World X · compra #'+str(r['id'])+': '+str(modal.reason)+'\nPanel → Mis compras → Añadir / corregir capturas.')
                    except discord.HTTPException:pass
            await i.response.send_modal(Correction())
        b.callback=correct;self.market.view.add_item(b)

class ProfileModal(SafeModal,title='Autorizar perfil (2 capturas)'):
    def __init__(self,system,seed):
        super().__init__();self.system=system;self.seed=seed or {}
        self.proofs=discord.ui.FileUpload(min_values=2,max_values=4,required=True)
        self.add_item(discord.ui.Label(text='2 capturas: Roles Discord + Stats Shindo',description='Sube 1) captura de tus roles en Discord y 2) captura de tus stats en Shindo Life 2 (menú M).',component=self.proofs))
    async def on_submit(self,i):
        system=self.system;system.market.guild(i);await i.response.defer(ephemeral=True)
        await system.market.refresh(i.guild_id,i.user.id)
        seed=system.ui.flow.seed_profile(i.guild_id,i.user.id)
        detected=system.ui.flow.detect_member_shinobi_rank(i.guild_id,i.user)
        data={**seed,'shinobi_rank':detected or seed.get('shinobi_rank','genin')}
        proofs=await upload_files(system.ui,self.proofs.values)
        try:ident=system.ui.flow.propose_profile(i.guild_id,i.user.id,data,proofs)
        except BaseException:
            for p in proofs:safe_path(system.ui.ms.root,p['path']).unlink(missing_ok=True)
            raise
        await i.followup.send(f'✅ Perfil #{ident} enviado a revisión con tus 2 capturas. Cuando el staff lo apruebe ya podrás comprar en la tienda.',ephemeral=True)

class PurchaseModal(SafeModal,title='Evidencia de compra'):
    def __init__(self,system,ident,price,rid=None):
        super().__init__();self.system=system;self.ident=ident;self.price=price;self.rid=rid
        self.proofs=discord.ui.FileUpload(min_values=1,max_values=4,required=True)
        self.add_item(discord.ui.Label(text='Captura del rango Shinobi en Discord',description='Adjunta rango Discord y pruebas del juego: desbloqueo, nivel del elemento y requisitos.',component=self.proofs))
    async def on_submit(self,i):
        system=self.system;system.market.guild(i);await i.response.defer(ephemeral=True)
        await system.market.refresh(i.guild_id,i.user.id)
        if self.rid:
            r=system.m.req(i.guild_id,self.rid)
            if i.user.id not in (r['actor'],r['target']) or r['state'] not in ('pending','offered'):raise ValueError('Solicitud cerrada o ajena.')
            kind=r['kind']
        else:
            q=system.m.quote(i.guild_id,i.user.id,system.m.item(self.ident))
            if q['price']!=self.price:raise ValueError('El precio cambió. Abre de nuevo el artículo antes de confirmar.')
            if system.m.available(i.guild_id,i.user.id)<self.price:raise ValueError('No tienes saldo disponible suficiente.')
            kind='purchase'
        channel=system.market.channel(i.guild_id,kind)
        proofs=await upload_files(system.ui,self.proofs.values)
        files=[discord.File(safe_path(system.ui.ms.root,p['path']),filename='prueba-'+str(n)+Path(p['path']).suffix) for n,p in enumerate(proofs)]
        msg=None;rid=self.rid
        try:
            msg=await channel.send(content=f'Pruebas de compra de <@{i.user.id}> · revisión de rango Discord obligatoria.',files=files,allowed_mentions=discord.AllowedMentions.none())
            if not rid:
                fresh=system.m.quote(i.guild_id,i.user.id,system.m.item(self.ident))
                if fresh['price']!=self.price:raise ValueError('El precio cambió durante la subida. Abre de nuevo el artículo.')
                rid=system.m.purchase(i.guild_id,i.user.id,self.ident,str(i.id))
            for a in msg.attachments:system.m.evidence(i.guild_id,i.user.id,rid,a.url,msg.jump_url)
            with system.m.transaction():
                system.m.db.execute("UPDATE market_requests SET reason='',dirty=1 WHERE id=? AND state IN ('pending','offered')",(rid,))
                system.m.audit(i.guild_id,i.user.id,'rank_evidence_uploaded',{'roles':[r.id for r in i.user.roles],'message':msg.jump_url},rid)
        except BaseException:
            if rid and not self.rid:
                try:system.m.cancel(i.guild_id,i.user.id,rid)
                except ValueError:pass
            raise
        finally:
            for f in files:f.close()
            for p in proofs:safe_path(system.ui.ms.root,p['path']).unlink(missing_ok=True)
        await i.followup.send(f'Solicitud #{rid} enviada. El dinero está reservado; se cobrará al aprobar. Consulta Panel → Mis compras.',ephemeral=True)

from pathlib import Path
