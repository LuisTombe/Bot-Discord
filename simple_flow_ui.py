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
from simple_flow_domain import SimpleFlow, NORMAL, MISSION_PAY_RANGES, MISSION_TYPE_BASE_RANK, SHINOBI_RANKS
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

BATCH_OBJECTIVES = [
    ('rpg_azul', 'RPG · Estrella Azul (Rango D: 500–1.000 Ryo)', 'Misión civil de estrella azul en modo RPG.'),
    ('rpg_roja', 'RPG · Estrella Roja (Rango D: 1.001–2.500 Ryo)', 'Misión de estrella roja en modo RPG.'),
    ('rpg_verde', 'RPG · Pergamino Verde / Bandidos (Rango D: 2.501–4.999 Ryo)', 'Derrotar bandidos o NPCs de pergamino verde en RPG.'),
    ('D', 'Open World · Rango D Base (5.000–9.999 Ryo)', 'Entregar/robar pergamino o entregar/robar cargamento.'),
    ('C', 'Open World · Rango C Base (10.000–24.999 Ryo)', 'Proteger/destruir cargamento, escoltar o ejecutar persona.'),
    ('B', 'Open World · Rango B Base (25.000–49.999 Ryo)', 'Capturar/secuestrar objetivo o enfrentar Chunin/Medical/Police.'),
    ('A', 'Open World · Rango A (50.000–99.999 Ryo)', 'Oposición de nivel Jounin, ANBU o Jounin Sensei.'),
    ('S', 'Open World · Rango S (100.000–500.000 Ryo)', 'Oposición de nivel Subkage, Sannin, Kage o Akatsuki.'),
]

class BatchMissionModal(SafeModal,title='Reportar tanda de misiones (5+)'):
    def __init__(self,ui,members=None,enemies=None,base_key='D'):
        super().__init__();self.ui=ui;self.members=members;self.enemies=enemies or [];self.base_key=base_key
        self.proofs=discord.ui.FileUpload(min_values=5,max_values=10,required=True)
        self.add_item(discord.ui.Label(
            text='Sube mínimo 5 capturas (misiones + menú M)',
            description='Faltando 5-7s (o ejecutando), fecha/hora, panel de jugadores y código privado abajo a la derecha.',
            component=self.proofs
        ))
        self.count_input=discord.ui.TextInput(label='Cantidad de misiones en esta tanda (mínimo 5)',default='5',max_length=3,required=True)
        self.notes=discord.ui.TextInput(label='Detalle / tipos de misión u observaciones',max_length=400,required=False,style=discord.TextStyle.short)
        self.add_item(self.count_input)
        self.add_item(self.notes)
    async def on_submit(self,i):
        ui=self.ui;ui.system.player(i);await i.response.defer(ephemeral=True)
        if len(self.proofs.values)<5:
            raise ValueError('Debes subir obligatorio un mínimo de 5 capturas por tanda.')
        try:
            batch_count=int(str(self.count_input).strip())
        except ValueError:
            batch_count=max(5,len(self.proofs.values)-1) if len(self.proofs.values)>5 else 5
        if batch_count<5:
            raise ValueError('La tanda mínima obligatoria es de 5 misiones.')
        await ui.eco.sync(i.guild_id,i.user.id);ui.eco.store.player(i.guild_id,i.user.id)
        session=ui.flow.daily_session(i.guild_id,i.user.id)
        proofs=await upload_files(ui,self.proofs.values)
        members=sorted({i.user.id,*(self.members or [])})
        calc=ui.flow.calculate_difficulty(i.guild_id,self.base_key,self.enemies)
        final_rank=calc['final_rank']
        enemy_summary=', '.join(f'<@{uid}> ({SHINOBI_RANKS.get(sr,(sr,1))[0]})' for uid,sr in calc['enemies']) or 'Sin enemigos jugadores'
        now_str=datetime.now(BOGOTA).strftime('%d/%m/%Y %H:%M')
        d={
            'simple_flow':True,
            'batch_flow':True,
            'batch_count':batch_count,
            'pay_key':calc['pay_key'],
            'world':'rpg' if calc['pay_key'].startswith('rpg_') else 'open_world',
            'type':'tanda_misiones',
            'enemy':'none',
            'mission_rank':final_rank,
            'members':members,
            'enemies':[{'user':uid,'rank':sr} for uid,sr in calc['enemies']],
            'start':now_str,
            'end':now_str,
            'notes':(str(self.notes).strip() or f'Tanda de {batch_count} misiones ({calc["label"]})') + f' | Rango calculado: {final_rank} (Paga sugerida: {calc["min_pay"]:,}–{calc["max_pay"]:,} Ryo c/u)',
            'opponents':enemy_summary,
            'proofs':proofs,
        }
        with ui.ms.db:
            c=ui.ms.db.execute(
                "INSERT INTO mission_reports(guild,session,owner,kind,state,revision,data,proof,digest,created) VALUES(?,?,?,'mission','draft',1,?,?,?,?)",
                (i.guild_id,session['id'],i.user.id,json.dumps(d),proofs[0]['path'],proofs[0]['digest'],ui.ms.now())
            )
            rid=c.lastrowid
        row=ui.ms.submit(rid,1,i.user.id)
        await i.followup.send(
            f'✅ **Tanda #{row["id"]} enviada** con **{len(proofs)} capturas** ({batch_count} misiones) a tu publicación en el foro.\n'
            f'• **Clasificación:** {calc["label"]} → **Rango {final_rank}**\n'
            f'• **Enemigos detectados:** {enemy_summary}\n'
            f'• **Rango de paga por misión:** {ryo(calc["min_pay"])} – {ryo(calc["max_pay"])}\n'
            f'El Staff validará tus capturas y acreditará el pago.',
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none()
        )

class BatchSquadView(SafeView):
    def __init__(self,ui,owner):
        super().__init__(timeout=600);self.ui=ui;self.owner=owner
        self.selected_members=[owner]
        self.selected_enemies=[]
        self.base_key='D'
        obj_select=discord.ui.Select(
            placeholder='1. Clasifica el tipo / rango base de las misiones',
            row=0,
            options=[discord.SelectOption(label=label,value=key,description=desc[:100],default=key=='D') for key,label,desc in BATCH_OBJECTIVES]
        )
        async def on_obj(i):
            self.base_key=obj_select.values[0]
            calc=ui.flow.calculate_difficulty(i.guild_id,self.base_key,self.selected_enemies)
            await i.response.send_message(f'📌 Clasificación base: **{calc["label"]}** → Rango calculado actual: **{calc["final_rank"]}** ({ryo(calc["min_pay"])}–{ryo(calc["max_pay"])} por misión).',ephemeral=True)
        obj_select.callback=on_obj;self.add_item(obj_select)

        users=discord.ui.UserSelect(placeholder='2. Compañeros de misión (opcional, reparto equitativo)',min_values=0,max_values=max(1,ui.ms.config['max_squad']-1),row=1)
        async def on_users(i):
            ids=sorted({owner,*(u.id for u in users.values)})
            for u in users.values:
                ui.system.review_ui.shinobi(u)
            self.selected_members=ids
            await i.response.send_message(f'👥 Compañeros seleccionados: ' + ', '.join(f'<@{u}>' for u in ids),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        users.callback=on_users;self.add_item(users)

        enemies=discord.ui.UserSelect(placeholder='3. Shinobis enemigos enfrentados (opcional, lee sus roles)',min_values=0,max_values=5,row=2)
        async def on_enemies(i):
            self.selected_enemies=list(enemies.values)
            calc=ui.flow.calculate_difficulty(i.guild_id,self.base_key,self.selected_enemies)
            enemy_txt=', '.join(f'<@{uid}> ({SHINOBI_RANKS.get(sr,(sr,1))[0]})' for uid,sr in calc['enemies']) or 'Ninguno'
            await i.response.send_message(
                f'⚔️ **Enemigos registrados:** {enemy_txt}\n'
                f'📈 **Dificultad calculada por roles enemigos:** Rango **{calc["final_rank"]}** ({ryo(calc["min_pay"])}–{ryo(calc["max_pay"])} por misión).',
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none()
            )
        enemies.callback=on_enemies;self.add_item(enemies)
    async def interaction_check(self,i):
        self.ui.system.player(i)
        if i.user.id!=self.owner:raise ValueError('Abre tu propio reporte.')
        return True
    @discord.ui.button(label='4. Subir tanda de 5+ capturas y enviar',emoji='📷',style=discord.ButtonStyle.success,row=3)
    async def upload_now(self,i,b):
        await i.response.send_modal(BatchMissionModal(self.ui,self.selected_members,self.selected_enemies,self.base_key))

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
    @discord.ui.button(label='Reportar misiones',emoji='📂',style=discord.ButtonStyle.success,custom_id='wx:simple:report',row=0)
    async def report(self,i,b):await self.ui.report(i)
    @discord.ui.button(label='Mis reportes',emoji='📋',custom_id='wx:simple:reports',row=0)
    async def reports(self,i,b):await self.ui.reports(i)
    @discord.ui.button(label='Mi perfil',emoji='🥷',style=discord.ButtonStyle.primary,custom_id='wx:simple:profile',row=0)
    async def profile(self,i,b):await self.ui.profiles.show(i)
    @discord.ui.button(label='Tienda',emoji='🏪',custom_id='wx:simple:shop',row=1)
    async def shop(self,i,b):await self.ui.market.shop(i)
    @discord.ui.button(label='Mis compras',emoji='🧾',custom_id='wx:simple:orders',row=1)
    async def orders(self,i,b):await self.ui.profiles.orders(i)
    @discord.ui.button(label='Inventario',emoji='🎒',custom_id='wx:simple:inventory',row=1)
    async def inventory(self,i,b):await self.ui.market.listing(i,'inventory')
    @discord.ui.button(label='Saldo / Cartera',emoji='💰',custom_id='wx:simple:balance',row=1)
    async def balance(self,i,b):await self.ui.market.listing(i,'history')
    @discord.ui.button(label='Tarjeta Ninja',emoji='🪪',style=discord.ButtonStyle.secondary,custom_id='wx:simple:ninjacard',row=2)
    async def ninjacard(self,i,b):
        from ninja_card import show
        await show(self.ui.eco,i)
    @discord.ui.button(label='Guía y Pagos de Misiones',emoji='📖',style=discord.ButtonStyle.secondary,custom_id='wx:simple:guide',row=2)
    async def guide(self,i,b):
        await i.response.send_message(embed=self.ui.mission_guide_embed(),ephemeral=True)
    @discord.ui.button(label='Mi Ficha Registro',emoji='📜',style=discord.ButtonStyle.secondary,custom_id='wx:simple:regcard',row=2)
    async def regcard(self,i,b):
        await self.ui.ctx['show_mine'](i)

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
            legacy_removed=SafeView(timeout=None)
            for cid in ('wx:simple:start','wx:simple:end'):
                btn=discord.ui.Button(label='Misiones',custom_id=cid)
                async def _redir(i):
                    await self.report(i)
                btn.callback=_redir;legacy_removed.add_item(btn)
            bot.add_view(legacy_removed)
            for kind in ('authorization','mission'):
                legacy=SafeView(timeout=None)
                b=discord.ui.Button(label='Abrir',custom_id='worldx:missions:panel:'+kind)
                async def callback(i,kind=kind):
                    await self.report(i)
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

    def _forum_id(self,guild_id):
        fid=self.ms.settings(guild_id).get('simple_forum')
        if fid:return int(fid)
        try:return int(self.system.channel_id('mission'))
        except Exception:return 0

    async def _find_user_thread(self,guild,forum_id,user):
        if not forum_id:return None
        row=self.ms.db.execute(
            "SELECT review_channel FROM mission_reports WHERE guild=? AND owner=? AND review_channel IS NOT NULL AND review_channel!=0 ORDER BY id DESC LIMIT 1",
            (guild.id,user.id)
        ).fetchone()
        if row and row[0]:
            ch=guild.get_thread(int(row[0]))
            if ch and getattr(ch,'parent_id',None)==forum_id:return ch
        forum=guild.get_channel(forum_id)
        if not forum:
            try:forum=await self.bot.fetch_channel(forum_id)
            except discord.HTTPException:return None
        if not isinstance(forum,discord.ForumChannel):return None
        uname=getattr(user,'display_name',getattr(user,'name','')).lower()
        raw_name=getattr(user,'name','').lower()
        for th in getattr(forum,'threads',()):
            if th.owner_id==user.id:return th
            tlow=th.name.lower()
            if uname and (tlow.startswith(uname) or f'{uname} - misiones' in tlow):return th
            if raw_name and (tlow.startswith(raw_name) or f'{raw_name} - misiones' in tlow):return th
        return None

    async def report(self,i,row=None):
        self.system.guild(i)
        await i.response.defer(ephemeral=True)
        fid=self._forum_id(i.guild_id)
        if not fid:
            await i.followup.send('El foro `#report-missions` aún no está configurado por el Staff.',ephemeral=True)
            return
        v=SafeView(timeout=300)
        v.add_item(discord.ui.Button(label='Ir a #report-missions',emoji='📂',style=discord.ButtonStyle.link,url=f'https://discord.com/channels/{i.guild_id}/{fid}'))
        await i.followup.send(
            f'📂 **Reportar misiones**\n'
            f'Dirígete al foro <#{fid}> y sube allí tus capturas en **tu única publicación**.\n'
            f'• No necesitas abrir/cerrar sesión ni llenar formularios.\n'
            f'• El Staff revisará tus capturas directamente en el foro y te acreditará las misiones y Ryo con `/pagar misiones`.',
            view=v,ephemeral=True
        )

    async def reports(self,i,page=0,staff=False):
        self.system.guild(i)
        if not staff:
            await i.response.defer(ephemeral=True)
            fid=self._forum_id(i.guild_id)
            th=await self._find_user_thread(i.guild,fid,i.user) if fid else None
            totals=self.ms.totals(i.guild_id,i.user.id)
            counts_txt=' · '.join(f'**{r}:** {int(totals.get(r,0))}' for r in ('D','C','B','A','S'))
            total_sum=sum(int(totals.get(r,0)) for r in ('D','C','B','A','S'))
            em=discord.Embed(
                title='📋 Mis reportes de misiones',
                description=(
                    (f'Tu publicación en el foro: <#{th.id}>\n' if th else f'Aún no encontramos una publicación tuya en <#{fid}>. Créala una sola vez y sube allí tus capturas.\n' if fid else 'Foro de misiones no configurado.\n')
                    +f'\n**Misiones acreditadas por rango:**\n{counts_txt} · **Total:** {total_sum}'
                ),
                color=0x348F79
            )
            v=SafeView(timeout=300)
            if th:
                v.add_item(discord.ui.Button(label='Ir a mi publicación',emoji='📋',style=discord.ButtonStyle.link,url=f'https://discord.com/channels/{i.guild_id}/{th.id}'))
            elif fid:
                v.add_item(discord.ui.Button(label='Ir a #report-missions',emoji='📂',style=discord.ButtonStyle.link,url=f'https://discord.com/channels/{i.guild_id}/{fid}'))
            await i.followup.send(embed=em,view=v,ephemeral=True)
            return
        self.system.staff(i)
        where="guild=? AND state='pending'";args=[i.guild_id]
        rows=[self.ms.get(r[0]) for r in self.ms.db.execute('SELECT id FROM mission_reports WHERE '+where+' ORDER BY id DESC LIMIT 8 OFFSET ?',(*args,page*8))]
        v=SafeView(timeout=600)
        if rows:
            select=discord.ui.Select(placeholder='Abrir reporte pendiente',options=[discord.SelectOption(label=f'#{r["id"]} · {r["kind"]} · {r["state"]}',value=str(r['id'])) for r in rows])
            async def selected(inter):
                if inter.user.id!=i.user.id:raise ValueError('Abre tu lista.')
                r=self.system.get(inter,int(select.values[0]))
                await self.proofs(inter,r)
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
        class Confirm(SafeModal,title='Confirmar revisión y pago'):
            confirmation=discord.ui.TextInput(label='Revisé todas las pruebas: CONFIRMO',default='CONFIRMO',max_length=8)
            def __init__(self):
                super().__init__()
                self.amount=None
                self.rank_input=None
                self.count_input=None
                self.reason=discord.ui.TextInput(label='Comentario / razón de aprobación',default='Misiones verificadas',max_length=250,required=False)
                if row['kind']=='mission':
                    d=row['data']
                    cur_rank=d.get('mission_rank','C')
                    cur_cnt=max(1,int(d.get('batch_count',1)))
                    lo,hi=ui.flow.limits(row)
                    default_pay=MISSION_PAY_RANGES.get(d.get('pay_key',cur_rank),(None,cur_rank,10000,24999,''))[2]*cur_cnt if d.get('batch_flow') else lo
                    self.rank_input=discord.ui.TextInput(label='Rango a acreditar (D, C, B, A o S)',default=cur_rank,max_length=2,required=True)
                    self.count_input=discord.ui.TextInput(label='Número de misiones a pagar (ej: 5)',default=str(cur_cnt),max_length=3,required=True)
                    self.amount=discord.ui.TextInput(label='Pago total en Ryo (a repartir equitativo)',default=str(default_pay),max_length=12,required=True)
                    self.add_item(self.rank_input)
                    self.add_item(self.count_input)
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
                            try:
                                n=int(str(modal.amount).strip())
                                cnt=max(1,int(str(modal.count_input).strip()))
                            except ValueError:raise ValueError('Escribe el importe y la cantidad de misiones solo con números.')
                            chosen_rank=str(modal.rank_input).strip().upper()
                            if chosen_rank not in ('D','C','B','A','S'):
                                raise ValueError('El rango debe ser D, C, B, A o S.')
                            if row['data'].get('batch_flow'):
                                d_upd=dict(row['data'],mission_rank=chosen_rank,batch_count=cnt)
                                ui.ms.db.execute('UPDATE mission_reports SET data=? WHERE id=?',(json.dumps(d_upd),row['id']))
                            tx,recipients=ui.flow.approve_pay(inter.guild_id,inter.user.id,row['id'],row['revision'],n)
                            text=f'✅ **Misiones aprobadas y pagadas** · Comprobante #{tx} · Rango **{chosen_rank}**.\n'+'\n'.join(f'• <@{u}>: **{ryo(v)}** · **+{cnt} misión(es) Rango {chosen_rank}**' for u,v in recipients.items())
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

    def mission_guide_embed(self):
        e=discord.Embed(
            title='📜 World X · Clasificación y Pagos de Misiones',
            description=(
                'El rango final de la misión se define primero por el **tipo de misión** y se eleva automáticamente según el **rango de los Shinobis enemigos enfrentados** (visibles en el panel de jugadores de la captura).\n\n'
                '🔹 **Misiones RPG (Modo Normal - Rango D):**\n'
                '• **Misiones de estrella (Azules):** Rango D — Paga: **500 – 1.000 Ryo**\n'
                '• **Misiones de estrella (Rojas):** Rango D — Paga: **1.001 – 2.500 Ryo**\n'
                '• **Misiones de pergamino (Verdes / Bandidos o NPCs):** Rango D — Paga: **2.501 – 4.999 Ryo**\n\n'
                '🔸 **Misiones Open World:**\n'
                '• **Rango D — Dificultad Baja (5.000 – 9.999 Ryo):**\n'
                '  ◦ Entregar pergamino / Robar pergamino\n'
                '  ◦ Entregar un cargamento / Robar cargamento\n'
                '• **Rango C — Dificultad Moderada (10.000 – 24.999 Ryo):**\n'
                '  ◦ Proteger cargamento / Destruir un cargamento\n'
                '  ◦ Proteger o escoltar a una persona / Ejecutar o asesinar a una persona\n'
                '• **Rango B — Dificultad Alta (25.000 – 49.999 Ryo):**\n'
                '  ◦ Capturar o secuestrar a un objetivo\n'
                '  ◦ Completar encargos anteriores (D, C, B) enfrentando enemigos **Chunin, Medical Corps o Police Corps**\n'
                '• **Rango A — Dificultad Muy Alta (50.000 – 99.999 Ryo):**\n'
                '  ◦ Completar cualquiera de los objetivos anteriores con oposición **Jounin, ANBU o Jounin Sensei**\n'
                '• **Rango S — Dificultad Extrema (100.000 – 500.000 Ryo):**\n'
                '  ◦ Completar cualquiera de los objetivos anteriores con oposición **Subkage, Sannin, Kage o Akatsuki**\n\n'
                '📌 *El rango y la recompensa requieren validación del Staff. En misiones grupales el pago total se reparte en partes equitativas y suma las misiones completadas del rango correspondiente en tu Tarjeta Ninja y Cartera.*'
            ),
            color=0x348F79
        )
        return e

    def general_panel_embed(self):
        return discord.Embed(
            title='🌐 WORLD X · PANEL GENERAL DE JUGADORES',
            description=(
                'Bienvenido al centro de control de **World X (Open World & RPG)**. Usa los botones inferiores para acceder a todo:\n\n'
                '📂 **Reportar misiones:** Te lleva directo al foro `#report-missions` para subir tus capturas en tu publicación única.\n'
                '📋 **Mis reportes:** Te lleva directo a tu publicación en `#report-missions` y muestra tus misiones acreditadas por rango.\n'
                '🥷 **Mi perfil:** Autoriza tu perfil subiendo solo **2 capturas** (roles de Discord y menú M de Shindo) para poder comprar en la tienda.\n'
                '🏪 **Tienda:** Explora y compra jutsus, armas, modos y permisos según tu Rango Shinobi de Discord.\n'
                '🧾 **Mis compras:** Revisa tus solicitudes de compra, corrige evidencias o cancela reservas.\n'
                '🎒 **Inventario:** Consulta todos tus permisos, técnicas y objetos adquiridos.\n'
                '💰 **Saldo / Cartera:** Revisa tus Ryo disponibles, reservas y movimientos contables.\n'
                '🪪 **Tarjeta Ninja:** Visualiza tu ficha oficial con tu cartera y misiones aprobadas por rango (D, C, B, A, S).\n'
                '📖 **Guía y Pagos de Misiones:** Tabla oficial de rangos de dificultad, enemigos y pagos de misiones.'
            ),
            color=0xC4A76B
        )

    def install(self):
        bot=self.bot;ui=self
        # Sustituye el acceso anterior, manteniendo el comando conocido.
        bot.tree.remove_command('reporte')
        @bot.tree.command(name='reporte',description='Ir al foro #report-missions para reportar tus misiones')
        async def report(i:discord.Interaction):await ui.report(i)

        async def _parse_members(guild,text):
            import re
            ids=[]
            for raw_id in re.findall(r'\d{15,21}',str(text or '')):
                uid=int(raw_id)
                if uid not in ids:ids.append(uid)
            members=[]
            for uid in ids:
                m=guild.get_member(uid)
                if m is None:
                    try:m=await guild.fetch_member(uid)
                    except discord.HTTPException:m=uid
                members.append(m)
            return members

        pagar_group=app_commands.Group(name='pagar',description='Staff: pagar misiones por rango, cantidad, integrantes y paga',guild_only=True)
        @pagar_group.command(name='misiones',description='Staff: pagar misiones completas a cada integrante de la squad (máx. 4)')
        @app_commands.choices(rango=[
            app_commands.Choice(name='Rango D · RPG Estrella Azul (500–1.000 Ryo)',value='rpg_azul'),
            app_commands.Choice(name='Rango D · RPG Estrella Roja (1.001–2.500 Ryo)',value='rpg_roja'),
            app_commands.Choice(name='Rango D · RPG Pergamino Verde / Bandidos (2.501–4.999 Ryo)',value='rpg_verde'),
            app_commands.Choice(name='Rango D · Open World Baja (5.000–9.999 Ryo)',value='D'),
            app_commands.Choice(name='Rango C · Moderada (10.000–24.999 Ryo)',value='C'),
            app_commands.Choice(name='Rango B · Alta (25.000–49.999 Ryo)',value='B'),
            app_commands.Choice(name='Rango A · Muy Alta (50.000–99.999 Ryo)',value='A'),
            app_commands.Choice(name='Rango S · Extrema (100.000–500.000 Ryo)',value='S'),
        ])
        async def pagar_misiones(
            i:discord.Interaction,
            rango:app_commands.Choice[str],
            numero_misiones:app_commands.Range[int,1,100],
            integrantes:str,
            paga:app_commands.Range[int,500,500000],
            motivo:str='Pago de misiones verificado por Staff'
        ):
            ui.system.staff(i)
            await i.response.defer(ephemeral=True)
            player_members=await _parse_members(i.guild,integrantes)
            if not player_members:
                raise ValueError('Menciona al menos un integrante (ejemplo: `@gabo @shinobisiul`, máximo 4).')
            async with ui.eco.lock:
                res=ui.flow.pay_missions_command(
                    i.guild_id,i.user.id,player_members,rango.value,paga,
                    count=numero_misiones,reason=motivo
                )
            calc=res['calc']
            dist_txt='\n'.join(f'• <@{u}>: **{ryo(v)}** (+{res["count"]} misiones Rango **{res["final_rank"]}**)' for u,v in res['recipients'].items())
            embed=discord.Embed(
                title=f'💰 Pago de Misiones Acreditado · Comprobante #{res["tx"]}',
                description=(
                    f'**Clasificación:** {calc["label"]}\n'
                    f'**Rango acreditado:** **Rango {res["final_rank"]}** (Escala por misión: {ryo(calc["min_pay"])}–{ryo(calc["max_pay"])})\n'
                    f'**Misiones pagadas por integrante:** **{res["count"]} misión(es)**\n'
                    f'**Paga por misión:** **{ryo(paga)}** → **{ryo(res["per_player"])} completo a cada integrante**\n\n'
                    f'**Integrantes acreditados:**\n{dist_txt}\n\n'
                    f'*Se actualizaron automáticamente los saldos, la Cartera y la Tarjeta Ninja de cada participante.*'
                ),
                color=0x388E63
            )
            await i.followup.send(embed=embed,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        bot.tree.add_command(pagar_group)

        group=app_commands.Group(name='panel',description='Panel general de misiones, perfil, tienda y cartera',guild_only=True)
        @group.command(name='abrir',description='Abrir el panel general en privado')
        async def home(i:discord.Interaction):
            ui.system.guild(i);await i.response.send_message(embed=ui.general_panel_embed(),view=Home(ui),ephemeral=True)
        @group.command(name='publicar',description='Staff: publicar el panel general permanente en este canal')
        async def publish(i:discord.Interaction):
            ui.system.staff(i,True);await i.response.defer(ephemeral=True)
            await i.channel.send(embed=ui.general_panel_embed(),view=Home(ui))
            await i.followup.send('Panel general publicado.',ephemeral=True)
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
