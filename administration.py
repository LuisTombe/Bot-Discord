"""Staff queries and administrator commands; all responses are private."""
from i18n import format_fragment, t
import asyncio
import json
import sqlite3
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands


def install(bot, ctx):
    def store(): return ctx['STORE']
    def require(i, admin=False):
        if i.guild_id!=ctx['GUILD'] or not isinstance(i.user,discord.Member):
            raise ValueError(t('Usa estos comandos en el servidor configurado.'))
        if admin:
            if not i.user.guild_permissions.manage_guild: raise ValueError(t('Necesitas Gestionar servidor.'))
        elif not ctx['staff'](i): raise ValueError(t('Solo el staff puede usar este comando.'))

    def lookup(user):
        row=store().find(ctx['GUILD'],user.id)
        if not row or row['state']=='deleted': raise ValueError(t('Ese usuario no tiene una ficha activa.'))
        return row

    class Confirmation(ctx['SafeView']):
        def __init__(self,actor,row,action,reason):
            super().__init__(timeout=60)
            self.actor,self.row,self.action,self.reason=actor,row,action,reason
            self.used=False

        async def interaction_check(self,i):
            try:
                require(i,True)
                if i.user.id!=self.actor: raise ValueError(t('La confirmación pertenece a otro administrador.'))
                if self.used: raise ValueError(t('Esta confirmación ya fue utilizada.'))
                return True
            except ValueError as e:
                await ctx['notify_error'](i,e)
                return False

        @discord.ui.button(label='Confirmar operación',style=discord.ButtonStyle.danger)
        async def confirm(self,i,button):
            require(i,True)
            if i.user.id!=self.actor or self.used: raise ValueError(t('Confirmación inválida.'))
            self.used=True
            await i.response.defer(ephemeral=True)
            async with ctx['OPERATIONS']:
                row=store().get(self.row['id'])
                if not row or row['revision']!=self.row['revision']: raise ValueError(t('La ficha cambió. Ejecuta de nuevo el comando.'))
                messages=[]
                if row['review_message']: messages.append([ctx['REVIEW'],row['review_message']])
                if row['public_message']: messages.append([ctx['PUBLIC'],row['public_message']])
                payload={'user':row['user'],'messages':messages,
                         'roles':list(ctx['assigned_roles'](row)) if row['state']=='approved' else []}
                result=store().admin_action(row['id'],row['revision'],i.user.id,self.action,self.reason,payload)
            for child in self.children: child.disabled=True
            await i.edit_original_response(content=''.join([format_fragment(t('Operación guardada para la ficha #'), '', -1), format_fragment(row['id'], '', -1), ': ', format_fragment(t(ctx['STATE_NAMES'][result['state']]), '', -1), format_fragment(t('.\nLa retirada de publicaciones y roles se procesará en unos segundos. Consulta /registro estado.'), '', -1)]),view=self)
            self.stop()

        @discord.ui.button(label='Cancelar',style=discord.ButtonStyle.secondary)
        async def cancel(self,i,button):
            self.used=True
            await i.response.edit_message(content=t('Operación cancelada.'),view=None)
            self.stop()

    group=app_commands.Group(name='registro',description='Administración y pruebas del registro World X',guild_only=True)

    async def request_action(i,user,reason,action):
        require(i,True)
        reason=reason.strip()
        if not reason or len(reason)>180: raise ValueError(t('Escribe un motivo de 1 a 180 caracteres.'))
        row=lookup(user)
        meanings={'reopen':t('Reabrir para edición, conservando datos y capturas.'),
                  'close':t('Cerrar y bloquear la ficha hasta que el staff la reabra.'),
                  'delete':t('Vaciar la ficha activa para comenzar otra. El historial y las capturas archivadas se conservan.')}
        await i.response.send_message(''.join(['**', format_fragment(meanings[action], '', -1), format_fragment(t('**\nUsuario: '), '', -1), format_fragment(ctx['clean'](user), '', -1), ' · ID `', format_fragment(user.id, '', -1), format_fragment(t('`\nFicha #'), '', -1), format_fragment(row['id'], '', -1), ' · ', format_fragment(t(ctx['STATE_NAMES'][row['state']]), '', -1), format_fragment(t('\nMotivo: '), '', -1), format_fragment(ctx['clean'](reason), '', -1), format_fragment(t('\nSe retirarán los mensajes vinculados de revisión/publicación y, si estaba aprobada, los roles de registro configurados. No afecta a otras personas.\nConfirma en 60 segundos.'), '', -1)]),view=Confirmation(i.user.id,row,action,reason),ephemeral=True)

    @group.command(name='reabrir',description='Reabrir una ficha conservando sus datos y capturas (administración)')
    async def reopen(i:discord.Interaction,usuario:discord.User,motivo:str):
        await request_action(i,usuario,motivo,'reopen')

    @group.command(name='cerrar',description='Bloquear una ficha individual hasta reabrirla (administración)')
    async def close(i:discord.Interaction,usuario:discord.User,motivo:str):
        await request_action(i,usuario,motivo,'close')

    @group.command(name='borrar',description='Vaciar la ficha activa de un usuario con confirmación (administración)')
    async def delete(i:discord.Interaction,usuario:discord.User,motivo:str):
        await request_action(i,usuario,motivo,'delete')

    @group.command(name='modo',description='Abrir, cerrar o poner en pruebas las inscripciones (administración)')
    @app_commands.choices(modo=[app_commands.Choice(name=x.capitalize(),value=x) for x in ('abierto','cerrado','pruebas')])
    async def mode(i:discord.Interaction,modo:app_commands.Choice[str]):
        require(i,True)
        store().set_mode(ctx['GUILD'],i.user.id,modo.value)
        await i.response.send_message(''.join([format_fragment(t('Modo guardado: **'), '', -1), format_fragment(modo.value, '', -1), format_fragment(t('**. Persiste al reiniciar. Los paneles existentes aplican este estado al pulsar el botón. Cerrado impide crear/editar/enviar; consultar fichas y revisarlas sigue disponible.'), '', -1)]),ephemeral=True)

    @group.command(name='ver',description='Consultar privadamente una ficha y sus capturas (staff)')
    async def view(i:discord.Interaction,usuario:discord.User):
        require(i)
        row=lookup(usuario)
        await i.response.defer(ephemeral=True)
        # Use the existing attachment builder through an ephemeral followup adapter.
        class Destination:
            async def send(self,**kwargs): return await i.followup.send(**kwargs,ephemeral=True)
        await ctx['send_card'](Destination(),row)

    @group.command(name='listar',description='Listado paginado de fichas (staff)')
    @app_commands.choices(estado=[app_commands.Choice(name=label,value=key) for key,label in ctx['STATE_NAMES'].items()])
    async def listing(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1,estado:app_commands.Choice[str]|None=None):
        require(i)
        rows=[r for r in store().all() if r['guild']==ctx['GUILD'] and (r['state']==estado.value if estado else r['state']!='deleted')]
        pages=max(1,(len(rows)+9)//10)
        if pagina>pages: raise ValueError(''.join([format_fragment(t('Solo hay '), '', -1), format_fragment(pages, '', -1), format_fragment(t(' páginas.'), '', -1)]))
        text='\n'.join(''.join(['#', format_fragment(r['id'], '', -1), ' · ID ', format_fragment(r['user'], '', -1), ' · ', format_fragment(t(ctx['STATE_NAMES'][r['state']]), '', -1), ' · ', format_fragment(ctx['clean'](r['data'].get('roblox_name', t('Sin username'))), '', -1)]) for r in rows[(pagina-1)*10:pagina*10])
        await i.response.send_message(''.join([format_fragment(t('Página '), '', -1), format_fragment(pagina, '', -1), '/', format_fragment(pages, '', -1), ' · ', format_fragment(len(rows), '', -1), format_fragment(t(' fichas\n'), '', -1)])+(text or t('Sin fichas.')),ephemeral=True)

    @group.command(name='estado',description='Consultar modo, fichas y errores de sincronización (staff)')
    async def status(i:discord.Interaction):
        await ctx['diagnostic'].callback(i)

    @group.command(name='historial',description='Últimas 15 acciones de una ficha, sin exponer datos archivados (staff)')
    async def history(i:discord.Interaction,usuario:discord.User):
        require(i)
        row=store().find(ctx['GUILD'],usuario.id)
        if not row: raise ValueError(t('No hay historial de ese usuario.'))
        records=store().history(row['id'])
        text='\n'.join(''.join([format_fragment(r['created'], '', -1), ' UTC · ', format_fragment(r['action'], '', -1), format_fragment(t(' · administrador/usuario '), '', -1), format_fragment(r['actor'], '', -1)]) for r in records)
        await i.response.send_message((text or t('Sin acciones registradas.'))[:1950],ephemeral=True)

    @group.command(name='reintentar',description='Procesar ahora las publicaciones y retiradas pendientes (administración)')
    async def retry(i:discord.Interaction):
        require(i,True)
        await i.response.defer(ephemeral=True)
        await bot.deliver_once()
        await i.followup.send(t('Reintento completado. Consulta /registro estado para ver los errores que continúen.'),ephemeral=True)

    @group.command(name='probar',description='Comprobar canales, permisos, roles y catálogo sin crear fichas (staff)')
    async def test(i:discord.Interaction):
        require(i)
        await i.response.defer(ephemeral=True)
        results=[]
        for name,ident in [(t('Revisión'),ctx['REVIEW']),(t('Publicación'),ctx['PUBLIC'])]:
            try:
                channel=await bot.channel(ident)
                perms=channel.permissions_for(i.guild.me)
                missing=[label for key,label in [('view_channel',t('Ver canal')),('send_messages',t('Enviar mensajes')),('embed_links',t('Insertar enlaces')),('attach_files',t('Adjuntar archivos')),('read_message_history',t('Leer historial'))] if not getattr(perms,key)]
                results.append(name+': '+(t('faltan ')+', '.join(missing) if missing else t('permisos correctos')))
            except (discord.HTTPException,ValueError): results.append(name+t(': canal no accesible o incorrecto'))
        ids={ctx['REGISTERED']}|{int(v.get('role_id',0)) for v in ctx['RULES']['clans'].values()}|{int(v.get('role_id',0)) for v in ctx['LEGACY'].get('clans',{}).values()}
        for ident in sorted(ids-{0}):
            role=i.guild.get_role(ident)
            valid=role and i.guild.me.guild_permissions.manage_roles and not role.managed and not role.is_default() and role<i.guild.me.top_role
            results.append(''.join([format_fragment(t('Rol '), '', -1), format_fragment(ident, '', -1), ': '])+('correcto' if valid else t('revisa existencia, Gestionar roles y jerarquía')))
        results.append(''.join([format_fragment(t('Catálogo: '), '', -1), format_fragment(len(ctx['RULES']['main']), '', -1), format_fragment(t(' principales, '), '', -1), format_fragment(len(ctx['RULES']['secondary']), '', -1), format_fragment(t(' elementales, '), '', -1), format_fragment(len(ctx['RULES']['elements']), '', -1), format_fragment(t(' elementos.'), '', -1)]))
        await i.followup.send('\n'.join(results)[:1950]+t('\nNo se crearon fichas ni se enviaron mensajes a los canales.'),ephemeral=True)

    @group.command(name='respaldo',description='Guardar una copia local de datos y capturas (administración)')
    async def backup(i:discord.Interaction):
        require(i,True)
        await i.response.defer(ephemeral=True)
        root=store().root
        folder=root.parent/'respaldos';folder.mkdir(exist_ok=True)
        name='worldx-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
        dbpath=folder/(name+'.sqlite3');output=folder/(name+'.zip')
        async with ctx['OPERATIONS']:
            dest=sqlite3.connect(dbpath)
            try: store().db.backup(dest)
            finally: dest.close()
            captures=list((root/'capturas').rglob('*'))
            def write():
                with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
                    z.write(dbpath,'data/worldx-v2.sqlite3')
                    for path in captures:
                        if path.is_file(): z.write(path,'data/'+path.relative_to(root).as_posix())
                    for filename in ('catalogo.json','rules.json','misiones_config.json','asignacion_config.json'):
                        path=root.parent/filename
                        if path.exists(): z.write(path,filename)
            try: await asyncio.to_thread(write)
            finally: dbpath.unlink(missing_ok=True)
        await i.followup.send(''.join([format_fragment(t('Respaldo guardado en la carpeta **respaldos/'), '', -1), format_fragment(output.name, '', -1), format_fragment(t('** del equipo que ejecuta el bot. Incluye datos, historial, capturas y catálogos; no incluye el token. No se envía por Discord.'), '', -1)]),ephemeral=True)

    @group.command(name='ayuda',description='Mostrar los comandos y el flujo de pruebas')
    async def help_command(i:discord.Interaction):
        require(i)
        await i.response.send_message(t('**Administración (Gestionar servidor)**\n/registro reabrir · /registro cerrar · /registro borrar\n/registro modo · /registro reintentar · /registro respaldo\n**Staff**\n/registro ver · /registro listar · /registro estado · /registro historial · /registro probar\n**Jugadores**\n/mi_ficha · /capturas_registro\n**Para probar tu cuenta aprobada:** /registro reabrir, confirmar, y después /mi_ficha.\nEn pruebas solo entra el staff. Solo el dueño de la aplicación puede revisar su propia ficha, incluso en pruebas. Las pruebas completas usan registros reales: ciérralos o bórralos al terminar.'),ephemeral=True)

    bot.tree.add_command(group)
