"""Discord UI for World X authorizations and one-mission reports."""
from i18n import bilingual, format_fragment, t
from content_i18n import mission_label
from panels_i18n import panel_embeds
from discord_i18n import public_output
import asyncio
import hashlib
import uuid
from pathlib import Path

import discord
from discord import app_commands
from evidence import MAX_BYTES, image_extension, safe_path
from missions_domain import MissionStore, load_config, EDITABLE, STATES, RANKS, formatted, stats_values

AUTH_GUIDE=(
    'Pulsa Comenzar misiones, completa nivel y CHI/NIN/TAI/HP y adjunta capturas completas '
    'con username Roblox, nivel, estadísticas y fecha/hora del computador o móvil. '
    'Cada jugador necesita autorización del staff antes de realizar misiones. '
    'No necesitas que el líder abra una sesión. Consulta el panel → Mis reportes.'
)
MISSION_GUIDE=(
    'Una misión por reporte. Necesitas autorización individual de hoy para empezar. '
    'Pantalla completa, username y fecha/hora del dispositivo obligatorios. '
    'Permanencia: temporizador a 3 segundos o menos. Ejecución: objetivo K/O/abatido en el suelo '
    'y jugador realizando la ejecución. Captura: objetivo capturado. Cargamento: a punto de destruirse. '
    'El staff revisa y usa Aprobar y pagar. El bot reparte el pago total según la regla de squad '
    'y suma una misión por integrante, sin duplicados. Las capturas se revisan manualmente.'
)


class MissionSystem:
    def __init__(self,bot,ctx):
        self.bot,self.ctx=bot,ctx
        path=ctx['BASE']/'misiones_config.json'
        if not path.exists():
            # New installs use defaults; upgrades preserve the server's mission settings.
            path.write_bytes((ctx['BASE']/'misiones_config.ejemplo.json').read_bytes())
        self.config=load_config(path)
        self.store=MissionStore(ctx['STORE'],self.config)
        self.install()

    def guild(self,i):
        if i.guild_id!=self.ctx['GUILD'] or not isinstance(i.user,discord.Member):raise ValueError(t('Usa el servidor configurado.'))
    def staff(self,i,admin=False):
        self.guild(i)
        if not (i.user.guild_permissions.manage_guild if admin else self.ctx['staff'](i)):raise ValueError(t('Necesitas Gestionar servidor.') if admin else t('Solo el staff puede revisar reportes.'))
    def leader(self,i):
        self.guild(i)
        leader=self.store.settings(i.guild_id).get('leader',i.guild.owner_id)
        if i.user.id!=leader:raise ValueError(t('Solo el líder supremo configurado puede abrir o cerrar sesiones de Open World.'))
    def player(self,i):
        self.guild(i)
        if self.ctx['is_demo']() and not self.ctx['staff'](i):raise ValueError(t('El bot está en pruebas privadas del staff.'))
        self.store.registered(i.guild_id,i.user.id)
    def get(self,i,ident,owner=False):
        self.guild(i);row=self.store.get(ident)
        if not row or row['guild']!=i.guild_id or row['state']=='deleted':raise ValueError(t('Reporte no encontrado o eliminado.'))
        if owner and row['owner']!=i.user.id:raise ValueError(t('Solo el autor puede editar este reporte.'))
        return row
    def channel_id(self,kind):
        settings=self.store.settings(self.ctx['GUILD'])
        return settings.get('auth_channel' if kind=='authorization' else 'mission_channel',self.ctx['REVIEW'])
    def clean(self,value,limit=900):
        return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))[:limit]
    def card(self,row):
        d=row['data'];s=self.store.session(row['session']) if row['session'] else {'label':t('Encargo azul fuera de Open World'),'closed':None}
        title=t('Autorización') if row['kind']=='authorization' else t('Misión')
        e=discord.Embed(title=f"📋 {title} #{row['id']}",color=0x348F79,description=''.join(['**', format_fragment(t(STATES[row['state']]), '', -1), format_fragment(t('** · Sesión #'), '', -1), format_fragment(row['session'], '', -1), format_fragment(t('\nAutor Discord: '), '', -1), format_fragment(row['owner'], '', -1)]))
        current_reg=self.ctx['STORE'].find(row['guild'],row['owner'])
        if current_reg and current_reg['data'].get('character_name'):
            e.add_field(name=t('Nombre del autor (registro actual)'),value=self.clean(current_reg['data']['character_name']),inline=False)
        e.add_field(name=t('Sesión'),value=self.clean(s['label']),inline=False)
        if row['kind']=='authorization':
            rank=self.config['ranks'].get(d.get('rank'),{})
            reg=self.ctx['STORE'].find(row['guild'],row['owner'])
            pairs=[('Username Roblox',reg['data'].get('roblox_name','') if reg else t('Sin registro')),(t('Rango / nivel'),''.join([format_fragment(rank.get('label', t('Pendiente')), '', -1), ' · ', format_fragment(d.get('level', '?'), '', -1)])),('CHI NIN TAI HP',d.get('stats',t('Pendiente'))),(t('Tope asignado'),rank.get('max_stats',t('Pendiente'))),(t('Aldea'),d.get('village',t('Pendiente'))),(t('Aliado / enemigo'),d.get('counterparty',t('Pendiente'))),(t('Actividad solicitada'),d.get('purpose',t('Pendiente'))),(t('Autorizado desde'),formatted(row['granted']) if row['granted'] is not None else t('Todavía no autorizado')),(t('Salida / cierre'),formatted(min(x for x in [row['ended'],s['closed']] if x is not None)) if row['ended'] is not None or s['closed'] is not None else t('En curso'))]
        else:
            pairs=[(t('Objetivo'),mission_label(self.config['types'].get(d.get('type'),{'label':t('Pendiente')}))),(t('Enemigo más fuerte'),self.config['ranks'].get(d.get('enemy'),{}).get('label','Sin enemigo' if d.get('enemy')=='none' else t('Pendiente'))),(t('Rango de misión'),d.get('mission_rank',t('Se calcula al enviar'))),(t('Inicio → fin (Colombia)'),f"{d.get('start','?')} → {d.get('end','?')}"),('Squad',d.get('squad') or t('Individual')),(t('Enemigos / objetivos'),d.get('opponents',t('Pendiente'))),(t('Descripción / resultado'),d.get('notes') or t('Sin observaciones'))]
            roster=d.get('roster') or [{'user':u} for u in d.get('members',[row['owner']])]
            pairs.append((t('Participantes'),'\n'.join(''.join([format_fragment(x.get('username', ''), '', -1), ' · Discord ', format_fragment(x['user'], '', -1), format_fragment(t(' · autorización #'), '', -1), format_fragment(x.get('authorization', '?'), '', -1), ' · ', format_fragment(x.get('rank', ''), '', -1), format_fragment(t(' · Nv '), '', -1), format_fragment(x.get('level', '?'), '', -1), ' · stats ', format_fragment(x.get('stats', '?'), '', -1)]) for x in roster)))
        if row['kind']=='mission' and hasattr(self,'review_ui'):
            e.add_field(name='Regla de evidencia',value=self.clean(self.review_ui.policy.rule(row)['evidence']),inline=False)
        for name,value in pairs:e.add_field(name=name,value=self.clean(value) or '—',inline=False)
        if row['reason']:e.add_field(name=t('Respuesta del staff'),value=self.clean(row['reason']),inline=False)
        e.set_footer(text=''.join([format_fragment(t('Revisión '), '', -1), format_fragment(row['revision'], '', -1), format_fragment(t(' · Evidencia revisada manualmente · Hora Colombia UTC−5'), '', -1)]))
        return e

    @public_output('destination')
    async def file_send(self,destination,row,view=None,message=None):
        e=self.card(row);files=[]
        try:
            if row['proof']:
                proofs=row['data'].get('proofs') or [{'path':row['proof']}]
                for index,proof in enumerate(proofs[:10]):
                    path=safe_path(self.store.root,proof['path']);name=f'reporte-{index+1}'+path.suffix
                    files.append(discord.File(path,filename=name))
                    if index==0:e.set_image(url='attachment://'+name)
            if message:return await message.edit(embed=e,attachments=files,view=view)
            return await destination.send(embed=e,files=files,view=view)
        finally:
            for f in files:f.close()

    async def deliver(self):
        async with self.ctx['OPERATIONS']:
            pending=self.store.db.execute("SELECT * FROM mission_reports WHERE guild=? AND state!='draft' AND (submitted IS NOT NULL OR review_message IS NOT NULL) AND sent_revision!=revision ORDER BY id LIMIT 20",(self.ctx['GUILD'],)).fetchall()
            for raw in pending:
                row=self.store.record(raw)
                if row['state']=='draft' or (not row['submitted'] and not row['review_message']):continue
                if row['sent_revision']==row['revision']:continue
                try:
                    channel_id=row['review_channel'] or self.channel_id(row['kind'])
                    channel=await self.bot.channel(channel_id)
                    self.review_ui.validate_channel(channel)
                    view=self.Review(row) if row['state']=='pending' else None
                    message=None
                    if row['review_message'] and not isinstance(channel,discord.ForumChannel):
                        try:message=await channel.fetch_message(row['review_message'])
                        except discord.NotFound:pass
                    # Deletion removes the screenshot from the staff message, preserving audit locally.
                    payload={**row,'proof':''} if row['state']=='deleted' else row
                    if isinstance(channel,discord.ForumChannel):
                        reg=self.ctx['STORE'].find(row['guild'],row['owner'])
                        uname=(reg['data'].get('discord_name') or reg['data'].get('character_name') or reg['data'].get('roblox_name') or str(row['owner'])) if reg else str(row['owner'])
                        thread_name=f"{uname} - misiones"[:100]
                        existing_thread=None
                        prev=self.store.db.execute("SELECT review_channel FROM mission_reports WHERE guild=? AND owner=? AND kind='mission' AND review_channel IS NOT NULL AND review_channel!=? ORDER BY id DESC LIMIT 1",(row['guild'],row['owner'],channel.id)).fetchone()
                        if prev:
                            try:
                                cand=await self.bot.channel(prev[0])
                                if isinstance(cand,discord.Thread) and cand.parent_id==channel.id:
                                    existing_thread=cand
                            except (discord.HTTPException,ValueError):
                                existing_thread=None
                        if existing_thread is not None:
                            if existing_thread.archived:
                                try:await existing_thread.edit(archived=False)
                                except discord.HTTPException:pass
                            result=await self.file_send(existing_thread,payload,view)
                            channel_id=existing_thread.id
                        else:
                            class ForumDestination:
                                async def send(inner,**kwargs):
                                    post=await channel.create_thread(name=thread_name,**kwargs)
                                    return post.message
                            result=await self.file_send(ForumDestination(),payload,view)
                            channel_id=result.channel.id
                    else:
                        result=await self.file_send(channel,payload,view,message)
                    self.store.delivered(row['id'],channel_id,result.id,row['revision'])
                except (discord.HTTPException,ValueError,OSError) as error:self.store.error(row['id'],f'{type(error).__name__}: {error}')

    def restore(self):
        self.bot.add_view(self.Panel('authorization'));self.bot.add_view(self.Panel('mission'))
        for raw in self.store.db.execute("SELECT * FROM mission_reports WHERE guild=? AND state='pending' AND review_message IS NOT NULL",(self.ctx['GUILD'],)):
            row=self.store.record(raw);self.bot.add_view(self.Review(row),message_id=row['review_message'])

    def install(self):
        system=self;ctx=self.ctx;store=self.store;config=self.config
        SafeView=ctx['SafeView'];SafeModal=ctx['SafeModal']

        async def check_draft(i,row):
            system.player(i);fresh=system.get(i,row['id'],True)
            if fresh['revision']!=row['revision'] or fresh['state'] not in EDITABLE:raise ValueError(t('Reporte desactualizado o bloqueado. Usa /misiones continuar.'))
            return fresh

        async def show(i,row):
            await i.response.send_message(embed=system.card(row),view=Wizard(row) if row['state'] in EDITABLE else None,ephemeral=True)

        class Details(SafeModal):
            def __init__(self,row):
                super().__init__(title=t('Datos de autorización') if row['kind']=='authorization' else t('Datos de la misión'),timeout=900)
                self.row=row;self.inputs={}
                if row['kind']=='authorization':
                    specs=[('level',t('Nivel de Shindo'),4,True),('stats',t('Puntos: CHI NIN TAI HP (ej. 900 900 900 900)'),40,True),('village',t('Tu aldea'),80,True),('counterparty',t('Aliado/enemigo: aldea y usernames'),180,True),('purpose',t('Actividad que solicitas realizar'),300,True)]
                else:
                    specs=[('start',t('Inicio Colombia: DD/MM/AAAA HH:MM'),16,True),('end',t('Fin Colombia: DD/MM/AAAA HH:MM'),16,True),('squad',t('Nombre de squad (vacío si individual)'),80,False),('opponents',t('Usernames enemigos/objetivos o Sin enemigo'),180,True),('notes',t('Objetivo, resultado y observaciones'),1500,True)]
                for key,label,limit,required in specs:
                    f=discord.ui.TextInput(label=label,max_length=limit,required=required,default=str(row['data'].get(key,'')))
                    self.inputs[key]=f;self.add_item(f)
            async def on_submit(self,i):
                await check_draft(i,self.row)
                d=dict(self.row['data']);d.update({k:str(f.value).strip() for k,f in self.inputs.items()})
                if self.row['kind']=='authorization':stats_values(d['stats'])
                row=store.edit(self.row['id'],self.row['revision'],i.user.id,d)
                await show(i,row)

        class Proof(SafeModal,title=t('Captura del reporte')):
            def __init__(self,row):
                super().__init__(timeout=900);self.row=row
                self.upload=discord.ui.FileUpload(required=True,min_values=1,max_values=4 if row['kind']=='mission' else 1)
                self.add_item(discord.ui.Label(text=t('Captura completa y legible'),description=t('PNG, JPG o WEBP. Máximo 8 MiB. Revisa las instrucciones anteriores.'),component=self.upload))
            async def on_submit(self,i):
                await check_draft(i,self.row)
                await i.response.defer(ephemeral=True)
                paths=[];proofs=[]
                try:
                    for a in self.upload.values:
                        if not 0<a.size<=MAX_BYTES:raise ValueError(t('Máximo 8 MiB por imagen.'))
                        store.evidence_policy.check_upload_budget(a.size)
                        blob=await asyncio.wait_for(a.read(),timeout=90)
                        from mission_evidence_policy import validate_screenshot
                        ext,digest=validate_screenshot(blob);store.evidence_policy.check_upload_budget(len(blob));relative=f'capturas/reportes/{uuid.uuid4().hex}{ext}'
                        path=safe_path(store.root,relative);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(blob);paths.append(path)
                        proofs.append({'path':relative,'digest':digest})
                    await check_draft(i,self.row)
                    row=store.edit(self.row['id'],self.row['revision'],i.user.id,data={**self.row['data'],'proofs':proofs},proof=proofs[0])
                except BaseException:
                    for path in paths:path.unlink(missing_ok=True)
                    raise
                await i.followup.send(t('Capturas guardadas. Revisa y envía al staff.'),embed=system.card(row),view=Wizard(row),ephemeral=True)

        class Guide(SafeView):
            def __init__(self,row):super().__init__(timeout=900);self.row=row
            @discord.ui.button(label='Subir captura',style=discord.ButtonStyle.primary)
            async def upload(self,i,button):
                await check_draft(i,self.row);await i.response.send_modal(Proof(self.row))

        class Wizard(SafeView):
            def __init__(self,row):
                super().__init__(timeout=900);self.row=row;d=row['data']
                selectors=[('rank',t('Rango de tu shinobi'),config['ranks'])] if row['kind']=='authorization' else [('type',t('Tipo de misión'),{k:v for k,v in config['types'].items() if k.startswith('azul_')==(row['session']==0)}),('enemy',t('Rango del enemigo más fuerte'),{'none':{'label':'Sin enemigo'},**config['ranks']})]
                for pos,(key,prompt,options) in enumerate(selectors):
                    select=discord.ui.Select(placeholder=prompt,row=pos,options=[discord.SelectOption(label=mission_label(v)[:100],value=k,default=d.get(key)==k) for k,v in list(options.items())[:25]])
                    async def choose(i,field=key,control=select):
                        await check_draft(i,self.row);values=dict(self.row['data']);values[field]=control.values[0]
                        fresh=store.edit(row['id'],row['revision'],i.user.id,values)
                        await i.response.edit_message(embed=system.card(fresh),view=Wizard(fresh))
                    select.callback=choose;self.add_item(select)
                if row['kind']=='mission' and row['session']!=0 and config['max_squad']>1:
                    users=discord.ui.UserSelect(placeholder=''.join([format_fragment(t('Compañeros (tú ya estás incluido). Máximo '), '', -1), format_fragment(config['max_squad'] - 1, '', -1)]),min_values=0,max_values=config['max_squad']-1,row=2)
                    async def selected(i):
                        await check_draft(i,self.row)
                        members=sorted({row['owner'],*(u.id for u in users.values)})
                        for selected_member in users.values:system.review_ui.shinobi(selected_member)
                        for user in members:store.registered(row['guild'],user)
                        fresh=store.edit(row['id'],row['revision'],i.user.id,{**row['data'],'members':members})
                        await i.response.edit_message(embed=system.card(fresh),view=Wizard(fresh))
                    users.callback=selected;self.add_item(users)
                for name,action,style in [(t('Datos y horario'),'details',discord.ButtonStyle.secondary),(t('Captura e instrucciones'),'proof',discord.ButtonStyle.secondary),(t('Enviar al staff'),'submit',discord.ButtonStyle.success)]:
                    button=discord.ui.Button(label=name,style=style,row=3)
                    async def clicked(i,choice=action):
                        await check_draft(i,self.row)
                        if choice=='details':await i.response.send_modal(Details(row))
                        elif choice=='proof':await i.response.send_message(t(AUTH_GUIDE) if row['kind']=='authorization' else system.review_ui.instructions(row),view=Guide(row),ephemeral=True)
                        else:
                            system.review_ui.policy.validate_files(row)
                            fresh=store.submit(row['id'],row['revision'],i.user.id)
                            await i.response.edit_message(content=t('Reporte enviado. Consulta /misiones ver para conocer la decisión.'),embed=system.card(fresh),view=None)
                    button.callback=clicked;self.add_item(button)
            async def interaction_check(self,i):
                try:await check_draft(i,self.row);return True
                except ValueError as e:await ctx['notify_error'](i,e);return False

        class Panel(SafeView):
            def __init__(self,kind):
                super().__init__(timeout=None)
                button=discord.ui.Button(label=bilingual('REPORTAR AUTORIZACIÓN') if kind=='authorization' else bilingual('REPORTAR MISIÓN'),style=discord.ButtonStyle.primary,custom_id='worldx:missions:panel:'+kind)
                async def start(i):
                    system.player(i);row=store.start(i.guild_id,i.user.id,kind)
                    await show(i,row)
                button.callback=start;self.add_item(button)

        class Decision(SafeModal,title=t('Resolver reporte')):
            reason=discord.ui.TextInput(label=t('Motivo / indicaciones'),style=discord.TextStyle.paragraph,max_length=300)
            def __init__(self,row,action):super().__init__(timeout=600);self.row,self.action=row,action
            async def on_submit(self,i):
                system.staff(i,admin=self.action=='delete')
                system.get(i,self.row['id'])
                if self.action=='delete':
                    await i.response.send_message(''.join([format_fragment(t('¿Eliminar el reporte #'), '', -1), format_fragment(self.row['id'], '', -1), format_fragment(t('? Se anularán sus créditos. Si es una autorización, se congelarán las misiones dependientes. El archivo de auditoría y la imagen se conservan localmente.'), '', -1)]),view=ConfirmDelete(self.row,str(self.reason.value),i.user.id),ephemeral=True)
                    return
                row=store.decision(self.row['id'],self.row['revision'],i.user.id,self.action,str(self.reason.value),ctx['is_demo']())
                await i.response.send_message(t('Decisión guardada. El mensaje de revisión se actualizará en unos segundos.'),embed=system.card(row),ephemeral=True)

        class ConfirmDelete(SafeView):
            def __init__(self,row,reason,actor):super().__init__(timeout=60);self.row,self.reason,self.actor=row,reason,actor;self.used=False
            @discord.ui.button(label='Confirmar eliminación',style=discord.ButtonStyle.danger)
            async def confirm(self,i,button):
                system.staff(i,True)
                if i.user.id!=self.actor or self.used:raise ValueError(t('Confirmación ajena o ya utilizada.'))
                system.get(i,self.row['id']);self.used=True
                store.decision(self.row['id'],self.row['revision'],i.user.id,'delete',self.reason)
                await i.response.edit_message(content=t('Reporte eliminado de los activos; créditos anulados.'),view=None);self.stop()
            @discord.ui.button(label='Cancelar',style=discord.ButtonStyle.secondary)
            async def cancel(self,i,button):
                if i.user.id!=self.actor:raise ValueError(t('Confirmación ajena.'))
                self.used=True;await i.response.edit_message(content=t('Cancelado.'),view=None);self.stop()

        class Review(SafeView):
            def __init__(self,row):
                super().__init__(timeout=None)
                verify=discord.ui.Button(label='Verificar pruebas',style=discord.ButtonStyle.primary,custom_id=f"worldx:missions:verify:{row['id']}:{row['revision']}")
                async def verify_click(i):await system.review_ui.show_review(i,row)
                verify.callback=verify_click;self.add_item(verify)
                for name,action in [('Autorizar' if row['kind']=='authorization' else t('Aprobar misión'),'approve'),(t('Corregir'),'correct'),(t('No autorizar / Rechazar'),'reject'),('Congelar','freeze')]:
                    button=discord.ui.Button(label=name,style=discord.ButtonStyle.success if action=='approve' else discord.ButtonStyle.secondary,custom_id=f"worldx:missions:review:{row['id']}:{row['revision']}:{action}")
                    async def clicked(i,choice=action):
                        system.staff(i);fresh=system.get(i,row['id'])
                        if fresh['revision']!=row['revision'] or fresh['state']!='pending':raise ValueError(t('Este reporte ya cambió. Consulta su versión actual.'))
                        await i.response.send_modal(Decision(row,choice))
                    button.callback=clicked;self.add_item(button)

        self.Panel,self.Review,self.Wizard,self.Details,self.Proof=Panel,Review,Wizard,Details,Proof
        group=app_commands.Group(name='misiones',description='Autorizaciones, sesiones y reportes de Open World',guild_only=True)

        @group.command(name='configurar',description='Elegir canales privados de revisión (Gestionar servidor)')
        async def configure(i:discord.Interaction,autorizaciones:discord.TextChannel,misiones:discord.TextChannel):
            system.staff(i,True)
            for channel in (autorizaciones,misiones):
                system.review_ui.validate_channel(channel)
                if channel.guild.id!=i.guild_id:raise ValueError(t('Los canales deben estar en este servidor.'))
                perms=channel.permissions_for(i.guild.me)
                if not all(getattr(perms,k) for k in ('view_channel','send_messages','embed_links','attach_files','read_message_history')):raise ValueError(t('Faltan permisos del bot en los canales de revisión.'))
            store.configure(i.guild_id,i.user.id,auth_channel=autorizaciones.id,mission_channel=misiones.id)
            await i.response.send_message(t('Canales guardados para reportes nuevos. Configura su visibilidad solo para el staff. Los reportes ya publicados se siguen actualizando en su canal original.'),ephemeral=True)

        @group.command(name='lider',description='Designar al líder supremo (solo propietario del servidor)')
        async def leader(i:discord.Interaction,usuario:discord.Member):
            system.guild(i)
            if i.user.id!=i.guild.owner_id:raise ValueError(t('Solo el propietario puede designar al líder supremo.'))
            if usuario.bot:raise ValueError(t('El líder debe ser una persona.'))
            store.configure(i.guild_id,i.user.id,leader=usuario.id)
            await i.response.send_message(''.join([format_fragment(t('Líder supremo configurado: ID '), '', -1), format_fragment(usuario.id, '', -1), format_fragment(t('. Solo esa cuenta podrá abrir/cerrar sesiones.'), '', -1)]),ephemeral=True)

        @group.command(name='abrir',description='Declarar abierta una sesión de Open World (solo líder supremo)')
        async def open_session(i:discord.Interaction,nombre:str):
            system.leader(i);s=store.open(i.guild_id,i.user.id,nombre)
            await i.response.send_message(''.join([format_fragment(t('Sesión #'), '', -1), format_fragment(s['id'], '', -1), format_fragment(t(' abierta desde '), '', -1), format_fragment(formatted(s['opened']), '', -1), format_fragment(t('. Abre el servidor de Roblox por separado: este comando no abre el juego ni distribuye su código privado.'), '', -1)]),ephemeral=True)

        @group.command(name='cerrar',description='Cerrar la sesión actual de Open World (solo líder supremo)')
        async def close_session(i:discord.Interaction):
            system.leader(i);s=store.close(i.guild_id,i.user.id)
            await i.response.send_message(''.join([format_fragment(t('Sesión #'), '', -1), format_fragment(s['id'], '', -1), format_fragment(t(' cerrada a las '), '', -1), format_fragment(formatted(s['closed']), '', -1), format_fragment(t('. Se pueden reportar después las misiones realizadas dentro de los horarios autorizados usando /misiones nuevo sesion:'), '', -1), format_fragment(s['id'], '', -1), '.']),ephemeral=True)

        @group.command(name='panel',description='Publicar uno de los paneles de reportes en este canal (administración)')
        @app_commands.choices(tipo=[app_commands.Choice(name='Autorización',value='authorization'),app_commands.Choice(name='Misión terminada',value='mission')])
        async def panel(i:discord.Interaction,tipo:app_commands.Choice[str]):
            system.staff(i,True)
            if not isinstance(i.channel,discord.TextChannel):raise ValueError(t('Elige un canal de texto.'))
            description=t(AUTH_GUIDE) if tipo.value=='authorization' else t(MISSION_GUIDE)+t('\n\n**Objetivos y rango base (sin enemigo superior):**\n')+'\n'.join(f"• {t['label']}: {t['base_rank']}" for t in config['types'].values())+t('\n\n**Escala del enemigo:** Sin enemigo/Academia D · Genin C · Chunin/Medical/Police B · Jounin/Anbu/Sensei A · Subkage/Sannin/Kage/Akatsuki S.')
            await i.response.defer(ephemeral=True)
            await i.channel.send(embeds=panel_embeds(tipo.value),view=Panel(tipo.value))
            await i.followup.send(t('Panel publicado.'),ephemeral=True)

        @group.command(name='nuevo',description='Crear reporte de misión; permite indicar una sesión ya cerrada')
        async def new(i:discord.Interaction,sesion:app_commands.Range[int,1]):
            system.player(i);await show(i,store.start(i.guild_id,i.user.id,'mission',sesion))

        @group.command(name='continuar',description='Continuar tu borrador o un reporte devuelto para corrección')
        async def resume(i:discord.Interaction,reporte:app_commands.Range[int,1]):
            system.player(i);row=system.get(i,reporte,True);await show(i,row)

        @group.command(name='salida',description='Marcar el final de tu actividad autorizada en una sesión')
        async def end(i:discord.Interaction,sesion:app_commands.Range[int,1]):
            system.player(i);row=store.end_authorization(i.guild_id,i.user.id,sesion)
            await i.response.send_message(t('Salida guardada. Puedes reportar después las misiones terminadas antes de esta hora.'),embed=system.card(row),ephemeral=True)

        @group.command(name='ver',description='Consultar un reporte propio; el staff puede ver todos')
        async def view(i:discord.Interaction,reporte:app_commands.Range[int,1]):
            row=system.get(i,reporte)
            if i.user.id not in {row['owner'],*(x['user'] for x in row['participants'])} and not ctx['staff'](i):raise ValueError(t('Solo el autor, sus participantes o el staff pueden ver el reporte.'))
            await i.response.defer(ephemeral=True)
            class Dest:
                async def send(self,**kwargs):return await i.followup.send(**kwargs,ephemeral=True)
            await system.file_send(Dest(),row)

        @group.command(name='mis_reportes',description='Listar tus reportes y los de tu squad')
        async def my_reports(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1):
            system.guild(i)
            rows=[r for r in store.all(i.guild_id) if r['state']!='deleted' and i.user.id in {r['owner'],*(x['user'] for x in r['participants'])}]
            await list_rows(i,rows,pagina)

        async def list_rows(i,rows,page):
            pages=max(1,(len(rows)+9)//10)
            if page>pages:raise ValueError(''.join([format_fragment(t('Solo hay '), '', -1), format_fragment(pages, '', -1), format_fragment(t(' páginas.'), '', -1)]))
            text='\n'.join(''.join(['#', format_fragment(r['id'], '', -1), ' · ', format_fragment(t('Autorización') if r['kind'] == 'authorization' else t('Misión'), '', -1), format_fragment(t(' · Sesión '), '', -1), format_fragment(r['session'], '', -1), ' · ', format_fragment(t(STATES[r['state']]), '', -1), ' · ID ', format_fragment(r['owner'], '', -1)]) for r in rows[(page-1)*10:page*10])
            await i.response.send_message(''.join([format_fragment(t('Página '), '', -1), format_fragment(page, '', -1), '/', format_fragment(pages, '', -1), '\n'])+(text or t('Sin reportes.')),ephemeral=True)

        @group.command(name='listar',description='Listar reportes para el staff')
        @app_commands.choices(estado=[app_commands.Choice(name=v,value=k) for k,v in STATES.items()])
        async def listing(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1,estado:app_commands.Choice[str]|None=None):
            system.staff(i);rows=[r for r in store.all(i.guild_id) if (r['state']==estado.value if estado else r['state']!='deleted')]
            await list_rows(i,rows,pagina)

        @group.command(name='conteo',description='Misiones aprobadas por rango; staff puede consultar a otros')
        async def totals(i:discord.Interaction,usuario:discord.User|None=None,sesion:app_commands.Range[int,1]|None=None):
            system.guild(i);user=usuario or i.user
            if user.id!=i.user.id:system.staff(i)
            counts=store.totals(i.guild_id,user.id,sesion)
            await i.response.send_message(f"Shinobi {user.id}\n"+' · '.join(f'{k}: {v}' for k,v in counts.items())+''.join([format_fragment(t('\nTotal aprobado: '), '', -1), format_fragment(sum(counts.values()), '', -1), format_fragment(t('. Las autorizaciones, borradores, rechazados y congelados no suman.'), '', -1)]),ephemeral=True)

        @group.command(name='actividad',description='Horarios autorizados y misiones aprobadas por sesión')
        async def activity(i:discord.Interaction,usuario:discord.User|None=None,pagina:app_commands.Range[int,1,100000]=1):
            system.guild(i);user=usuario or i.user
            if user.id!=i.user.id:system.staff(i)
            records=store.activity(i.guild_id,user.id)
            pages=max(1,(len(records)+4)//5)
            if pagina>pages:raise ValueError(''.join([format_fragment(t('Solo hay '), '', -1), format_fragment(pages, '', -1), format_fragment(t(' páginas.'), '', -1)]))
            text=[]
            for r in records[(pagina-1)*5:pagina*5]:
                end=t('En curso') if r['ongoing'] else formatted(r['end'])
                counts=' · '.join(f'{k}:{v}' for k,v in r['totals'].items())
                text.append(''.join([format_fragment(t('Sesión #'), '', -1), format_fragment(r['session'], '', -1), format_fragment(t(' · Autorización #'), '', -1), format_fragment(r['authorization'], '', -1), '\n', format_fragment(formatted(r['start']), '', -1), ' → ', format_fragment(end, '', -1), '\n', format_fragment(r['seconds'] // 60, '', -1), format_fragment(t(' minutos autorizados · '), '', -1), format_fragment(counts, '', -1)]))
            await i.response.send_message(''.join([format_fragment(t('Página '), '', -1), format_fragment(pagina, '', -1), '/', format_fragment(pages, '', -1), format_fragment(t(' · Hora Colombia\n'), '', -1)])+('\n\n'.join(text) or t('Sin actividad autorizada.'))+t('\nEl tiempo autorizado no mide conexión real a Roblox.'),ephemeral=True)

        # Separate named commands make the administrative actions easy to discover.
        def add_action(name,action,description):
            async def callback(i:discord.Interaction,reporte:app_commands.Range[int,1]):
                system.staff(i,admin=action=='delete');row=system.get(i,reporte)
                await i.response.send_modal(Decision(row,action))
            group.add_command(app_commands.Command(name=name,description=description,callback=callback))
        for name,action,description in [('aprobar','approve',t('Aprobar/autorizar un reporte pendiente')),('rechazar','reject',t('No autorizar o rechazar un reporte pendiente')),('corregir','correct',t('Solicitar corrección de un reporte pendiente')),('congelar','freeze',t('Congelar un reporte y retirar sus créditos')),('reabrir','reopen',t('Permitir al autor corregir y reenviar un reporte')),('borrar','delete',t('Eliminar de los activos con confirmación (administración)'))]:add_action(name,action,description)

        @group.command(name='historial',description='Últimas 15 acciones de un reporte (staff)')
        async def history(i:discord.Interaction,reporte:app_commands.Range[int,1]):
            system.staff(i);rows=store.history(i.guild_id,reporte)
            await i.response.send_message(('\n'.join(f"{formatted(r['created'])} · {r['action']} · ID {r['actor']}" for r in rows) or t('Sin historial.'))[:1950],ephemeral=True)

        @group.command(name='estado',description='Sesión actual y problemas de envío (staff)')
        async def status(i:discord.Interaction):
            system.staff(i);s=store.current(i.guild_id);rows=store.all(i.guild_id)
            text=''.join([format_fragment(t('Sesión abierta: '), '', -1), format_fragment(s['id'] if s else t('Ninguna'), '', -1), format_fragment(t('\nPendientes de revisar: '), '', -1), format_fragment(sum((r['state'] == 'pending' for r in rows)), '', -1), '\n'])
            text+='\n'.join(f"#{r['id']}: {system.clean(r['error'],180)}" for r in rows if r['error'])[:1500] or t('Sin errores de envío.')
            await i.response.send_message(text,ephemeral=True)

        @group.command(name='reintentar',description='Actualizar ahora los mensajes de revisión pendientes (staff)')
        async def retry(i:discord.Interaction):
            system.staff(i);await i.response.defer(ephemeral=True);await system.deliver()
            await i.followup.send(t('Reintento terminado. Consulta /misiones estado.'),ephemeral=True)

        @group.command(name='ayuda',description='Flujo y comandos del sistema de misiones')
        async def help_command(i:discord.Interaction):
            system.guild(i)
            await i.response.send_message(t('**Líder:** /misiones abrir, cerrar. El propietario puede designarlo con /misiones lider.\n**Administración:** /misiones configurar, panel, borrar. /registro respaldo incluye los reportes.\n**Jugadores:** panel de autorización → esperar aprobación → jugar → panel de misión → esperar revisión. /misiones salida, continuar, nuevo, ver, mis_reportes, conteo, actividad.\n**Staff:** /misiones aprobar, rechazar, corregir, congelar, reabrir, listar, historial, estado, reintentar.\nUna misión aprobada suma una por integrante, nunca una por captura. Congelar o borrar retira esos créditos. Todas las horas se escriben en hora de Colombia.'),ephemeral=True)

        self.bot.tree.add_command(group)
