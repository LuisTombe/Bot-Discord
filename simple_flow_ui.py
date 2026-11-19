"""Paneles guiados y persistentes. Ninguna captura se interpreta automáticamente."""
import asyncio
import io
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
import discord
from discord import app_commands
from evidence import MAX_BYTES, safe_path
from missions_domain import EDITABLE, BOGOTA, formatted
from mission_evidence_policy import validate_screenshot
from simple_flow_domain import SimpleFlow, NORMAL
from simple_transactions import atomic
from worldx_v7 import split_reward
from wx_market.ui import SafeView, ryo

log=logging.getLogger('worldx.simple')

class SafeModal(discord.ui.Modal):
    async def on_error(self,i,error):
        log.exception('Formulario sencillo',exc_info=error)
        text=str(error) if isinstance(error,ValueError) else 'No se pudo completar. Consulta el estado antes de repetir.'
        if i.response.is_done():await i.followup.send(text[:1800],ephemeral=True)
        else:await i.response.send_message(text[:1800],ephemeral=True)

async def upload_files(ui,attachments):
    proofs=[]
    try:
        for a in attachments:
            if not 0<a.size<=MAX_BYTES:raise ValueError('Cada imagen debe pesar como máximo 8 MiB.')
            ui.ms.evidence_policy.check_upload_budget(a.size)
            data=await asyncio.wait_for(a.read(),90);ext,sha=validate_screenshot(data)
            rel='capturas/reportes/'+uuid.uuid4().hex+ext
            path=safe_path(ui.ms.root,rel);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            proofs.append({'path':rel,'digest':sha})
        return proofs
    except BaseException:
        for p in proofs:safe_path(ui.ms.root,p['path']).unlink(missing_ok=True)
        raise

class AuthorizationModal(SafeModal,title='Comenzar misiones'):
    level=discord.ui.TextInput(label='Nivel del personaje',max_length=4)
    stats=discord.ui.TextInput(label='CHI NIN TAI HP (ejemplo: 900 900 900 900)',max_length=40)
    def __init__(self,ui,row):
        super().__init__();self.ui=ui;self.row=row
        self.level.default=str(row['data'].get('level',''));self.stats.default=row['data'].get('stats','')
        self.proofs=discord.ui.FileUpload(min_values=1,max_values=2,required=True)
        self.add_item(discord.ui.Label(text='Capturas de estadísticas y fecha/hora',description='Pantalla completa. Si necesitas dos capturas, ambas deben tener fecha y hora del dispositivo.',component=self.proofs))
    async def on_submit(self,i):
        ui=self.ui;ui.system.player(i);await i.response.defer(ephemeral=True)
        await ui.eco.sync(i.guild_id,i.user.id);e=ui.eco.store.player(i.guild_id,i.user.id)
        paths=await upload_files(ui,self.proofs.values)
        try:
            d={**self.row['data'],'simple_flow':True,'rank':e['rank'],'level':str(self.level),'stats':str(self.stats),'village':ui.eco.store.village(i.guild_id,e['village'])['name'],'purpose':'Realizar misiones','counterparty':'Individual o squad autorizado','proofs':paths}
            row=ui.ms.edit(self.row['id'],self.row['revision'],i.user.id,d,paths[0]);ui.ms.submit(row['id'],row['revision'],i.user.id)
        except Exception:
            # Si el borrador ya guarda la evidencia, se conserva para corrección.
            fresh=ui.ms.get(self.row['id'])
            if fresh['proof']!=paths[0]['path']:
                for p in paths:safe_path(ui.ms.root,p['path']).unlink(missing_ok=True)
            raise
        await i.followup.send('Solicitud enviada. Espera la autorización; puedes consultar su estado en Mis reportes.',ephemeral=True)

class BatchMissionModal(SafeModal,title='Reportar tanda de misiones (5+)'):
    def __init__(self,ui,members=None):
        super().__init__();self.ui=ui;self.members=members
        self.proofs=discord.ui.FileUpload(min_values=5,max_values=10,required=True)
        self.add_item(discord.ui.Label(
            text='Sube mínimo 5 capturas (misiones + menú M)',
            description='Faltando 5-7s (o ejecutando), fecha/hora, panel de jugadores a la derecha y código privado abajo.',
            component=self.proofs
        ))
        self.notes=discord.ui.TextInput(label='Observaciones o compañeros (opcional)',max_length=400,required=False,style=discord.TextStyle.short)
        self.add_item(self.notes)
    async def on_submit(self,i):
        ui=self.ui;ui.system.player(i);await i.response.defer(ephemeral=True)
        if len(self.proofs.values)<5:
            raise ValueError('Debes subir obligatorio un mínimo de 5 capturas por tanda.')
        await ui.eco.sync(i.guild_id,i.user.id);ui.eco.store.player(i.guild_id,i.user.id)
        session=ui.flow.daily_session(i.guild_id,i.user.id)
        proofs=await upload_files(ui,self.proofs.values)
        members=sorted({i.user.id,*(self.members or [])})
        now_str=datetime.now(BOGOTA).strftime('%d/%m/%Y %H:%M')
        batch_count=max(5,len(proofs)-1) if len(proofs)>5 else 5
        d={
            'simple_flow':True,
            'batch_flow':True,
            'batch_count':batch_count,
            'world':'open_world',
            'type':'tanda_misiones',
            'enemy':'none',
            'mission_rank':'C',
            'members':members,
            'start':now_str,
            'end':now_str,
            'notes':str(self.notes).strip() or f'Tanda de {len(proofs)} capturas ({batch_count} misiones)',
            'opponents':str(self.notes).strip() or 'Tanda de misiones Open World',
            'proofs':proofs,
        }
        with ui.ms.db:
            c=ui.ms.db.execute(
                "INSERT INTO mission_reports(guild,session,owner,kind,state,revision,data,proof,digest,created) VALUES(?,?,?,'mission','draft',1,?,?,?,?)",
                (i.guild_id,session['id'],i.user.id,json.dumps(d),proofs[0]['path'],proofs[0]['digest'],ui.ms.now())
            )
            rid=c.lastrowid
        row=ui.ms.submit(rid,1,i.user.id)
        await i.followup.send(f'✅ Tanda #{row["id"]} enviada con **{len(proofs)} capturas** a tu publicación en el foro de misiones. El staff revisará las capturas y aprobará el pago.',ephemeral=True)

class BatchSquadView(SafeView):
    def __init__(self,ui,owner):
        super().__init__(timeout=600);self.ui=ui;self.owner=owner;self.selected_members=[owner]
        users=discord.ui.UserSelect(placeholder='Compañeros si hicieron las misiones en conjunto (opcional)',min_values=0,max_values=max(1,ui.ms.config['max_squad']-1),row=0)
        async def on_users(i):
            ids=sorted({owner,*(u.id for u in users.values)})
            for u in users.values:
                ui.system.review_ui.shinobi(u)
            self.selected_members=ids
            await i.response.send_modal(BatchMissionModal(ui,self.selected_members))
        users.callback=on_users;self.add_item(users)
    async def interaction_check(self,i):
        self.ui.system.player(i)
        if i.user.id!=self.owner:raise ValueError('Abre tu propio reporte.')
        return True
    @discord.ui.button(label='Subir tanda de 5+ capturas (Solitario / Continuar)',emoji='📷',style=discord.ButtonStyle.success,row=1)
    async def upload_now(self,i,b):
        await i.response.send_modal(BatchMissionModal(self.ui,self.selected_members))

class MissionModal(SafeModal,title='Enviar evidencia de misión'):
    start=discord.ui.TextInput(label='Inicio (Colombia): DD/MM/AAAA HH:MM',max_length=16)
    end=discord.ui.TextInput(label='Fin/captura (Colombia): DD/MM/AAAA HH:MM',max_length=16)
    notes=discord.ui.TextInput(label='Objetivo / username enemigo / observaciones',max_length=700,style=discord.TextStyle.paragraph)
    def __init__(self,ui,row):
        super().__init__();self.ui=ui;self.row=row
        d=row['data'];self.start.default=d.get('start',datetime.now(BOGOTA).strftime('%d/%m/%Y %H:%M'));self.end.default=d.get('end',datetime.now(BOGOTA).strftime('%d/%m/%Y %H:%M'));self.notes.default=d.get('notes','')
        self.proofs=discord.ui.FileUpload(min_values=1,max_values=4,required=True)
        self.add_item(discord.ui.Label(text='Capturas completas con fecha y hora',description=ui.ms.evidence_policy.rule(row)['evidence'][:100],component=self.proofs))
    async def on_submit(self,i):
        ui=self.ui;ui.system.player(i);await i.response.defer(ephemeral=True)
        proofs=await upload_files(ui,self.proofs.values)
        d={**self.row['data'],'start':str(self.start),'end':str(self.end),'notes':str(self.notes),'opponents':str(self.notes),'proofs':proofs}
        row=ui.ms.edit(self.row['id'],self.row['revision'],i.user.id,d,proofs[0]);row=ui.ms.submit(row['id'],row['revision'],i.user.id)
        await i.followup.send(f'Reporte #{row["id"]} enviado. El staff revisará y pagará desde la publicación; no debes hacer otro trámite.',ephemeral=True)

class MissionWizard(SafeView):
    def __init__(self,ui,row):
        super().__init__(timeout=900);self.ui=ui;self.row=row;d=row['data']
        worlds=discord.ui.Select(placeholder='1. Mundo',row=0,options=[discord.SelectOption(label=l,value=k,default=d.get('world','world_x')==k) for k,l in [('world_x','World X'),('open_world','Open World'),('rpg','Shindo normal')]])
        async def world(i):
            fresh=ui.ms.edit(row['id'],row['revision'],i.user.id,{**d,'world':worlds.values[0],'type':None,'enemy':'none'})
            await i.response.edit_message(content='Selecciona la misión y sus participantes.',view=MissionWizard(ui,fresh))
        worlds.callback=world;self.add_item(worlds)
        choices=[(k,v) for k,v in ui.ms.evidence_policy.config['missions'].items() if d.get('world','world_x') in v['worlds'] and (not k.startswith('azul_'))]
        types=discord.ui.Select(placeholder='2. Misión',row=1,options=[discord.SelectOption(label=v['label'][:100],value=k,default=d.get('type')==k) for k,v in choices[:25]])
        async def kind(i):
            fresh=ui.ms.edit(row['id'],row['revision'],i.user.id,{**d,'type':types.values[0]})
            await i.response.edit_message(content=ui.ms.evidence_policy.rule(fresh)['evidence'],view=MissionWizard(ui,fresh))
        types.callback=kind;self.add_item(types)
        if d.get('world')!='rpg':
            enemies=discord.ui.Select(placeholder='Rango del enemigo (sin enemigo por defecto)',row=2,options=[discord.SelectOption(label='Sin enemigo',value='none',default=d.get('enemy','none')=='none')]+[discord.SelectOption(label=v['label'],value=k,default=d.get('enemy')==k) for k,v in ui.ms.config['ranks'].items()])
            async def enemy(i):
                fresh=ui.ms.edit(row['id'],row['revision'],i.user.id,{**d,'enemy':enemies.values[0]});await i.response.edit_message(view=MissionWizard(ui,fresh))
            enemies.callback=enemy;self.add_item(enemies)
        users=discord.ui.UserSelect(placeholder='Compañeros opcionales; tú ya estás incluido',min_values=0,max_values=max(1,ui.ms.config['max_squad']-1),row=3)
        async def members(i):
            ids=sorted({row['owner'],*(u.id for u in users.values)})
            for u in users.values:
                ui.system.review_ui.shinobi(u);ui.flow.active(i.guild_id,u.id)
            fresh=ui.ms.edit(row['id'],row['revision'],i.user.id,{**d,'members':ids})
            await i.response.edit_message(content=f'{len(ids)} participantes. El pago total se repartirá según la regla de squad existente.',view=MissionWizard(ui,fresh))
        users.callback=members;self.add_item(users)
    async def interaction_check(self,i):
        self.ui.system.player(i)
        if i.user.id!=self.row['owner']:raise ValueError('Abre tu propio reporte.')
        return True
    @discord.ui.button(label='Adjuntar capturas y enviar',style=discord.ButtonStyle.success,row=4)
    async def send(self,i,b):
        if not self.row['data'].get('type'):raise ValueError('Selecciona la misión.')
        await i.response.send_modal(MissionModal(self.ui,self.row))

class Home(SafeView):
    def __init__(self,ui):super().__init__(timeout=None);self.ui=ui
    @discord.ui.button(label='Reportar misiones (5+ capturas)',emoji='📂',style=discord.ButtonStyle.success,custom_id='wx:simple:report',row=0)
    async def report(self,i,b):await self.ui.report(i)
    @discord.ui.button(label='Mis reportes',emoji='📋',custom_id='wx:simple:reports',row=0)
    async def reports(self,i,b):await self.ui.reports(i)
    @discord.ui.button(label='Ver mi perfil',emoji='🥷',style=discord.ButtonStyle.primary,custom_id='wx:simple:profile',row=0)
    async def profile(self,i,b):await self.ui.profiles.show(i)
    @discord.ui.button(label='Tienda',emoji='🏪',custom_id='wx:simple:shop',row=1)
    async def shop(self,i,b):await self.ui.market.shop(i)
    @discord.ui.button(label='Mis compras',emoji='🧾',custom_id='wx:simple:orders',row=1)
    async def orders(self,i,b):await self.ui.profiles.orders(i)
    @discord.ui.button(label='Inventario',emoji='🎒',custom_id='wx:simple:inventory',row=1)
    async def inventory(self,i,b):await self.ui.market.listing(i,'inventory')
    @discord.ui.button(label='Saldo / Cartera',emoji='💰',custom_id='wx:simple:balance',row=1)
    async def balance(self,i,b):await self.ui.market.listing(i,'history')
    @discord.ui.button(label='Comenzar misiones',style=discord.ButtonStyle.secondary,custom_id='wx:simple:start',row=2)
    async def start(self,i,b):await self.ui.begin(i)
    @discord.ui.button(label='Terminar misiones',style=discord.ButtonStyle.secondary,custom_id='wx:simple:end',row=2)
    async def end(self,i,b):
        self.ui.system.player(i)
        try:
            a=self.ui.flow.active(i.guild_id,i.user.id)
            self.ui.ms.end_authorization(i.guild_id,i.user.id,a['session'])
            await i.response.send_message('Actividad cerrada.',ephemeral=True)
        except ValueError:
            await i.response.send_message('No necesitas abrir ni cerrar sesión: usa directamente **Reportar misiones (5+ capturas)**.',ephemeral=True)

class Review(SafeView):
    def __init__(self,ui,row):
        super().__init__(timeout=None);self.ui=ui;self.row=row
        for text,action in [('Ver todas las pruebas','proof'),('Aceptar autorización' if row['kind']=='authorization' else 'Aprobar y pagar','approve'),('Pedir corrección','correct'),('Rechazar','reject')]:
            b=discord.ui.Button(label=text,custom_id=f'wx:simple:review:{row["id"]}:{row["revision"]}:{action}',style=discord.ButtonStyle.success if action=='approve' else discord.ButtonStyle.secondary)
            async def callback(i,action=action):
                ui.access(i,row)
                if action=='proof':await ui.proofs(i,row)
                elif action=='approve':await ui.confirm_review(i,row)
                else:
                    class Reason(SafeModal,title='Indicaciones para el jugador'):
                        reason=discord.ui.TextInput(label='Qué debe corregir / motivo',max_length=300,style=discord.TextStyle.paragraph)
                        async def on_submit(modal,inter):
                            ui.access(inter,row);ui.ms.decision(row['id'],row['revision'],inter.user.id,action,str(modal.reason))
                            await inter.response.send_message('Decisión guardada. El jugador puede verla en Mis reportes.',ephemeral=True)
                    await i.response.send_modal(Reason())
            b.callback=callback;self.add_item(b)

class SimpleUI:
    def __init__(self,bot,ctx):
        self.bot=bot;self.ctx=ctx;self.system=ctx['MISSIONS'];self.ms=self.system.store;self.eco=ctx['ECONOMY'];self.market=ctx['MARKET']
        self.flow=SimpleFlow(self.ms,self.eco.store,self.market.store);self.home=Home(self)
        settings=self.ms.settings(ctx['GUILD'])
        if not settings.get('simple_installed_at'):
            self.ms.configure(ctx['GUILD'],0,simple_installed_at=self.ms.now())
            settings=self.ms.settings(ctx['GUILD'])
        self.installed_at=settings['simple_installed_at']
        if settings.get('simple_purchase_channel'):self.market.config['channels']['purchase']=settings['simple_purchase_channel']
        if settings.get('simple_review_roles'):self.market.config['review_role_ids']=settings['simple_review_roles']
        original_checklist=self.ms.evidence_policy.checklist
        def checklist(row):
            result=original_checklist(row)
            if row['data'].get('simple_flow'):
                result+=['Fecha/hora visibles coherentes con el día de la sesión y el horario declarado; comprobar manualmente.']
            return result
        self.ms.evidence_policy.checklist=checklist
        rules=self.ms.evidence_policy.config['missions']
        for k,(label,_) in NORMAL.items():rules[k]={'label':label,'worlds':['rpg'],'base_rank':'D','min_images':1,'evidence':'Objetivo y progreso/resultado visibles; pantalla completa con username, fecha y hora del dispositivo.'}
        rules['tanda_misiones']={'label':'Tanda de misiones (5+ capturas)','worlds':['open_world','world_x','rpg'],'base_rank':'C','min_images':5,'evidence':'Mínimo 5 capturas: a punto de completar (5-7s) o ejecutando al instante, fecha y hora del equipo, panel de jugadores a la derecha, código privado abajo a la derecha y captura del menú M con stats/slots.'}
        rules['ejecutar'].update(min_images=1,evidence='Objetivo abatido/K/O en el suelo y jugador realizando la ejecución; fecha y hora del dispositivo visibles.')
        for k in ('proteger_carga','proteger_objetivo','proteger_persona'):
            rules[k]['evidence']='Objetivo y panel visibles. Si exige permanencia: temporizador de 3 segundos o menos; fecha y hora del dispositivo visibles.'
        self.system.Review=lambda row:Review(self,row)
        old_validate=self.system.review_ui.validate_channel
        def channel(ch):
            parent=ch.parent if isinstance(ch,discord.Thread) else ch
            if isinstance(parent,discord.ForumChannel):
                if parent.id!=self.ms.settings(ch.guild.id).get('simple_forum'):raise ValueError('Foro de misiones no configurado.')
                if parent.permissions_for(ch.guild.default_role).view_channel:raise ValueError('Oculta el foro a @everyone y permite solo Shinobi/staff.')
                p=ch.permissions_for(ch.guild.me)
                if not all(getattr(p,k) for k in ('view_channel','send_messages_in_threads','attach_files','embed_links','read_message_history')):raise ValueError('Faltan permisos del bot en el foro.')
            else:old_validate(ch)
        self.system.review_ui.validate_channel=channel
        self.system.review_ui.review_access=self.access
        old_id=self.system.channel_id
        self.system.channel_id=lambda kind:self.ms.settings(ctx['GUILD']).get('simple_forum',old_id(kind)) if kind=='mission' else old_id(kind)
        old_restore=self.system.restore
        def restore():
            old_restore();bot.add_view(self.home);self.profiles.restore()
            for kind in ('authorization','mission'):
                legacy=SafeView(timeout=None)
                b=discord.ui.Button(label='Abrir',custom_id='worldx:missions:panel:'+kind)
                async def callback(i,kind=kind):
                    if kind=='authorization':await self.begin(i)
                    else:await self.report(i)
                b.callback=callback;legacy.add_item(b);bot.add_view(legacy)
        self.system.restore=restore
        # Los paneles antiguos también llevan al flujo nuevo.
        # Cada mensaje necesita su propia vista. Discord asigna 15 minutos a
        # las vistas efímeras; nunca enviar self.home (registro persistente).
        self.system.Panel=lambda kind:Home(self)
        original_deliver=self.system.deliver
        async def deliver():
            await original_deliver();await self.profiles.deliver();await self.notify()
        self.system.deliver=deliver
        from simple_market_ui import ProfileUI
        self.profiles=ProfileUI(self)
        self.install()

    def access(self,i,row):
        self.system.staff(i);fresh=self.system.get(i,row['id'])
        if fresh['revision']!=row['revision'] or fresh['state']!='pending':raise ValueError('Este reporte ya cambió o fue resuelto.')
        if i.user.id in {row['owner'],*row['data'].get('members',[])}:raise ValueError('Otro supervisor debe revisar tu reporte.')
        # El panel privado y los comandos muestran exactamente la misma evidencia.
        return fresh

    async def begin(self,i):
        self.system.player(i)
        # Sin red antes de send_modal; el submit sincroniza los roles reales.
        self.flow.daily_session(i.guild_id,i.user.id)
        row=self.ms.start(i.guild_id,i.user.id,'authorization')
        if row['state'] not in EDITABLE:
            await i.response.send_message('Tu autorización está '+row['state']+'. Consulta Mis reportes.',ephemeral=True);return
        await i.response.send_modal(AuthorizationModal(self,row))

    async def report(self,i,row=None):
        self.system.player(i)
        if row is not None:
            if row['owner']!=i.user.id or row['state'] not in EDITABLE:raise ValueError('Reporte ajeno o ya enviado.')
            if row['session']==0:
                a=self.flow.active(i.guild_id,i.user.id)
                with self.ms.db:self.ms.db.execute('UPDATE mission_reports SET session=?,revision=revision+1 WHERE id=?',(a['session'],row['id']))
                row=self.ms.get(row['id'])
            if not row['data'].get('simple_flow'):
                row=self.ms.edit(row['id'],row['revision'],i.user.id,{**row['data'],'simple_flow':True,'world':'world_x','enemy':'none'})
            await i.response.send_message('Selecciona mundo, misión y compañeros. Las capturas deben mostrar fecha y hora del dispositivo.',view=MissionWizard(self,row),ephemeral=True)
            return
        guide=(
            '📂 **REPORTAR TANDA DE MISIONES (Mínimo 5 capturas)**\n'
            '• Tu reporte se publicará en tu hilo único del foro **`TuNombre - misiones`**.\n'
            '• Sube **5 o más capturas** a punto de completar la misión (faltando **5 a 7 segundos**, o ejecutando al objetivo al instante).\n'
            '• Incluye la captura de tu **menú (M)** donde se vean stats, slots, bloodlines, elementos y nivel.\n'
            '• En las capturas debe verse: **fecha y hora de tu equipo**, **panel de jugadores a la derecha** (para pagos en conjunto) y **código privado del servidor abajo a la derecha**.\n'
            '• Si la hicieron en conjunto, elige a tus compañeros abajo; si fue en solitario, pulsa directamente el botón verde.'
        )
        await i.response.send_message(guide,view=BatchSquadView(self,i.user.id),ephemeral=True)

    async def reports(self,i,page=0,staff=False):
        self.system.guild(i)
        if staff:self.system.staff(i)
        where="guild=? AND state!='deleted'";args=[i.guild_id]
        if staff:where+=" AND state='pending'"
        else:where+=' AND (owner=? OR id IN (SELECT report FROM mission_participants WHERE user=?))';args +=[i.user.id,i.user.id]
        rows=[self.ms.get(r[0]) for r in self.ms.db.execute('SELECT id FROM mission_reports WHERE '+where+' ORDER BY id DESC LIMIT 8 OFFSET ?',(*args,page*8))]
        v=SafeView(timeout=600)
        if rows:
            select=discord.ui.Select(placeholder='Abrir reporte / autorización',options=[discord.SelectOption(label=f'#{r["id"]} · {r["kind"]} · {r["state"]}',value=str(r['id'])) for r in rows])
            async def selected(inter):
                if inter.user.id!=i.user.id:raise ValueError('Abre tu lista.')
                r=self.system.get(inter,int(select.values[0]))
                if staff:await self.proofs(inter,r)
                elif r['owner']==inter.user.id and r['state'] in EDITABLE:
                    if r['kind']=='authorization':await inter.response.send_modal(AuthorizationModal(self,r))
                    else:await self.report(inter,r)
                else:
                    await inter.response.defer(ephemeral=True)
                    detail_view=SafeView(timeout=600)
                    if r['kind']=='authorization' and r['state']=='approved' and r['owner']==inter.user.id:
                        b=discord.ui.Button(label='Reportar misión de esta sesión')
                        async def late(ii):
                            self.system.player(ii)
                            if ii.user.id!=r['owner']:raise ValueError('Autorización ajena.')
                            draft=self.ms.start(ii.guild_id,ii.user.id,'mission',r['session'])
                            await self.report(ii,draft)
                        b.callback=late;detail_view.add_item(b)
                    await inter.followup.send(embed=self.system.card(r),view=detail_view,ephemeral=True)
            select.callback=selected;v.add_item(select)
        for label,delta in [('Anterior',-1),('Siguiente',1)]:
            b=discord.ui.Button(label=label,disabled=page==0 and delta<0 or len(rows)<8 and delta>0)
            async def turn(inter,delta=delta):
                if inter.user.id!=i.user.id:raise ValueError('Abre tu lista.')
                await self.reports(inter,page+delta,staff)
            b.callback=turn;v.add_item(b)
        lines=[f'#{r["id"]} · {r["kind"]} · **{r["state"]}**'+(' · '+r['reason'] if r['reason'] else '') for r in rows]
        counts=self.ms.db.execute("SELECT json_extract(r.data,'$.type') kind,c.rank,COUNT(*) n FROM mission_credits c JOIN mission_reports r ON r.id=(CASE WHEN c.report<0 THEN (-c.report)/100 ELSE c.report END) WHERE r.guild=? AND c.user=? AND r.state='approved' GROUP BY kind,c.rank",(i.guild_id,i.user.id)).fetchall()
        summary={k:0 for k in ('D','C','B','A','S','Azules','Verdes','Rojas')}
        for r in counts:
            key={'normal_azul':'Azules','normal_verde':'Verdes','normal_roja':'Rojas'}.get(r['kind'],'Azules' if (r['kind'] or '').startswith('azul_') else r['rank']);summary[key]+=r['n']
        await self.market.reply(i,content=('\n'.join(lines) or 'Sin reportes.')+'\n\n'+' · '.join(f'{k}: {v}' for k,v in summary.items()),view=v)

    async def proofs(self,i,row):
        self.access(i,row);await i.response.defer(ephemeral=True)
        class Dest:
            async def send(inner,**kw):return await i.followup.send(**kw,ephemeral=True)
        await self.system.file_send(Dest(),row,Review(self,row))
        for p in row['participants']:
            if p['authorization']:
                auth=self.ms.get(p['authorization'])
                if auth:await self.system.file_send(Dest(),auth)

    async def confirm_review(self,i,row):
        self.access(i,row)
        ui=self
        class Confirm(SafeModal,title='Confirmar revisión humana'):
            confirmation=discord.ui.TextInput(label='Revisé todas las pruebas: CONFIRMO',default='CONFIRMO',max_length=8)
            def __init__(self):
                super().__init__()
                self.amount=None
                self.reason=discord.ui.TextInput(label='Comentario / razón de aprobación',default='Misiones verificadas',max_length=250,required=False)
                if row['kind']=='mission':
                    lo,hi=ui.flow.limits(row)
                    default_pay=25000 if row['data'].get('batch_flow') else lo
                    self.amount=discord.ui.TextInput(label=f'Pago total Ryo ({lo} a {hi})',default=str(default_pay),max_length=12)
                    self.add_item(self.amount)
                self.add_item(self.reason)
            async def on_submit(modal,inter):
                ui.access(inter,row)
                if str(modal.confirmation).strip().upper()!='CONFIRMO':raise ValueError('Escribe CONFIRMO después de revisar las capturas, fecha/hora, username y objetivo.')
                await inter.response.defer(ephemeral=True)
                for u in set(row['data'].get('members',[row['owner']])):
                    await ui.eco.sync(inter.guild_id,u);member=await inter.guild.fetch_member(u);ui.system.review_ui.shinobi(member)
                async with ui.eco.lock:
                    ui.access(inter,row)
                    with atomic(ui.ms.db):
                        ui.flow.attest_all(row['id'],row['revision'],inter.user.id)
                        if row['kind']=='authorization':
                            ui.ms.decision(row['id'],row['revision'],inter.user.id,'approve',str(modal.reason).strip() or 'Capturas completas, fecha/hora, nivel, stats y username verificados.')
                            text='Autorización aceptada. El jugador ya puede reportar misiones.'
                        else:
                            try:n=int(str(modal.amount))
                            except ValueError:raise ValueError('Escribe el importe sin puntos ni comas.')
                            tx,recipients=ui.flow.approve_pay(inter.guild_id,inter.user.id,row['id'],row['revision'],n)
                            cnt=max(1,int(row['data'].get('batch_count',1)))
                            text=f'Misión aprobada y pagada · comprobante #{tx}.\n'+'\n'.join(f'<@{u}>: {ryo(v)} · +{cnt} misión(es)' for u,v in recipients.items())
                await inter.followup.send(text,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        # En tandas (batch_flow) o autorizaciones, abre el modal directamente con un clic.
        if row['kind']=='mission' and not row['data'].get('batch_flow'):
            lo,hi=self.flow.limits(row);v=SafeView(timeout=300)
            b=discord.ui.Button(label='Revisé las pruebas: confirmar pago',style=discord.ButtonStyle.success)
            async def open_modal(inter):
                if inter.user.id!=i.user.id:raise ValueError('Abre tu revisión.')
                self.access(inter,row);await inter.response.send_modal(Confirm())
            b.callback=open_modal;v.add_item(b)
            parts=split_reward(lo,row['data']['roster'])
            text=f'Tesorería: aldea del autor. Importe permitido: {ryo(lo)}–{ryo(hi)}.\nReparto sugerido:\n'+'\n'.join(f'<@{u}>: {ryo(n)}' for u,n in parts.items())+'\nConfirma solo si verificaste TODAS las pruebas, las autorizaciones y la fecha/hora visible coherente con la sesión. Un importe ajustado usa el mismo reparto proporcional.'
            await i.response.send_message(text,view=v,ephemeral=True)
        else:await i.response.send_modal(Confirm())

    async def notify(self):
        rows=self.ms.db.execute("SELECT r.id,r.owner,r.revision,r.state,r.reason FROM mission_reports r WHERE r.state IN ('approved','rejected','correction') AND json_extract(r.data,'$.simple_flow')=1 AND NOT EXISTS(SELECT 1 FROM simple_notifications n WHERE n.kind='mission' AND n.ident=r.id AND n.revision=r.revision AND n.user=r.owner) LIMIT 10").fetchall()
        for r in rows:
            try:
                user=await self.bot.fetch_user(r['owner']);await user.send(f'World X · reporte #{r["id"]}: {r["state"]}. {r["reason"]}\nConsulta el panel → Mis reportes.')
            except discord.Forbidden:pass
            except discord.HTTPException:continue
            with self.ms.db:self.ms.db.execute("INSERT OR IGNORE INTO simple_notifications VALUES('mission',?,?,?)",(r['id'],r['revision'],r['owner']))

        market_rows=self.market.store.rows("SELECT * FROM market_requests r WHERE r.guild=? AND r.state IN ('approved','rejected','expired','cancelled') AND (r.created>=? OR r.reviewed_at>=?) AND NOT EXISTS(SELECT 1 FROM simple_notifications n WHERE n.kind='market' AND n.ident=r.id) ORDER BY id LIMIT 10",(self.ctx['GUILD'],self.installed_at,self.installed_at))
        for r in market_rows:
            for uid in set((r['actor'],r['target'])):
                try:
                    user=await self.bot.fetch_user(uid)
                    await user.send(f'World X · solicitud #{r["id"]}: {r["state"]}. {r["reason"] or ""}\nPanel → Mis compras / Inventario.')
                except discord.Forbidden:pass
                except discord.HTTPException:continue
                with self.ms.db:self.ms.db.execute("INSERT OR IGNORE INTO simple_notifications VALUES('market',?,0,?)",(r['id'],uid))

    def install(self):
        bot=self.bot;ui=self
        # Sustituye el acceso anterior, manteniendo el comando conocido.
        bot.tree.remove_command('reporte')
        @bot.tree.command(name='reporte',description='Reportar una misión con capturas y revisión del staff')
        async def report(i:discord.Interaction):await ui.report(i)
        group=app_commands.Group(name='panel',description='Flujo sencillo de misiones y compras',guild_only=True)
        @group.command(name='abrir',description='Abrir tu panel personal')
        async def home(i:discord.Interaction):
            ui.system.guild(i);await i.response.send_message('World X · Tu actividad, compras e inventario',view=Home(ui),ephemeral=True)
        @group.command(name='publicar',description='Staff: publicar panel permanente en este canal')
        async def publish(i:discord.Interaction):
            ui.system.staff(i,True);await i.response.defer(ephemeral=True)
            await i.channel.send('**WORLD X · MISIONES Y ECONOMÍA**\nComienza tu sesión y espera autorización. Después reporta cada misión con capturas completas y fecha/hora del dispositivo.\nCompra permisos desde Tienda; el staff revisará tu evidencia.',view=Home(ui))
            await i.followup.send('Panel publicado.',ephemeral=True)
        @group.command(name='configurar',description='Staff: elegir canal de autorizaciones y foro de misiones')
        async def configure(i:discord.Interaction,autorizaciones:discord.TextChannel,foro:discord.ForumChannel):
            ui.system.staff(i,True);ui.system.review_ui.validate_channel(autorizaciones)
            if foro.permissions_for(i.guild.default_role).view_channel:raise ValueError('Oculta el foro a @everyone; permite ver y leer a Shinobi y staff.')
            p=foro.permissions_for(i.guild.me)
            if not all(getattr(p,k) for k in ('view_channel','send_messages','send_messages_in_threads','attach_files','embed_links','read_message_history')):raise ValueError('El bot necesita crear publicaciones, enviar mensajes, adjuntar archivos y leer el foro.')
            if foro.flags.require_tag:raise ValueError('Desactiva exigir etiqueta en el foro para que el bot pueda publicar.')
            ui.ms.configure(i.guild_id,i.user.id,auth_channel=autorizaciones.id,simple_forum=foro.id)
            await i.response.send_message('Configurado. Las capturas de misiones serán visibles para quienes puedan ver el foro. Las autorizaciones permanecen en el canal privado.',ephemeral=True)
        @group.command(name='pendientes',description='Staff: abrir autorizaciones y misiones pendientes')
        async def pending(i:discord.Interaction):await ui.reports(i,staff=True)
        @group.command(name='pagar',description='Staff: revisar y pagar una misión pendiente')
        async def pay(i:discord.Interaction,reporte:int):await ui.confirm_review(i,ui.system.get(i,reporte))
        @pay.autocomplete('reporte')
        async def choices(i,current):
            ui.system.staff(i)
            rows=ui.ms.db.execute("SELECT id,owner FROM mission_reports WHERE guild=? AND state='pending' AND kind='mission' ORDER BY id DESC LIMIT 25",(i.guild_id,))
            return [app_commands.Choice(name=f'Misión #{r[0]} · jugador {r[1]}',value=r[0]) for r in rows if str(current) in str(r[0])]
        @group.command(name='compras',description='Administración: elegir canal y rol revisor de compras')
        async def market_config(i:discord.Interaction,canal:discord.TextChannel,rol:discord.Role):
            ui.system.staff(i,True)
            if rol.is_default() or rol.is_bot_managed():raise ValueError('Elige un rol de supervisores, no @everyone ni un rol de bot.')
            old=json.loads(json.dumps(ui.market.config))
            try:
                ui.market.config['channels']['purchase']=canal.id
                ui.market.config['review_role_ids']=list(set(ui.market.config['review_role_ids']+[rol.id]))
                ui.market.channel(i.guild_id,'purchase')
                # Persistir como override SQLite, sin sobrescribir la configuración del hosting.
                ui.ms.configure(i.guild_id,i.user.id,simple_purchase_channel=canal.id,simple_review_roles=ui.market.config['review_role_ids'])
            except Exception:
                ui.market.config.clear();ui.market.config.update(old);raise
            await i.response.send_message('Canal y revisores guardados. Compras y perfiles se revisarán aquí.',ephemeral=True)
        @group.command(name='estado',description='Administración: comprobar configuración del flujo sencillo')
        async def status(i:discord.Interaction):
            ui.system.staff(i,True);await i.response.defer(ephemeral=True);lines=[]
            for kind in ('authorization','mission'):
                try:
                    ch=await ui.bot.channel(ui.system.channel_id(kind));ui.system.review_ui.validate_channel(ch)
                    lines.append('✅ '+kind+': '+ch.mention)
                except (ValueError,discord.HTTPException) as e:lines.append('❌ '+kind+': '+str(e))
            try:lines.append('✅ Compras: '+ui.market.channel(i.guild_id,'purchase').mention)
            except ValueError as e:lines.append('❌ '+str(e))
            role=i.guild.get_role(ui.system.review_ui.role)
            lines.append(('✅' if role else '❌')+' Rol Shinobi: '+(role.name if role else 'Configura SHINOBI_ROLE_ID'))
            lines.append(('✅' if ui.eco.store.settings(i.guild_id).get('enabled') else '❌')+' Economía habilitada')
            lines.append('Aldeas configuradas: '+str(len(ui.eco.store.villages(i.guild_id))))
            await ui.market.reply(i,content='\n'.join(lines))
        bot.tree.add_command(group)
