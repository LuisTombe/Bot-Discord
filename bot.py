"""World X registration bot. Run a single process against the SQLite database."""
from i18n import bilingual, channel_scope, format_fragment, preference_scope, t
import sqlite3
import asyncio
import json
import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv
from discord_i18n import LocalizedTree, LocalizedView, LocalizedModal, Translator, public_output, install_channel_language
from panels_i18n import panel_embeds

from evidence import KINDS, TITLES, INSTRUCTIONS, receive, discard, safe_path
from domain import Store, SPECIALTIES, EDITABLE, change, eligible, load_rules, capacities, available_clans, merge_page_selection

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env')
RULES = load_rules(BASE / 'catalogo.json')
LEGACY = json.loads((BASE/'rules.json').read_text(encoding='utf-8')) if (BASE/'rules.json').exists() else {}
GUILD = int(os.getenv('GUILD_ID', '0'))
REVIEW = int(os.getenv('REVIEW_CHANNEL_ID', '0'))
PUBLIC = int(os.getenv('PUBLIC_CHANNEL_ID', '0'))
REVIEWER = int(os.getenv('REVIEWER_ROLE_ID', '0'))
REGISTERED = int(os.getenv('REGISTERED_ROLE_ID', '0'))
BOT_OWNER = int(os.getenv('BOT_OWNER_ID','0'))
DEMO = os.getenv('REGISTRATION_TEST_MODE','true').lower() == 'true'
STATE_NAMES = {'draft':'Borrador', 'pending':'Pendiente de revisión', 'approved':'Aprobada',
               'closed':'Cerrada por el staff', 'deleted':'Eliminada', 'correction':'Requiere corrección', 'rejected':'Rechazada (puedes corregir y reenviar)'}
STORE = Store(BASE / 'data' / 'worldx-v2.sqlite3')
log = logging.getLogger('worldx')
OPERATIONS = asyncio.Lock()

def current_mode(): return STORE.mode(GUILD,'pruebas' if DEMO else 'abierto')

def is_demo(): return current_mode()=='pruebas'


def owner(i):
    return i.guild_id == GUILD and BOT_OWNER != 0 and i.user.id == BOT_OWNER

def can_review_own(i, row):
    return i.user.id != row['user'] or owner(i)

def staff(i):
    return i.guild_id == GUILD and isinstance(i.user, discord.Member) and (
        owner(i) or i.user.guild_permissions.manage_guild or any(r.id == REVIEWER for r in i.user.roles))


def clean(value):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))[:200]


def label(kind, key):
    return RULES.get(kind, {}).get(key, {}).get('label', LEGACY.get(kind,{}).get(key,{}).get('label',key or t('Pendiente')))


def embed(row):
    d = row['data']
    result = discord.Embed(title=t('registration.title', id=row['id']), color=0x7759B8,
                           description=(t('registration.testing') if is_demo() else '')
                           + t('registration.state') + t(STATE_NAMES[row['state']]))
    fields = [(t('Nombre del personaje'),d.get('character_name',t('Pendiente de identidad'))),(t('Usuario Discord escrito'),d.get('discord_name',t('Pendiente'))),
              (t('ID Discord del solicitante'),row['user']),
              ('Username Roblox',d.get('roblox_name',t('Pendiente'))),
              ('Displayname',d.get('display_name',t('Pendiente'))),
              (t('Edad del personaje'),d.get('age',t('Pendiente'))),
              (t('Género del personaje'),d.get('gender',t('Pendiente'))),
              (t('Elementos'),', '.join(t(RULES['elements'].get(e,e)) for e in d.get('elements',[])) or (t('v8.empty.elements') if d.get('assignment_version')==80 else t('Pendiente'))),
              (t('Bloodline principal / clan'),', '.join(label('main',m) for m in d.get('mains',[])) or label('main',d.get('main'))),
              (t('Bloodlines elementales'),', '.join(label('secondary',k) for k in d.get('secondary',[])) or t('Ninguna')),
              (t('Especialidad'),d.get('specialty',t('Pendiente')))]
    fields.append((t('Asignación'),t('🥷 Guardada · sistema de nacimiento') if d.get('assignment_complete') else (t('Pendiente · /asignacion iniciar') if d.get('catalog_version')==5 else t('Ficha anterior conservada'))))
    if d.get('birth_village'):
        from birth_v71 import PATHS
        fields.append((t('Aldea de origen'),d['birth_village'].title()))
        fields.append((t('Camino'),t(PATHS.get(d.get('birth_path'),'Shinobi'))))
        if d.get('samurai_eligible'):fields.append((t('Camino opcional'),t('Elegible para samurái/respiraciones · /asignacion camino')))
    if d.get('assignment_version')==80 and d.get('birth_type')=='mecha':fields.append((t('v8.mecha'),t('v8.mecha_notice')))
    elif d.get('chakra_usable') is False:fields.append((t('Camino de Lee'),t('Sin uso de elementos ni bloodlines secundarios')))
    if len(d.get('element_access',[]))==5:fields.append((t('v8.entitlements'),t('v8.all_elements')))
    if d.get('secondary_access'):fields.append((t('v8.secondary_access'),t('v8.secondary_access_note')))
    if d.get('element_draws'):fields.append((t('Afinidades del sorteo'),', '.join(t(RULES['elements'].get(e,e)) for e in d['element_draws'])))
    fields.append((t('Capturas del personaje'),t('registration.images', count=len(row.get('screenshots',[])))))
    for name, value in fields:
        result.add_field(name=name,value=clean(value),inline=True)
    if row['reason']:
        result.add_field(name=t('Respuesta del staff'),value=clean(row['reason']),inline=False)
    if row['delivery_error']:
        result.add_field(name=t('Publicación / roles'),value=t('Sincronización pendiente. El staff puede usar /diagnostico_registro.'),inline=False)
    result.set_footer(text=t('registration.footer', revision=row['revision']))
    return result


async def notify_error(i, error):
    message = t(str(error)) if isinstance(error, ValueError) else t('No se pudo completar la operación. Usa /mi_ficha y avisa al staff si continúa.')
    if not isinstance(error, ValueError):
        log.error('Interaction failed: %s', type(error).__name__)
    if i.response.is_done():
        await i.followup.send(message, ephemeral=True)
    else:
        await i.response.send_message(message, ephemeral=True)


class SafeView(LocalizedView):
    async def on_error(self, interaction, error, item):
        await notify_error(interaction, error)


class SafeModal(LocalizedModal):
    async def on_error(self, interaction, error):
        await notify_error(interaction, error)


class Identity(SafeModal, title='Datos del personaje'):
    def __init__(self, row):
        super().__init__(timeout=600)
        self.row = row
        specs = [('discord_name','Username Discord',80),('roblox_name',t('Username Roblox (sin @)'),20),
                 ('display_name','Displayname',80),('age',t('Edad del personaje (no edad real)'),3),
                 ('gender',t('Género del personaje'),40)]
        self.inputs = {}
        for key, name, length in specs:
            field = discord.ui.TextInput(label=name,max_length=length,default=row['data'].get(key,''))
            self.inputs[key] = field
            self.add_item(field)

    async def on_submit(self, i):
        await access(i)
        if i.user.id != self.row['user']:
            raise ValueError(t('Esta ficha no te pertenece.'))
        data = dict(self.row['data'])
        data.update({k:str(v.value).strip() for k,v in self.inputs.items()})
        row = STORE.edit(self.row['id'],self.row['revision'],i.user.id,data)
        await i.response.send_message(embed=embed(row),view=Wizard(row),ephemeral=True)


async def access(i):
    if i.guild_id != GUILD:
        raise ValueError(t('Utiliza el bot en el servidor configurado.'))
    mode=current_mode()
    if mode=='cerrado': raise ValueError(t('Las inscripciones están cerradas temporalmente por el staff.'))
    if mode=='pruebas' and not staff(i):
        raise ValueError(t('El registro está en pruebas privadas del staff; aún no está abierto.'))


class Wizard(SafeView):
    def __init__(self, row, choosing=None, page=0):
        super().__init__(timeout=900)
        self.row=row
        d=row['data']; birth=row['birth']
        identity=discord.ui.Button(label=t('Datos del personaje'),emoji='📜',row=0)
        async def edit_identity(i):await i.response.send_modal(Identity(self.row))
        identity.callback=edit_identity;self.add_item(identity)
        assignment=discord.ui.Button(label=t('Asignar elementos y bloodlines'),emoji='🌀',row=0)
        async def open_assignment(i):await ASSIGNMENTS.show(i)
        assignment.callback=open_assignment;self.add_item(assignment)
        naming=discord.ui.Button(label=t('Elegir nombre y apellido'),emoji='🥷',row=2)
        async def open_naming(i):await IDENTITIES.show(i)
        naming.callback=open_naming;self.add_item(naming)
        select=discord.ui.Select(placeholder=t('Elige tu especialidad'),row=1,
            options=[discord.SelectOption(label=x,value=x,default=x==d.get('specialty')) for x in SPECIALTIES])
        async def specialty(i):
            data=dict(d);data['specialty']=select.values[0]
            fresh=STORE.edit(row['id'],row['revision'],i.user.id,data)
            await i.response.edit_message(embed=embed(fresh),view=Wizard(fresh))
        select.callback=specialty;self.add_item(select)
        capture=discord.ui.Button(label=t('Capturas e instrucciones'),emoji='📷',style=discord.ButtonStyle.secondary,row=4)
        async def captured(i):
            await show_capture_guide(i,self.row)
        capture.callback=captured
        self.add_item(capture)
        submit=discord.ui.Button(label=t('Enviar al staff'),emoji='📨',style=discord.ButtonStyle.success,row=4)
        async def submitted(i):
            fresh=STORE.submit(row['id'],row['revision'],i.user.id,RULES)
            await i.response.edit_message(content=t('Ficha enviada. Consulta /mi_ficha para ver la decisión.'),embed=embed(fresh),view=None)
        submit.callback=submitted
        self.add_item(submit)

    async def interaction_check(self,i):
        try:
            await access(i)
            fresh=STORE.get(self.row['id'])
            if i.user.id!=self.row['user']: raise ValueError(t('Esta ficha no te pertenece.'))
            if not fresh or fresh['revision']!=self.row['revision'] or fresh['state'] not in EDITABLE:
                raise ValueError(t('Formulario desactualizado. Abre /mi_ficha para continuar.'))
            return True
        except ValueError as error:
            await notify_error(i,error)
            return False


async def check_capture_access(i,row):
    await access(i)
    fresh=STORE.get(row['id'])
    if i.user.id!=row['user'] or not fresh or fresh['user']!=i.user.id:
        raise ValueError(t('Esta ficha no te pertenece.'))
    if fresh['state'] not in EDITABLE or fresh['revision']!=row['revision']:
        raise ValueError(t('La ficha cambió o ya está enviada. Abre /mi_ficha para continuar.'))


class CaptureModal(SafeModal,title='Capturas del personaje'):
    def __init__(self,row):
        super().__init__(timeout=900)
        self.row=row
        self.uploads={}
        descriptions={'personaje':t('Personaje completo de Shindo: apariencia y nivel visibles.'),
                      'username':t('Username real de Roblox (@usuario), no solo Displayname.'),
                      'estadisticas':t('Stats, edad y slots de bloodlines y elementos; reúne las vistas si hace falta.')}
        for kind in KINDS:
            upload=discord.ui.FileUpload(custom_id='capture:'+kind,required=True,min_values=1,max_values=1)
            self.uploads[kind]=upload
            self.add_item(discord.ui.Label(text=t(TITLES[kind]),description=descriptions[kind],component=upload))

    async def on_submit(self,i):
        await check_capture_access(i,self.row)
        if any(len(u.values)!=1 for u in self.uploads.values()):
            raise ValueError(t('Adjunta una imagen en cada uno de los tres campos.'))
        await i.response.defer(ephemeral=True,thinking=True)
        entries=[]
        committed=False
        try:
            entries=await asyncio.wait_for(receive({k:u.values[0] for k,u in self.uploads.items()},STORE.root),timeout=120)
            fresh=STORE.save_screenshots(self.row['id'],self.row['revision'],i.user.id,entries)
            committed=True
            await i.followup.send(t('Las tres capturas quedaron guardadas. Revisa los datos y pulsa Enviar al staff.'),embed=embed(fresh),view=Wizard(fresh),ephemeral=True)
        except TimeoutError:
            raise ValueError(t('La subida tardó demasiado. Abre /mi_ficha y vuelve a intentarlo.'))
        finally:
            if entries and not committed:
                discard(entries,STORE.root)


class CaptureGuide(SafeView):
    def __init__(self,row):
        super().__init__(timeout=900)
        self.row=row

    async def interaction_check(self,i):
        try:
            await check_capture_access(i,self.row)
            return True
        except ValueError as error:
            await notify_error(i,error)
            return False

    @discord.ui.button(label='Subir las 3 capturas',emoji='📷',style=discord.ButtonStyle.primary)
    async def upload(self,i,button):
        await i.response.send_modal(CaptureModal(self.row))


async def show_capture_guide(i,row):
    await check_capture_access(i,row)
    guide=discord.Embed(title=t('📷 Antes de subir tus capturas'),description=t(INSTRUCTIONS),color=0x7759B8)
    await i.response.send_message(embed=guide,view=CaptureGuide(row),ephemeral=True)


@public_output('channel')
async def send_card(channel,row,view=None):
    """Upload local copies, preserving evidence beyond temporary attachment URLs."""
    files=[]
    embeds=[embed(row)]
    try:
        entries={e['kind']:e for e in row.get('screenshots',[])}
        for kind in KINDS:
            if kind not in entries: continue  # Preserve already-approved legacy cards.
            e=entries[kind]
            path=safe_path(STORE.root,e['path'])
            filename=kind+path.suffix
            files.append(discord.File(path,filename=filename,description=t(TITLES[kind])))
            picture=discord.Embed(title=t(TITLES[kind]),color=0x7759B8)
            picture.set_image(url='attachment://'+filename)
            embeds.append(picture)
        return await channel.send(embeds=embeds,files=files,view=view)
    finally:
        for file in files: file.close()


async def show_mine(i):
    if i.guild_id!=GUILD: raise ValueError(t('Utiliza el servidor configurado.'))
    row=STORE.find(GUILD,i.user.id)
    if not row or row['state']=='deleted':
        await access(i)
        row=STORE.start(GUILD,i.user.id,RULES)
    await i.response.send_message(embed=embed(row),view=Wizard(row) if row['state'] in EDITABLE else None,ephemeral=True)


class Panel(SafeView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label=bilingual('Crear / continuar mi ficha'),emoji='📜',style=discord.ButtonStyle.primary,custom_id='worldx:registration:start:v1')
    async def start(self, i, button):
        await show_mine(i)


class Decision(SafeModal, title='Revisión de ficha'):
    reason = discord.ui.TextInput(label='Motivo o indicaciones',style=discord.TextStyle.paragraph,max_length=180)
    def __init__(self, row, decision, message):
        super().__init__(timeout=600)
        self.row,self.decision,self.message = row,decision,message

    async def on_submit(self, i):
        if not staff(i):
            raise ValueError(t('Solo el staff puede revisar fichas.'))
        if not can_review_own(i,self.row):
            raise ValueError(t('Solo el dueño de la aplicación puede revisar su propia ficha. Pide a otro revisor que revise la tuya.'))
        async with OPERATIONS:
            row = STORE.decide(self.row['id'],self.row['revision'],i.user.id,self.decision,str(self.reason.value))
        await i.response.send_message(t('Decisión guardada. El jugador puede consultarla en /mi_ficha.'),ephemeral=True)
        try:
            with channel_scope(GUILD,self.message.channel.id):
                await self.message.edit(embeds=[embed(row),*self.message.embeds[1:]],view=None)
        except discord.HTTPException:
            pass
        try:
            user = await i.client.fetch_user(row['user'])
            with preference_scope(GUILD,row['user']):
                await user.send(''.join([format_fragment(t('World X · Ficha #'), '', -1), format_fragment(row['id'], '', -1), ': ', format_fragment(t(STATE_NAMES[row['state']]), '', -1), '.\n', format_fragment(clean(row['reason']), '', -1), format_fragment(t('\nConsulta /mi_ficha en el servidor.'), '', -1)]))
        except discord.HTTPException:
            pass  # DMs can be disabled; /mi_ficha remains authoritative.


class Review(SafeView):
    def __init__(self,row):
        super().__init__(timeout=None)
        for name,state,style in [(t('Aprobar'),'approved',discord.ButtonStyle.success),
                                 (t('Solicitar corrección'),'correction',discord.ButtonStyle.secondary),
                                 (t('Rechazar'),'rejected',discord.ButtonStyle.danger)]:
            button = discord.ui.Button(label=name,style=style,custom_id=f"worldx:review:{row['id']}:{row['revision']}:{state}")
            async def callback(i, decision=state):
                if not staff(i):
                    raise ValueError(t('Solo el staff puede revisar fichas.'))
                fresh = STORE.get(row['id'])
                if not fresh or fresh['state'] != 'pending' or fresh['revision'] != row['revision']:
                    raise ValueError(t('Esta revisión ya fue resuelta o reemplazada.'))
                await i.response.send_modal(Decision(row,decision,i.message))
            button.callback = callback
            self.add_item(button)


def assigned_roles(row):
    if row['data'].get('catalog_version')==5:return {REGISTERED}-{0}
    clan=row['data'].get('clan')
    legacy_role=LEGACY.get('clans',{}).get(clan,{}).get('role_id',0)
    return {REGISTERED,int(RULES['clans'].get(clan,{}).get('role_id',0) or legacy_role)}-{0}


class WorldX(discord.Client):
    def __init__(self):
        intents=discord.Intents.default()
        intents.message_content=os.getenv('ENABLE_PREFIX_HELP','true').lower()=='true'
        super().__init__(intents=intents,allowed_mentions=discord.AllowedMentions.none())
        self.tree = LocalizedTree(self)

    async def setup_hook(self):
        global BOT_OWNER
        if not BOT_OWNER:
            app=await self.application_info()
            BOT_OWNER=app.team.owner_id if app.team else app.owner.id
        self.add_view(Panel())
        ASSIGNMENTS.restore()
        MISSIONS.restore()
        ECONOMY.restore()
        MARKET.restore()
        REWARDS.restore()
        for row in STORE.all():
            if row['state'] == 'pending' and row['review_message']:
                self.add_view(Review(row),message_id=row['review_message'])
        await self.tree.set_translator(Translator())
        self.tree.copy_global_to(guild=discord.Object(id=GUILD))
        await self.tree.sync(guild=discord.Object(id=GUILD))
        self.deliver.start()

    async def on_interaction(self, interaction):
        await REWARDS._fallback_interaction(interaction)

    async def on_message(self,message):
        if message.author.bot or not message.guild or message.guild.id!=GUILD:return
        if message.content.strip().lower()=='!help':
            from help_system import send_prefix_help
            await send_prefix_help(self,message,globals())

    async def channel(self, ident):
        channel = self.get_channel(ident) or await self.fetch_channel(ident)
        if not isinstance(channel,(discord.TextChannel,discord.ForumChannel,discord.Thread)) or channel.guild.id != GUILD:
            raise ValueError(t('Configura un canal de texto del servidor correcto.'))
        return channel

    async def process_cleanup(self):
        for job in STORE.cleanup():
            payload=json.loads(job['payload'])
            try:
                for channel_id,message_id in payload.get('messages',[]):
                    try:
                        channel=await self.channel(channel_id)
                        await channel.get_partial_message(message_id).delete()
                    except discord.NotFound: pass
                if payload.get('roles'):
                    guild=self.get_guild(GUILD)
                    if guild is None: raise ValueError(t('Servidor no disponible.'))
                    try: member=await guild.fetch_member(payload['user'])
                    except discord.NotFound: member=None
                    if member:
                        for role_id in payload['roles']:
                            role=guild.get_role(role_id)
                            if role and role in member.roles:
                                if role.is_default() or role.managed or role>=guild.me.top_role:
                                    raise ValueError(t('No se puede retirar un rol: revisa la jerarquía.'))
                                await member.remove_roles(role,reason=t('Ficha World X reabierta, cerrada o eliminada'))
                STORE.cleanup_done(job['id'])
            except (discord.HTTPException,ValueError,OSError) as error:
                STORE.cleanup_error(job['id'],str(error))

    async def deliver_once(self):
        async with OPERATIONS:
            await self.process_cleanup()
            for row in STORE.all():
                if row['guild']!=GUILD or STORE.cleanup(row['id']): continue
                try:
                    if row['state']=='pending' and not row['review_message']:
                        channel=await self.channel(REVIEW)
                        msg=await send_card(channel,row,Review(row))
                        STORE.mark(row['id'],review_message=msg.id,delivery_error='')
                    if row['state']=='approved':
                        if not row['public_message']:
                            channel=await self.channel(PUBLIC)
                            msg=await send_card(channel,row)
                            STORE.mark(row['id'],public_message=msg.id,delivery_error='')
                        dirty=STORE.db.execute('SELECT revision FROM identity_public_updates WHERE registration=?',(row['id'],)).fetchone()
                        current=STORE.get(row['id'])
                        if dirty and current['public_message']:
                            channel=await self.channel(PUBLIC)
                            message=await channel.fetch_message(current['public_message'])
                            with channel_scope(GUILD,channel.id):
                                await message.edit(embeds=[embed(current),*message.embeds[1:]])
                            with STORE.db:STORE.db.execute('DELETE FROM identity_public_updates WHERE registration=? AND revision=?',(row['id'],dirty[0]))
                        if not row['roles_done']:
                            guild=self.get_guild(GUILD)
                            if guild is None or guild.me is None: raise ValueError(t('Servidor no disponible.'))
                            member=await guild.fetch_member(row['user'])
                            for ident in assigned_roles(row):
                                role=guild.get_role(ident)
                                if role is None or role.is_default() or role.managed or role>=guild.me.top_role:
                                    raise ValueError(t('Rol no encontrado o jerarquía insuficiente.'))
                                await member.add_roles(role,reason=''.join([format_fragment(t('Registro World X #'), '', -1), format_fragment(row['id'], '', -1)]))
                            STORE.mark(row['id'],roles_done=1,delivery_error='')
                except (discord.HTTPException,ValueError,OSError) as error:
                    STORE.mark(row['id'],delivery_error=f'{type(error).__name__}: {str(error)[:180]}')
                    log.warning('Delivery pending for registration %s: %s',row['id'],type(error).__name__)

    @tasks.loop(seconds=20)
    async def deliver(self):
        await self.deliver_once()
        await MISSIONS.deliver()
        await ECONOMY.tick()
        from maintenance_v7 import backup
        try:await asyncio.to_thread(backup,STORE,BASE)
        except (OSError,sqlite3.Error) as error:log.error(t("Respaldo automático falló: %s"),error)

    @deliver.before_loop
    async def ready(self):
        await self.wait_until_ready()

    async def close(self):
        self.deliver.cancel()
        if REWARDS.task: REWARDS.task.cancel()
        await super().close()


bot = WorldX()


@bot.tree.command(name='mi_ficha',description='Crear, continuar o consultar mi ficha de World X')
@app_commands.guild_only()
async def my_card(i: discord.Interaction):
    await show_mine(i)


@bot.tree.command(name='capturas_registro',description='Leer instrucciones y adjuntar las tres capturas de mi ficha')
@app_commands.guild_only()
async def capture_command(i: discord.Interaction):
    await access(i)
    row=STORE.start(GUILD,i.user.id,RULES)
    if row['state'] not in EDITABLE:
        raise ValueError(t('Solo puedes adjuntar capturas a un borrador o una ficha devuelta para corregir. Las aprobadas anteriormente se conservan.'))
    await show_capture_guide(i,row)


@bot.tree.command(name='panel_registro',description='Publicar el panel de registro en este canal (administración)')
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def panel(i: discord.Interaction):
    if i.guild_id != GUILD or not i.user.guild_permissions.manage_guild:
        raise ValueError(t('Necesitas Gestionar servidor.'))
    if not isinstance(i.channel,discord.TextChannel):
        raise ValueError(t('Publica el panel en un canal de texto.'))
    await i.response.defer(ephemeral=True)
    for ident in (REVIEW,PUBLIC):
        channel = await bot.channel(ident)
        perms = channel.permissions_for(i.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links and perms.attach_files):
            raise ValueError(t('Faltan permisos Ver canal / Enviar mensajes / Insertar enlaces / Adjuntar archivos en los canales destino.'))
    e = discord.Embed(title=t('📜 World X · Registro Shinobi'),color=0x7759B8,
         description=''.join([format_fragment(t('**Estado al publicar este panel: '), '', -1), format_fragment(current_mode(), '', -1), format_fragment(t('**. El botón consulta el estado actual.\n\n'), '', -1)])+
         t('Crea tu personaje mediante el botón inferior. Completa tus datos y especialidad; después abre la asignación de nacimiento.\n\n'
         '**Una ficha activa por cuenta de Discord.** El staff revisará tu solicitud antes de publicarla.\n'
         '🌀 Elementos → Principal → Secundarios. Tres giros totales por sección. Cada giro reemplaza el anterior; confirmar es definitivo. Después del tercer giro revisa el resultado y pulsa Confirmar y continuar. Los resultados se guardan en tu ficha.\n'
         'Debes adjuntar tres capturas: personaje y nivel, username Roblox, y menú con stats, slots y edad. El formulario te dará las instrucciones.\n'
         'Edad y género se refieren al personaje. Puedes retomar el proceso con /mi_ficha.'))
    await i.channel.send(embeds=panel_embeds('registration'),view=Panel())
    await i.followup.send(t('Panel publicado.'),ephemeral=True)


@bot.tree.command(name='diagnostico_registro',description='Ver fichas pendientes y errores de entrega (staff)')
@app_commands.guild_only()
async def diagnostic(i: discord.Interaction):
    if not staff(i):
        raise ValueError(t('Solo el staff puede consultar el diagnóstico.'))
    rows = [r for r in STORE.all() if r['guild']==GUILD and r['state']!='deleted']
    errors = [f"#{r['id']}: {clean(r['delivery_error'])}" for r in rows if r['delivery_error']]
    text = ''.join([format_fragment(t('Modo: '), '', -1), format_fragment(current_mode(), '', -1), format_fragment(t('\nFichas: '), '', -1), format_fragment(len(rows), '', -1), format_fragment(t('\nPendientes: '), '', -1), format_fragment(sum((r['state'] == 'pending' for r in rows)), '', -1)])
    jobs=STORE.cleanup()
    text += ''.join([format_fragment(t('\nRetiradas pendientes de mensajes/roles: '), '', -1), format_fragment(len(jobs), '', -1)])
    text += ''.join(''.join([format_fragment(t('\nFicha #'), '', -1), format_fragment(job['registration'], '', -1), ': ', format_fragment(clean(job['error']), '', -1)]) for job in jobs[:4] if job['error'])
    text += '\n' + ('\n'.join(errors[:7]) if errors else t('Sin errores de entrega registrados.'))
    await i.response.send_message(text,ephemeral=True)


from assignment_ui import AssignmentSystem
ASSIGNMENTS=AssignmentSystem(bot,globals())
from identity_ui import IdentitySystem
IDENTITIES=IdentitySystem(bot,globals())
from help_system import install_help
install_help(bot,globals())
from administration import install
install(bot, globals())
from missions import MissionSystem
MISSIONS = MissionSystem(bot, globals())
from economy_ui import EconomySystem
ECONOMY = EconomySystem(bot, globals())
from v7_ui import V7System
V7 = V7System(bot, globals())
from discord_i18n import install as install_languages
LANGUAGES = install_languages(bot, globals())
from content_i18n import ContentTranslations
TRANSLATIONS = ContentTranslations(bot, globals())
install_channel_language(bot, globals())
from wx_market.ui import MarketSystem
MARKET = MarketSystem(bot, globals())
from wx_rewards.ui import RewardsSystem
REWARDS = RewardsSystem(bot, globals())
from simple_flow_ui import SimpleUI
SIMPLE = SimpleUI(bot, globals())


@bot.tree.error
async def command_error(i,error):
    await notify_error(i,getattr(error,'original',error))


if __name__ == '__main__':
    token = os.getenv('DISCORD_TOKEN','')
    if not token or token.startswith('PEGA_') or not all((GUILD,REVIEW,PUBLIC)):
        raise SystemExit('Configura .env: token, GUILD_ID, REVIEW_CHANNEL_ID y PUBLIC_CHANNEL_ID. Lee LEEME.md.')
    logging.basicConfig(level=logging.INFO)
    try:bot.run(token)
    except discord.PrivilegedIntentsRequired:
        raise SystemExit('Para !help activa Message Content Intent en Developer Portal → Bot. O añade ENABLE_PREFIX_HELP=false a .env y usa /help.')
