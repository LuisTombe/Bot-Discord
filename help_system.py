"""Permission-filtered, paginated help generated from registered slash commands."""
from i18n import format_fragment, preference_scope, t
import time
from discord_i18n import LocalizedView, command_name, COMMANDS
import discord
from discord import app_commands

PLAYER = {'asignacion equipar','idioma','identidad elegir','identidad ver','nombres listar','asignacion camino','mi_ficha','capturas_registro','help','asignacion iniciar','asignacion estado','asignacion ayuda','asignacion probabilidades',
          'misiones nuevo','misiones continuar','misiones salida','misiones ver','misiones mis_reportes',
          'misiones conteo','misiones actividad','misiones ayuda'}
ADMIN = {'panel_registro','registro reabrir','registro cerrar','registro borrar','registro modo',
         'registro reintentar','registro respaldo','asignacion panel','misiones configurar','misiones panel',
         'misiones borrar'}
OWNER = {'asignacion reiniciar','asignacion configurar','asignar probabilidades'}
LEADER = {'misiones abrir','misiones cerrar'}
PLAYER.update({'tarjeta_ninja','cartera','pagos ver','economia sueldo','inventario','permisos','economia estado','economia acreditar','economia saldo','economia transferir','economia simular','economia historial',
               'tienda ver','tienda articulo','tienda comprar','tienda usar','tienda categorias',
               'mercado ver','mercado ofrecer','mercado oferta','mercado cancelar',
               'ensenanza proponer','ensenanza listar','ensenanza ver','ensenanza evidencia','ensenanza cancelar'})
ECONOMY_ADMIN={'idioma_canal','traducciones articulo','traducciones categoria','traducciones plantilla','identidad corregir','nombres agregar','nombres desactivar','apellidos configurar','apellidos revisar','economia probar','economia panel','economia activar','economia aldea_crear','economia aldea_editar','economia rango','economia ajustar','economia traslado','economia revertir_mision','economia nomina','economia incidencias','economia recompensa','economia auditoria',
               'tienda_admin categoria','tienda_admin crear','tienda_admin editar','tienda_admin opciones','tienda_admin requisito','tienda_admin otorgar','tienda_admin retirar'}
_cooldown = {}


def help_pages(bot, member, guild, ctx):
    owner=member.id==ctx['BOT_OWNER']
    admin=member.guild_permissions.manage_guild
    staff=admin or owner or any(r.id==ctx['REVIEWER'] for r in member.roles)
    leader=ctx['MISSIONS'].store.settings(guild.id).get('leader',guild.owner_id)==member.id
    groups={'🥷 Jugadores':[], '🛡️ Moderación':[], '⚙️ Administración':[], '👑 Dueño / líder':[]}
    for command in sorted(bot.tree.walk_commands(),key=lambda c:c.qualified_name):
        if isinstance(command,app_commands.Group):continue
        name=command.qualified_name
        if name in {'prestamos ofrecer','prestamos ver','prestamos aceptar','prestamos devolver','prestamos cancelar','prestamos listar','actividad ficha','legado reclamar','reportes azul','reportes nueva','reportes ver','reportes lote'}:
            group='🥷 Jugadores'
        elif name in {'tesoreria autoridad','tesoreria guerra'}:
            authority_role=ctx['ECONOMY'].store.policy.ensure(guild.id)['authority'] if hasattr(ctx['ECONOMY'].store,'policy') else 0
            if not owner and authority_role not in [r.id for r in member.roles]:continue
            group='👑 Dueño / líder'
        elif name.startswith('eventos ') or name in {'prestamos aldeas','tesoreria estado','reportes clasificar','reportes resolver'}:
            roles={r.id for r in member.roles}
            if not (staff or any(v['role'] in roles and v['kage_role'] in roles for v in ctx['ECONOMY'].store.villages(guild.id))):continue
            group='👑 Dueño / líder'
        elif name in {'actividad excepcion','personaje permadeath','legado clasificar','reportes plantilla'}:
            if not (owner or admin):continue
            group='⚙️ Administración'
        elif name in ECONOMY_ADMIN:
            if not (owner or admin):continue
            group='⚙️ Administración'
        elif name in ('economia mision','pagos mision'):
            roles={r.id for r in member.roles}
            if not (owner or admin or any(v['role'] in roles and v['kage_role'] in roles for v in ctx['ECONOMY'].store.villages(guild.id))):continue
            group='👑 Dueño / líder'
        elif name in PLAYER:group='🥷 Jugadores'
        elif name in OWNER:
            if not owner:continue
            group='👑 Dueño / líder'
        elif name in LEADER:
            if not leader:continue
            group='👑 Dueño / líder'
        elif name=='misiones lider':
            if member.id!=guild.owner_id:continue
            group='👑 Dueño / líder'
        elif name in ADMIN:
            if not admin and not (owner and name=='asignacion panel'):continue
            group='⚙️ Administración'
        else:
            if not staff:continue
            group='🛡️ Moderación'
        groups[group].append(''.join(['**/', format_fragment(command_name(name), '', -1), '** — ', format_fragment(t(command.description), '', -1)]))
    pages=[]
    for group,lines in groups.items():
        for offset in range(0,len(lines),9):
            e=discord.Embed(title=t('🍃 World X · Guía de comandos'),color=0xE89436,
                description=t(group)+'\n\n'+'\n'.join(lines[offset:offset+9]))
            pages.append(e)
    for n,e in enumerate(pages):e.set_footer(text=t('help.footer', page=n+1, total=len(pages)))
    return pages


def help_view(pages, actor):
    class Pages(LocalizedView):
        def __init__(self):
            super().__init__(timeout=600);self.page=0
            self.previous.disabled=True;self.next.disabled=len(pages)<2
        async def interaction_check(self,i):
            if i.user.id!=actor:
                await i.response.send_message(t('Abre tu propia ayuda con /help.'),ephemeral=True);return False
            return True
        async def move(self,i,delta):
            self.page=max(0,min(len(pages)-1,self.page+delta))
            self.previous.disabled=self.page==0;self.next.disabled=self.page==len(pages)-1
            await i.response.edit_message(embed=pages[self.page],view=self)
        @discord.ui.button(label='Anterior')
        async def previous(self,i,button):await self.move(i,-1)
        @discord.ui.button(label='Siguiente')
        async def next(self,i,button):await self.move(i,1)
    return Pages()


def install_help(bot,ctx):
    @bot.tree.command(name='help',description='Mostrar los comandos disponibles y para qué sirve cada uno')
    @app_commands.guild_only()
    async def help_command(i:discord.Interaction):
        if i.guild_id!=ctx['GUILD']:raise ValueError(t('Usa el servidor configurado.'))
        pages=help_pages(bot,i.user,i.guild,ctx)
        await i.response.send_message(embed=pages[0],view=help_view(pages,i.user.id),ephemeral=True)


async def send_prefix_help(bot,message,ctx):
    now=time.monotonic();key=(message.guild.id,message.author.id)
    if now-_cooldown.get(key,0)<10:return
    _cooldown[key]=now
    if len(_cooldown)>5000:
        for old in list(_cooldown):
            if now-_cooldown[old]>60:del _cooldown[old]
    with preference_scope(message.guild.id, message.author.id):
        pages=help_pages(bot,message.author,message.guild,ctx)
        view=help_view(pages,message.author.id)
    try:
        await message.reply(embed=pages[0],view=view,mention_author=False)
    except discord.HTTPException:
        pass
