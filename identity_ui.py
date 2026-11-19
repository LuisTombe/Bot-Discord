"""Discord identity picker and audited catalogue administration."""
from i18n import format_fragment, t
import io
import json
import secrets
from discord_i18n import LocalizedModal
import discord
from discord import app_commands
from identity_domain import IdentityService, normalized

class IdentitySystem:
    def __init__(self,bot,ctx):
        self.bot,self.ctx=bot,ctx
        self.service=IdentityService(ctx['STORE'],ctx['BASE']/'identidad_catalogo.json')
        self.install()

    def require(self,i,admin=False):
        if i.guild_id!=self.ctx['GUILD']:raise ValueError(t('Usa el servidor configurado.'))
        if admin and not (self.ctx['owner'](i) or i.user.guild_permissions.manage_guild):
            raise ValueError(t('Solo el dueño o quien tenga Gestionar servidor.'))

    async def show(self,i):
        self.require(i)
        row=self.ctx['STORE'].find(i.guild_id,i.user.id)
        if not row or row['state']!='approved':await self.ctx['access'](i)
        row=self.service.prepare(i.guild_id,i.user.id)
        if self.service.valid(row['data']):
            await i.response.send_message(t('🥷 Tu identidad es **')+row['data']['character_name']+t('**. Para corregirla, solicita ayuda al administrador.'),ephemeral=True);return
        view=self.view(row)
        await i.response.send_message(embed=view.card(),view=view,ephemeral=True)

    def view(self,row,category='',initial='',query='',page=0,selected=None):
        system=self
        class Picker(self.ctx['SafeView']):
            def __init__(self):
                super().__init__(timeout=900)
                self.items=system.service.names(row['guild'],category,initial,query)
                self.page=min(max(0,page),max(0,(len(self.items)-1)//25))
                self.selected=selected
                cats=sorted({c for v in system.service.config(row['guild'])['names'].values() for c in v['categories']})
                cat=discord.ui.Select(placeholder=t('Categoría'),options=[discord.SelectOption(label=t('Todas'),value='all')]+[discord.SelectOption(label=t(c),value=c,default=c==category) for c in cats],row=0)
                async def category_changed(i):await self.replace(i,category='' if cat.values[0]=='all' else cat.values[0],page=0,selected=None)
                cat.callback=category_changed;self.add_item(cat)
                visible=self.items[self.page*25:(self.page+1)*25]
                if visible:
                    sel=discord.ui.Select(placeholder=t('Selecciona tu nombre'),options=[discord.SelectOption(label=v['name'],value=k,default=k==selected) for k,v in visible],row=1)
                    async def choose(i):await self.replace(i,selected=sel.values[0])
                    sel.callback=choose;self.add_item(sel)
                self.button(t('Anterior'),2,lambda i:self.replace(i,page=self.page-1),self.page==0)
                self.button(t('Siguiente'),2,lambda i:self.replace(i,page=self.page+1),(self.page+1)*25>=len(self.items))
                async def filtering(i):
                    class Search(LocalizedModal,title=t('Filtrar nombres')):
                        letter=discord.ui.TextInput(label=t('Letra inicial (opcional)'),required=False,max_length=1,default=initial)
                        text=discord.ui.TextInput(label=t('Buscar nombre (opcional)'),required=False,max_length=40,default=query)
                        async def on_submit(modal,i):
                            try:
                                if not await self.interaction_check(i):return
                                await self.replace(i,initial=str(modal.letter).strip(),query=str(modal.text).strip(),page=0,selected=None)
                            except ValueError as exc:await system.ctx['notify_error'](i,exc)
                    await i.response.send_modal(Search())
                self.button(t('Filtrar letra / buscar'),3,filtering)
                async def random_name(i):await self.replace(i,selected=secrets.choice(self.items)[0])
                self.button(t('Sugerencia aleatoria'),3,random_name,not self.items)
                self.button(t('Limpiar filtros'),3,lambda i:self.replace(i,category='',initial='',query='',page=0,selected=None))
                async def confirm(i):
                    fresh=system.service.choose(row['guild'],row['user'],row['revision'],self.selected)
                    await i.response.edit_message(content=t('✅ Identidad guardada: **')+fresh['data']['character_name']+t('**. Abre /mi_ficha para continuar.'),embed=None,view=None)
                self.button(t('Confirmar identidad'),4,confirm,selected is None,discord.ButtonStyle.success)

            def button(self,label,rownum,callback,disabled=False,style=discord.ButtonStyle.secondary):
                b=discord.ui.Button(label=t(label),row=rownum,disabled=disabled,style=style);b.callback=callback;self.add_item(b)

            async def interaction_check(self,i):
                system.require(i)
                if i.user.id!=row['user']:raise ValueError(t('Este selector pertenece a otro jugador.'))
                fresh=system.ctx['STORE'].get(row['id'])
                system.service.check(fresh)
                if fresh['state']!='approved':await system.ctx['access'](i)
                if fresh['revision']!=row['revision']:raise ValueError(t('Panel desactualizado. Abre /identidad elegir.'))
                return True

            async def replace(self,i,**changes):
                opts=dict(category=category,initial=initial,query=query,page=self.page,selected=self.selected);opts.update(changes)
                view=system.view(row,**opts)
                await i.response.edit_message(embed=view.card(),view=view)

            def card(self):
                entry=system.service.config(row['guild'])['names'].get(self.selected,{})
                given=entry.get('name',t('[Elige nombre]'))
                e=discord.Embed(title=t('🥷 忍 · Identidad de World X'),description=t('identity.preview', given=given, surname=row['data']['surname']),color=0xBC904A)
                e.add_field(name=t('Origen del apellido'),value=row['data']['identity_source'])
                e.add_field(name=t('Filtros'),value=''.join([format_fragment(category or t('Todas'), '', -1), format_fragment(t(' · Inicial: '), '', -1), format_fragment(initial or t('Todas'), '', -1), format_fragment(t(' · Buscar: '), '', -1), format_fragment(query or '—', '', -1)]))
                e.set_footer(text=t('identity.footer', count=len(self.items), page=self.page+1, total=max(1,(len(self.items)+24)//25)))
                return e
        return Picker()

    def install(self):
        identities=app_commands.Group(name='identidad',description='Elegir y consultar la identidad ninja')
        names=app_commands.Group(name='nombres',description='Catálogo de nombres del servidor')
        surnames=app_commands.Group(name='apellidos',description='Apellidos automáticos por bloodline')
        @identities.command(name='elegir',description='Elegir nombre por categoría, inicial o búsqueda; no consume giros')
        async def choose(i:discord.Interaction):await self.show(i)
        @identities.command(name='ver',description='Ver tu nombre completo y el origen de tu apellido')
        async def see(i:discord.Interaction):
            self.require(i);row=self.ctx['STORE'].find(i.guild_id,i.user.id)
            if not row:raise ValueError(t('Crea primero tu ficha con /mi_ficha.'))
            d=row['data'];await i.response.send_message(''.join(['🥷 ', format_fragment(d.get('character_name', t('Pendiente de identidad')), '', -1), format_fragment(t('\nOrigen: '), '', -1), format_fragment(d.get('identity_source', t('Pendiente')), '', -1), format_fragment(t('\nConfirmada: '), '', -1), format_fragment(t('Sí') if self.service.valid(d) else 'No', '', -1)]),ephemeral=True)
        @identities.command(name='corregir',description='Administración: corregir el nombre de una identidad con motivo')
        async def correct(i:discord.Interaction,usuario:discord.Member,nombre:str,motivo:str):
            self.require(i,True);row=self.service.prepare(i.guild_id,usuario.id)
            fresh=self.service.choose(i.guild_id,usuario.id,row['revision'],nombre,i.user.id,motivo)
            await i.response.send_message(t('Identidad actualizada: ')+fresh['data']['character_name'],ephemeral=True)
        @names.command(name='agregar',description='Administración: añadir o reactivar un nombre en una categoría existente')
        @app_commands.choices(categoria=[app_commands.Choice(name=c,value=c) for c in ('Japoneses','Naruto','Originales World X','Adaptaciones','Internacionales y anime')])
        async def add(i:discord.Interaction,nombre:str,categoria:str):
            self.require(i,True);nombre=nombre.strip()
            if not 2<=len(nombre)<=32 or not all(ch.isalpha() or ch in " '-" for ch in nombre):raise ValueError(t('Nombre de 2–32 caracteres: letras, espacios, guion o apóstrofo.'))
            def mutate(c):
                cats={cat for v in c['names'].values() for cat in v['categories']}
                if categoria not in cats:raise ValueError(t('Categorías: ')+', '.join(sorted(cats)))
                n=c['names'].setdefault(normalized(nombre),{'name':nombre,'categories':[],'active':True})
                n['active']=True
                if categoria not in n['categories']:n['categories'].append(categoria)
            self.service.catalog_edit(i.guild_id,i.user.id,'name_add',mutate)
            await i.response.send_message(t('Nombre disponible en el catálogo.'),ephemeral=True)
        @names.command(name='desactivar',description='Administración: ocultar un nombre para futuras elecciones')
        async def deactivate(i:discord.Interaction,nombre:str):
            self.require(i,True)
            def mutate(c):
                if normalized(nombre) not in c['names']:raise ValueError(t('Nombre no encontrado.'))
                c['names'][normalized(nombre)]['active']=False
            self.service.catalog_edit(i.guild_id,i.user.id,'name_disable',mutate)
            await i.response.send_message(t('Nombre oculto. Las identidades existentes se conservan.'),ephemeral=True)
        @names.command(name='listar',description='Descargar el catálogo actual de nombres y categorías')
        async def listing(i:discord.Interaction):
            self.require(i);c=self.service.config(i.guild_id)
            text='\n'.join(''.join([format_fragment(v['name'], '', -1), ' | ', format_fragment(', '.join(t(category_name) for category_name in v['categories']), '', -1), ' | ', format_fragment(t('activo') if v['active'] else t('inactivo'), '', -1)]) for k,v in sorted(c['names'].items()))
            await i.response.send_message(file=discord.File(io.BytesIO(text.encode()),filename='nombres-worldx.txt'),ephemeral=True)
        @surnames.command(name='configurar',description='Administración: cambiar el apellido de un bloodline para futuras identidades')
        @app_commands.choices(tipo=[app_commands.Choice(name='Principal',value='main'),app_commands.Choice(name='Secundario',value='secondary')])
        async def configure(i:discord.Interaction,tipo:str,bloodline:str,apellido:str,motivo:str):
            self.require(i,True);apellido=apellido.strip()
            if not 2<=len(apellido)<=40 or not all(ch.isalpha() or ch in " '-" for ch in apellido) or not 3<=len(motivo.strip())<=180:raise ValueError(t('Apellido de 2–40 caracteres y motivo de 3–180 caracteres.'))
            def mutate(c):
                if tipo not in ('main','secondary') or bloodline not in self.ctx['ASSIGNMENTS'].config[tipo]:raise ValueError(t('Identificador de bloodline inválido. Consulta /apellidos revisar.'))
                c[tipo][bloodline]=apellido
            self.service.catalog_edit(i.guild_id,i.user.id,'surname_configure: '+motivo,mutate)
            await i.response.send_message(t('Configurado para identidades aún no asignadas. No modifica apellidos existentes.'),ephemeral=True)
        @surnames.command(name='revisar',description='Administración: revisar asociaciones y detectar apellidos pendientes')
        async def review(i:discord.Interaction):
            self.require(i,True);c=self.service.config(i.guild_id)
            lines=[''.join([format_fragment(group, '', -1), ': ', format_fragment(k, '', -1), ' → ', format_fragment(c[group].get(k, t('FALTA CONFIGURAR')), '', -1)]) for group in ('main','secondary') for k in self.ctx['ASSIGNMENTS'].config[group]]
            await i.response.send_message(file=discord.File(io.BytesIO('\n'.join(lines).encode()),filename='apellidos-worldx.txt'),ephemeral=True)
        async def complete_name(i:discord.Interaction,current:str):
            self.require(i,True)
            return [app_commands.Choice(name=v['name'],value=v['name']) for k,v in self.service.names(i.guild_id,query=current)[:25]]
        correct.autocomplete('nombre')(complete_name)
        deactivate.autocomplete('nombre')(complete_name)
        async def complete_bloodline(i:discord.Interaction,current:str):
            self.require(i,True)
            kind=getattr(i.namespace,'tipo','main') or 'main'
            return [app_commands.Choice(name=k,value=k) for k in self.ctx['ASSIGNMENTS'].config.get(kind,{}) if current.casefold() in k.casefold()][:25]
        configure.autocomplete('bloodline')(complete_bloodline)
        for group in (identities,names,surnames):self.bot.tree.add_command(group)
