"""One active ninja card at a time, persistent rolls, owner-only reset and staff controls."""
from i18n import bilingual, format_fragment, t
from panels_i18n import panel_embeds
import json
import copy
import asyncio
import io
from balance_ui import BalanceUI
from assignment_stats import StatisticsStore, census, simulate, attachments, POLICIES
from collections import Counter
import discord
from discord import app_commands
from assignment_domain import AssignmentStore, Engine, STAGES, load_config, final_data, fingerprint

from birth_v8 import is_v8
from birth_v71 import engine_for, has_phase, TYPES, PATHS

TITLES = ('🌪️ Ⅰ · Naturalezas de chakra', '👁️ Ⅱ · Linaje principal', '🧬 Ⅲ · Bloodlines secundarios')


class AssignmentSystem:
    def __init__(self, bot, ctx):
        self.bot, self.ctx = bot, ctx
        self.config = load_config(ctx['BASE']/'asignacion_config.json')
        self.store = AssignmentStore(ctx['STORE'], self.config)
        self.statistics = StatisticsStore(ctx['STORE'].db)
        self.statistics_job = asyncio.Lock()
        self.balance_ui = BalanceUI(self)
        self.install()
        self.balance_ui.install_alias()

    def require(self, i, owner=False, staff=False):
        if i.guild_id != self.ctx['GUILD']:
            raise ValueError(t('Usa el servidor configurado.'))
        if owner and not self.ctx['owner'](i):
            raise ValueError(t('Solo el dueño de la aplicación puede reiniciar asignaciones.'))
        if staff and not self.ctx['staff'](i):
            raise ValueError(t('Solo el staff puede usar este comando.'))

    def card(self, row, stage):
        if is_v8(row['state']['rules']):
            from birth_ui_v8 import card
            return card(self,row,stage)
        if row['state']['rules'].get('birth_policy',{}).get('version')==2:return self.free_card(row,stage)
        state = row['state']; name = STAGES[stage]; c = state['rules']
        value = state['results'].get(name)
        birth=state.get('birth')
        absent=bool(birth and not has_phase(birth,name))
        if value is None: result = t('Todavía no has girado.')
        elif name == 'elements':
            result = '\n'.join(f"{t(c['elements'][k]['label'])} ×{n}" for k,n in Counter(value).items())
        else:
            result = '\n'.join(t(c[name][k]['label']) for k in value) or (t('N/BP · Sin linaje principal') if name=='main' else t('N/BS · Sin secundario'))
        if state['frozen']: status = t('❄️ Congelado por el staff')
        elif stage < state['stage']: status = t('🔒 Confirmado')
        elif stage == state['stage']: status = t('🌀 Tu turno')
        else: status = t('⏳ Confirma la sección anterior')
        e = discord.Embed(title=t(TITLES[stage]), color=0xE89436, description=f'**{status}**\n\n{result}')
        if birth:
            e.add_field(name=t('Origen'),value=c['birth_policy']['villages'][birth['village']]+' · '+t(TYPES[birth['type']]),inline=False)
        if absent:
            e.add_field(name=t('Sección sin tiradas'),value=t('Este nacimiento no incluye esta categoría. Confirma para continuar; no necesitas gastar giros vacíos.'),inline=False)
        e.add_field(name=t('Giros usados'),value=f"{state['used'][name]}/3",inline=True)
        e.add_field(name=t('Regla del pergamino'),value=t('Cada giro reemplaza el resultado. Confirmar bloquea esta sección. Después del tercer giro revisa el resultado y pulsa Confirmar y continuar.'),inline=False)
        if name=='elements':
            e.add_field(name=t('Pesos iniciales de elementos'),value=(t('Slots ocupados: ')+', '.join(f'{n}: {100*w/sum(c["element_count_weights"].values()):g}%' for n,w in c['element_count_weights'].items())+''.join([format_fragment(t('. Repetición base: '), '', -1), format_fragment(c['element_repeat_birth_percent'], ''.join(['g']), -1), format_fragment(t('% de intentos multielemento. Las condiciones de viabilidad y tus rerolls modifican las frecuencias finales.'), '', -1)]) if 'element_count_weights' in c else t('Reglas anteriores: cuatro tiradas. Reiniciar la asignación requiere al dueño.')),inline=False)
            e.add_field(name=t('Slots ocupados'),value=f'{len(value)}/4' if value is not None else t('Pendiente'),inline=True)
        elif name=='main':
            e.add_field(name=t('Linajes compatibles'),value=(t('La presencia de principal depende de tu tipo de nacimiento. ') if birth else t('Puede salir N/BP. '))+t('Solo las familias Sand y Storm permiten principales adicionales compatibles. Cuatro slots totales entre principales y secundarios.'),inline=False)
        else:
            e.add_field(name='Kekkei genkai',value=(t('Si tu tipo incluye secundarios, el primero está garantizado entre candidatos compatibles; los adicionales son opcionales. ') if birth else t('Cada slot libre tiene una tirada según las reglas guardadas. '))+t('Se respetan tus elementos y tu principal. Lee y Gai no pueden usar chakra ni secundarios.'),inline=False)
        if birth:
            e.add_field(name=t('Reglas de origen'),value=t('El tipo de nacimiento permanece fijo. La aldea aumenta ligeramente el peso de sus candidatos comunes; las combinaciones siguen siendo obligatorias. Las frecuencias de elementos se condicionan a que exista un secundario común compatible cuando tu nacimiento incluye secundarios.'),inline=False)
        e.add_field(name=t('Progreso'),value=''.join([format_fragment(t('Fase '), '', -1), format_fragment(min(state['stage'] + 1, 3), '', -1), format_fragment(t('/3 · Elementos → Principal → Secundarios'), '', -1)]),inline=False)
        if name=='main' and value==[] and c.get('balanced',{}).get('none_requires_secondary_common') and c['balanced']['secondary_percent_without_main'][0]==100:
            e.add_field(name=t('🌊 Puedes conservar este camino'),value=t('Al confirmar, el primer slot de cada giro de secundarios garantiza un bloodline elemental compatible. No tendrás mode de clan principal.'),inline=False)
        if state['used'][name]>=3 and state['stage']==stage:
            e.add_field(name=t('✅ Resultado final del giro'),value=t('Ya usaste los tres giros. Puedes verlo con calma; pulsa Confirmar y continuar cuando estés listo.'),inline=False)
        e.set_footer(text=''.join(['World X · Balance ', format_fragment(c.get('balance_revision', '5.0'), '', -1), ' · ID ', format_fragment(row['user'], '', -1), format_fragment(t(' · Reglas '), '', -1), format_fragment(state['rules_hash'], '', -1), format_fragment(t(' · /asignacion iniciar para retomar'), '', -1)]))
        return e

    def free_card(self,row,stage):
        state=row['state'];name=state.get('order',STAGES)[stage];c=state['rules'];value=state['results'].get(name)
        if value is None:result=t('Pulsa Girar para obtener tu primer resultado.')
        elif name=='elements':result='\n'.join(f"{t(c[name][k]['label'])} ×{n}" for k,n in Counter(value).items())
        else:result='\n'.join(t(c[name][k]['label']) for k in value) or (t('N/BP · Sin principal') if name=='main' else t('N/BS · Sin secundario'))
        status=t('❄️ Congelado') if state['frozen'] else t('🔒 Confirmado') if stage<state['stage'] else t('🌀 Tu turno') if stage==state['stage'] else t('⏳ Confirma la sección anterior')
        e=discord.Embed(title=t(TITLES[stage]),description=f'**{status}**\n\n{result}',color=0xE89436)
        e.add_field(name=t('Giros de esta sección'),value=f"{state['used'][name]}/3",inline=True)
        if state.get('birth'):e.add_field(name=t('Aldea de origen'),value=c['birth_policy']['villages'][state['birth']['village']],inline=True)
        e.add_field(name=t('Tu decisión'),value=t('Cada giro reemplaza el resultado anterior, incluso si sale vacío. Puedes confirmar después de 1, 2 o 3 giros. Confirmar no consume giros de las otras categorías.'),inline=False)
        if name=='elements':
            e.add_field(name=t('Naturalezas de chakra'),value=t('Entre 1 y 4 elementos. Los elementos confirmados fijan qué bloodlines son compatibles; las repeticiones conservan sus requisitos.'),inline=False)
        else:
            e.add_field(name=t('Suerte y compatibilidad'),value=t('N/BP y N/BS se pueden volver a sortear mientras queden giros. Se conservan requisitos elementales, afinidad de aldea y cuatro slots de bloodlines en total. Sin candidatos compatibles, el resultado será vacío. Lee/Gai no pueden obtener secundarios.'),inline=False)
        if state['used'][name]==3 and state['stage']==stage:
            e.add_field(name=t('Resultado del tercer giro'),value=t('Revisa lo que salió y pulsa Confirmar y continuar. No avanzas automáticamente.'),inline=False)
        e.set_footer(text=t('assignment.footer', revision=c.get('balance_revision'), phase=stage+1))
        return e

    def view(self, row, stage):
        system = self
        class RollView(self.ctx['SafeView']):
            def __init__(self):
                super().__init__(timeout=900)
                state=row['state'];name=state.get('order',STAGES)[stage]
                active=state['stage']==stage and not state['frozen']
                for action,title,emoji,style in [('spin',t('Girar / Reroll'),'🌀',discord.ButtonStyle.primary),
                                                ('confirm',t('Confirmar y continuar'),'🔒',discord.ButtonStyle.success)]:
                    disabled=not active or (action=='spin' and (state['used'][name]>=3 or (state.get('birth') and not has_phase(state['birth'],name)))) or (action=='confirm' and name not in state['results'])
                    button=discord.ui.Button(label=t(title),emoji=emoji,style=style,disabled=disabled)
                    async def clicked(i, action=action):
                        system.require(i)
                        await system.ctx['access'](i)
                        if i.user.id!=row['user']:raise ValueError(t('Este pergamino pertenece a otro shinobi.'))
                        async with system.ctx['OPERATIONS']:
                            fresh=system.store.mutate(i.guild_id,i.user.id,row['revision'],stage,action)
                        next_stage=fresh['state']['stage']
                        if next_stage==3:
                            registration=system.ctx['STORE'].find(i.guild_id,i.user.id)
                            await i.response.edit_message(content=t('✅ Nacimiento guardado. Usa el botón Elegir nombre y apellido o /identidad elegir para completar tu identidad. Si eres elegible, usa /asignacion camino para escoger samurái o respiraciones antes de enviar la ficha. Completa tus datos, especialidad y las tres capturas.'),
                                embed=system.ctx['embed'](registration),view=system.ctx['Wizard'](registration))
                        else:
                            notice=t('🔒 Sección confirmada. Continúa con el siguiente pergamino.') if next_stage!=stage else None
                            await i.response.edit_message(content=notice,embed=system.card(fresh,next_stage),view=system.view(fresh,next_stage))
                    button.callback=clicked;self.add_item(button)
                refresh=discord.ui.Button(label=t('Retomar / actualizar'),emoji='📜',row=1)
                async def refreshed(i):
                    if i.user.id!=row['user']:raise ValueError(t('Este pergamino pertenece a otro shinobi.'))
                    await system.show(i)
                refresh.callback=refreshed;self.add_item(refresh)
        return RollView()

    async def show(self, i):
        self.require(i)
        await self.ctx['access'](i)
        row=self.store.start(i.guild_id,i.user.id)
        stage=row['state']['stage']
        if row['state']['rules'].get('birth_policy') and not row['state'].get('birth'):
            await i.response.send_message(t('🍃 **Elige tu aldea de origen.** Queda fijada al seleccionarla; después podrás girar tus elementos. Es tu lugar de nacimiento, no un traslado ni una acreditación económica.'),view=self.origin_view(row),ephemeral=True)
            return
        if is_v8(row['state']['rules']) and not row['state'].get('order'):
            from birth_ui_v8 import order_view
            await i.response.send_message(t('v8.order_intro'),view=order_view(self,row),ephemeral=True)
            return
        if stage==3:
            registration=self.ctx['STORE'].find(i.guild_id,i.user.id)
            await i.response.send_message(t('✅ Tu asignación ya está terminada. Los resultados están guardados en tu ficha.'),
                embed=self.ctx['embed'](registration),view=self.ctx['Wizard'](registration),ephemeral=True)
        else:
            await i.response.send_message(embed=self.card(row,stage),view=self.view(row,stage),ephemeral=True)

    def origin_view(self,row):
        system=self
        class Origin(self.ctx['SafeView']):
            def __init__(self):
                super().__init__(timeout=900)
                picker=discord.ui.Select(placeholder=t('Aldea de nacimiento · elección definitiva'),options=[discord.SelectOption(label=v,value=k) for k,v in row['state']['rules']['birth_policy']['villages'].items()])
                async def picked(i):
                    system.require(i);await system.ctx['access'](i)
                    if i.user.id!=row['user']:raise ValueError(t('Este nacimiento pertenece a otro jugador.'))
                    async with system.ctx['OPERATIONS']:
                        fresh=system.store.choose_village(i.guild_id,i.user.id,row['revision'],picker.values[0])
                    if is_v8(fresh['state']['rules']) and not fresh['state'].get('order'):
                        from birth_ui_v8 import order_view
                        await i.response.edit_message(content=t('v8.order_intro'),view=order_view(system,fresh))
                        return
                    await i.response.edit_message(content=t('Origen guardado. Gira tus elementos.'),embed=system.card(fresh,0),view=system.view(fresh,0))
                picker.callback=picked;self.add_item(picker)
        return Origin()

    def panel(self):
        system=self
        class Panel(self.ctx['SafeView']):
            def __init__(self):super().__init__(timeout=None)
            @discord.ui.button(label=bilingual('Abrir mi asignación'),emoji='🌀',style=discord.ButtonStyle.primary,custom_id='worldx:assignment:start:v5')
            async def open(self,i,button):await system.show(i)
        return Panel()

    def restore(self):
        self.bot.add_view(self.panel())

    def install(self):
        system=self
        group=app_commands.Group(name='asignacion',description='Nacimiento shinobi: elementos, linajes y administración',guild_only=True)

        @group.command(name='configurar',description='Panel de pesos, porcentajes y combinaciones (solo dueño)')
        async def configure(i:discord.Interaction):await self.balance_ui.open(i)

        @group.command(name='iniciar',description='Abrir o retomar mis tres pergaminos de asignación')
        async def start(i:discord.Interaction):await self.show(i)

        @group.command(name='estado',description='Consultar mi asignación o la de un jugador (staff)')
        async def status(i:discord.Interaction,usuario:discord.User|None=None):
            self.require(i,staff=bool(usuario and usuario.id!=i.user.id))
            user=usuario or i.user;row=self.store.get(i.guild_id,user.id)
            if not row:raise ValueError(t('Todavía no existe una asignación para ese usuario.'))
            await i.response.send_message(embeds=[self.card(row,s) for s in range(3)],ephemeral=True)

        @group.command(name='camino',description='Elegir Shinobi, Samurái o aspirante a respiraciones tras el nacimiento')
        @app_commands.choices(camino=[app_commands.Choice(name=v,value=k) for k,v in PATHS.items()])
        async def path(i:discord.Interaction,camino:app_commands.Choice[str]):
            self.require(i);await self.ctx['access'](i)
            row=self.store.get(i.guild_id,i.user.id)
            if not row:raise ValueError(t('Completa primero tu nacimiento.'))
            async with self.ctx['OPERATIONS']:
                self.store.choose_path(i.guild_id,i.user.id,row['revision'],camino.value)
            await i.response.send_message(t('✅ Camino guardado: ')+t(PATHS[camino.value])+t('. Las técnicas se desbloquean mediante permisos y entrenamiento; este camino no concede habilidades automáticamente.'),embed=self.ctx['embed'](self.ctx['STORE'].find(i.guild_id,i.user.id)),ephemeral=True)

        @group.command(name='probabilidades' ,description='Ver mis bloodlines elegibles y sus probabilidades por slot')
        async def odds(i:discord.Interaction):
            self.require(i)
            row=self.store.get(i.guild_id,i.user.id)
            if row and is_v8(row['state']['rules']):
                from birth_ui_v8 import odds
                if not row['state'].get('order'):raise ValueError(t('v8.choose_order'))
                return await odds(self,i,row)
            if not row or row['state']['stage']==0:raise ValueError(t('Confirma primero tus elementos.'))
            state=row['state'];engine=engine_for(state['rules'],birth=state.get('birth'));elements=state['results']['elements']
            group='main' if state['stage']==1 else 'secondary'
            mains=state['results'].get('main',[])
            entries=engine.distribution(group,elements,mains)
            text='\n'.join(f"{state['rules'][group][key]['label']}: {p:g}%" for key,p in entries)
            empty=100-sum(p for _,p in entries)
            e=discord.Embed(title=t('📊 Probabilidades por slot · ')+(t('Principal') if group=='main' else t('Secundario')),color=0xE89436,
                description=(text or t('Sin candidatos compatibles.'))+''.join([format_fragment(t('\n\nNinguno: '), '', -1), format_fragment(empty, ''.join(['g']), -1), format_fragment(t('%\n\nSon porcentajes por intento de slot, no por nacimiento. Los candidatos por peso se reparten entre compatibles; los de porcentaje fijo conservan su valor por intento. Se muestra la primera tirada de la fase; los slots siguientes tienen menos probabilidad.'), '', -1)]))
            await i.response.send_message(embed=e,ephemeral=True)

        @group.command(name='panel',description='Publicar el panel de asignación en este canal (administración)')
        async def panel(i:discord.Interaction):
            self.require(i)
            if not (self.ctx['owner'](i) or i.user.guild_permissions.manage_guild):raise ValueError(t('Necesitas Gestionar servidor.'))
            await i.response.defer(ephemeral=True)
            e=discord.Embed(title=t('🍃 𝑾𝒐𝒓𝒍𝒅 𝑿 · Nacimiento Shinobi'),color=0xE89436,
                description=t('Crea primero tu borrador con **/mi_ficha**.\n\n**Aldea de origen → Elementos → Principal → Secundarios**\nTres giros por sección. Cada giro reemplaza el anterior. Para avanzar siempre debes confirmar, incluso después del tercer giro.\n\nTus resultados se guardan automáticamente. Reiniciar el bot o borrar la ficha no devuelve giros.'))
            await i.channel.send(embeds=panel_embeds('assignment'),view=self.panel())
            await i.followup.send(t('Panel publicado.'),ephemeral=True)

        @group.command(name='historial',description='Ver los últimos giros, confirmaciones y cambios (staff)')
        async def history(i:discord.Interaction,usuario:discord.User):
            self.require(i,staff=True)
            rows=self.store.history(i.guild_id,usuario.id)
            lines=[]
            for row in rows:
                detail=json.loads(row['detail'])
                text=detail.get('reason') or (str(detail) if row['action'] in ('birth_origin','birth_path') else '') or f"{detail.get('section','')} · {', '.join(detail.get('result',[])) or 'ninguno'}"
                lines.append(f"{row['created']} UTC · {row['action']} · actor {row['actor']} · {text}")
            await i.response.send_message(t('Sin historial.') if not lines else discord.utils.escape_mentions('\n'.join(lines))[:1900],ephemeral=True)

        class ResetConfirmation(self.ctx['SafeView']):
            def __init__(self,actor,user,row,reg,reason):
                super().__init__(timeout=60)
                self.actor,self.user,self.row,self.reg,self.reason=actor,user,row,reg,reason
                self.used=False
            @discord.ui.button(label='Reiniciar los nueve giros',style=discord.ButtonStyle.danger)
            async def confirm(self,i,button):
                system.require(i,owner=True)
                if i.user.id!=self.actor or self.used:raise ValueError(t('Confirmación inválida o utilizada.'))
                async with system.ctx['OPERATIONS']:
                    system.store.admin(i.guild_id,self.user,self.actor,self.row['revision'] if self.row else -1,
                                       'reset',self.reason,self.reg['revision'])
                self.used=True;self.stop()
                await i.response.edit_message(content=t('Asignación reiniciada y registrada en el historial. El jugador puede usar /asignacion iniciar.'),view=None)
            @discord.ui.button(label='Cancelar')
            async def cancel(self,i,button):
                if i.user.id!=self.actor:raise ValueError(t('Confirmación ajena.'))
                self.used=True;self.stop()
                await i.response.edit_message(content=t('Cancelado.'),view=None)

        @group.command(name='reiniciar',description='Restaurar los nueve giros con motivo y confirmación (solo dueño)')
        async def reset(i:discord.Interaction,usuario:discord.User,motivo:app_commands.Range[str,1,180]):
            self.require(i,owner=True)
            reg=self.ctx['STORE'].find(i.guild_id,usuario.id)
            if not reg or reg['state'] not in ('draft','correction','rejected'):
                raise ValueError(t('Crea o reabre primero la ficha con /registro reabrir.'))
            row=self.store.get(i.guild_id,usuario.id)
            await i.response.send_message(t('Se borrarán los resultados activos de elementos y bloodlines y se devolverán tres giros por sección. El historial se conserva. Las capturas deben actualizarse si dejan de coincidir. ¿Confirmas?'),
                view=ResetConfirmation(i.user.id,usuario.id,row,reg,motivo.strip()),ephemeral=True)

        async def freeze(i,user,reason,action):
            self.require(i,staff=True)
            row=self.store.get(i.guild_id,user.id);reg=self.ctx['STORE'].find(i.guild_id,user.id)
            if not row or not reg:raise ValueError(t('No existe asignación.'))
            async with self.ctx['OPERATIONS']:
                self.store.admin(i.guild_id,user.id,i.user.id,row['revision'],action,reason,reg['revision'])
            await i.response.send_message(t('Asignación ')+(t('congelada.') if action=='freeze' else t('reabierta sin devolver giros.')),ephemeral=True)

        @group.command(name='congelar',description='Bloquear giros de una asignación en curso (staff)')
        async def frozen(i:discord.Interaction,usuario:discord.User,motivo:app_commands.Range[str,1,180]):await freeze(i,usuario,motivo,'freeze')

        @group.command(name='reabrir',description='Descongelar sin devolver giros ni cambiar resultados (staff)')
        async def unfreeze(i:discord.Interaction,usuario:discord.User,motivo:app_commands.Range[str,1,180]):await freeze(i,usuario,motivo,'unfreeze')

        @group.command(name='probar',description='Simular uno o hasta tres giros por fase sin gastar giros reales (staff)')
        @app_commands.choices(estrategia=[app_commands.Choice(name='Un giro por fase',value='first'),app_commands.Choice(name='Hasta 3: aceptar principal y secundario',value='practical'),app_commands.Choice(name='Hasta 3: buscar principales raros',value='hunt')])
        @app_commands.choices(aldea=[app_commands.Choice(name=v,value=k) for k,v in self.config.get('birth_policy',{}).get('villages',{}).items()])
        async def test(i:discord.Interaction,estrategia:app_commands.Choice[str]|None=None,aldea:app_commands.Choice[str]|None=None):
            self.require(i,staff=True)
            from simulate_balance import sample
            policy=estrategia.value if estrategia else 'first'
            config=copy.deepcopy(self.config)
            if config.get('birth_policy') and aldea and aldea.value!='konoha':config['_simulation_village']=aldea.value
            engine=Engine(config);elements,mains,secondary=sample(engine,policy)
            result=final_data(getattr(engine,'last_state',None) or {'results':{'elements':elements,'main':mains,'secondary':secondary},'rules_hash':fingerprint(config),'rules':config,'birth':getattr(engine,'last_birth',None)})
            self.statistics.save(i.guild_id,i.user.id,config,policy,census([result],config))
            mains=result['mains'];secondary=result['secondary']
            await i.response.send_message('Modalidad: '+config.get('birth_v8',{}).get('strategy','legacy')+' · Orden: '+' → '.join(getattr(engine,'last_state',{}).get('order',STAGES))+'\n'+''.join([format_fragment(t('🧪 **SIMULACIÓN · balance '), '', -1), format_fragment(self.config.get('balance_revision', '5.0'), '', -1), format_fragment(t(' · guardada solo en estadísticas de prueba; no se puede reclamar**\nOrigen: '), '', -1), format_fragment(config.get('_simulation_village', 'konoha'), '', -1), format_fragment(t(' · Tipo: '), '', -1), format_fragment(result.get('birth_type', 'anterior'), '', -1), format_fragment(t('\nEstrategia: '), '', -1), format_fragment(t(estrategia.name) if estrategia else t('Un giro por fase'), '', -1), format_fragment(t('\nElementos: '), '', -1), format_fragment(', '.join(elements), '', -1), format_fragment(t('\nPrincipales: '), '', -1), format_fragment(', '.join(mains) or 'N/BP', '', -1), format_fragment(t('\nSecundarios: '), '', -1), format_fragment(', '.join(secondary) or 'N/BS', '', -1), '\n', format_fragment('' if result['chakra_usable'] else t('🥋 Camino de taijutsu: sin uso de elementos ni secundarios.'), '', -1)]),ephemeral=True)

        async def send_charts(i, summary, sections, source, config=None):
            if not summary['n']:
                await i.followup.send(t('Todavía no hay nacimientos en esta muestra. Las asignaciones deben estar terminadas; las pruebas anteriores a v5.5 no se guardaban.'),ephemeral=True)
                return
            meta=summary.get('simulation',{})
            if meta:source=f"{meta['mode']} · {meta.get('profile') or '-'} · {meta['policy']} · {' → '.join(meta.get('order') or [])} · cupos {meta['slots']}"
            m=summary.get('metrics',{})
            if m:
                eligible=m.get('eligible_optional_secondary',0);received=m.get('received_optional_secondary',0)
                source+=f" | Sec. opcional {received}/{eligible} compatibles; obligatorios {m.get('mandatory_secondary',0)}"
            files=await asyncio.to_thread(attachments,summary,config if config is not None else self.config,sections,source)
            await i.followup.send(
                '📊 **'+source+''.join([format_fragment(t('**\nSin principal: '), '', -1), format_fragment(summary.get('metrics',{}).get('without_genetic_main',summary['missing'].get('main',0)), '', -1), '/', format_fragment(summary['n'], '', -1), format_fragment(t(' · Sin secundario: '), '', -1), format_fragment(summary['missing'].get('secondary', 0), '', -1), '/', format_fragment(summary['n'], '', -1), '\n'])+t('Frecuencias observadas, no probabilidades exactas. Cero apariciones no significa imposible. '
                'Elementos repetidos cuentan una vez por usuario; Lee/Gai conservan su elemento registrado pero no pueden usar chakra. '
                'Las fichas eliminadas y las asignaciones sin terminar no cuentan en el censo real.'),
                files=[discord.File(io.BytesIO(data),filename=name) for name,data in files],ephemeral=True)

        async def real_chart(i,section,approved):
            self.require(i,staff=True)
            await i.response.defer(ephemeral=True)
            summary=self.statistics.real(i.guild_id,self.config,approved)
            source=t('REALES | ')+(t('Fichas aprobadas') if approved else t('Asignaciones terminadas, incluida revisión/borrador'))
            source+=''.join([' | ', format_fragment(len(summary['versions']), '', -1), format_fragment(t(' versiones de reglas'), '', -1)])
            await send_charts(i,summary,[section],source)

        @group.command(name='grafica_elementos',description='Censo real: usuarios con cada elemento, gráfica y CSV (staff)')
        async def chart_elements(i:discord.Interaction,solo_aprobados:bool=False):
            await real_chart(i,'elements',solo_aprobados)

        @group.command(name='grafica_principales',description='Censo real: usuarios con cada bloodline principal (staff)')
        async def chart_main(i:discord.Interaction,solo_aprobados:bool=False):
            await real_chart(i,'main',solo_aprobados)

        @group.command(name='grafica_secundarios',description='Censo real: usuarios con cada bloodline secundario (staff)')
        async def chart_secondary(i:discord.Interaction,solo_aprobados:bool=False):
            await real_chart(i,'secondary',solo_aprobados)

        @group.command(name='graficas_pruebas',description='Tres gráficas de pruebas acumuladas con las reglas actuales (staff)')
        @app_commands.choices(estrategia=[app_commands.Choice(name=label,value=key) for key,label in POLICIES.items()])
        @app_commands.choices(aldea=[app_commands.Choice(name=v,value=k) for k,v in self.config.get('birth_policy',{}).get('villages',{}).items()])
        async def test_charts(i:discord.Interaction,estrategia:app_commands.Choice[str]|None=None,aldea:app_commands.Choice[str]|None=None):
            self.require(i,staff=True)
            await i.response.defer(ephemeral=True)
            policy=estrategia.value if estrategia else 'first'
            config=copy.deepcopy(self.config)
            if config.get('birth_policy') and aldea and aldea.value!='konoha':config['_simulation_village']=aldea.value
            summary=self.statistics.tests(i.guild_id,config,policy)
            await send_charts(i,summary,['elements','main','secondary'],''.join([format_fragment(t('PRUEBAS '), '', -1), format_fragment(config.get('_simulation_village', 'konoha'), '', -1), ' | ', format_fragment(t(POLICIES[policy]), '', -1), format_fragment(t(' | Reglas '), '', -1), format_fragment(fingerprint(config), '', -1)]),config)

        @group.command(name='simular',description='Simular 100 a 1000 nacimientos y crear tres gráficas de prueba (staff)')
        @app_commands.choices(estrategia=[app_commands.Choice(name=label,value=key) for key,label in POLICIES.items()])
        @app_commands.choices(aldea=[app_commands.Choice(name=v,value=k) for k,v in self.config.get('birth_policy',{}).get('villages',{}).items()])
        @app_commands.choices(modalidad=[app_commands.Choice(name=n,value=k) for k,n in [('hardcore','Hardcore'),('normal','Normal'),('easy','Fácil')]])
        @app_commands.choices(orden=[app_commands.Choice(name=' → '.join(seq),value=','.join(seq)) for seq in __import__('itertools').permutations(('elements','main','secondary'))])
        async def batch(i:discord.Interaction,cantidad:app_commands.Range[int,100,1000]=100,
                        estrategia:app_commands.Choice[str]|None=None,aldea:app_commands.Choice[str]|None=None,modalidad:app_commands.Choice[str]|None=None,cupos_actuales:bool=False,orden:app_commands.Choice[str]|None=None):
            self.require(i,staff=True)
            if self.statistics_job.locked():raise ValueError(t('Hay una simulación en curso. Espera a que termine.'))
            async with self.statistics_job:
                await i.response.defer(ephemeral=True)
                policy=estrategia.value if estrategia else 'first'
                config=copy.deepcopy(self.config)
                if config.get('birth_policy') and aldea and aldea.value!='konoha':config['_simulation_village']=aldea.value
                if modalidad:config['birth_v8']['strategy']=modalidad.value
                if orden:config['_simulation_order']=orden.value.split(',')
                if cupos_actuales:
                    from birth_store_v8 import occupied
                    async with self.ctx['OPERATIONS']:config['_simulation_occupied']=occupied(self.store,i.guild_id,-1,config)
                summary=await asyncio.to_thread(simulate,config,cantidad,policy)
                batch_id=self.statistics.save(i.guild_id,i.user.id,config,policy,summary)
                await send_charts(i,summary,['elements','main','secondary'],''.join([format_fragment(t('PRUEBA '), '', -1), format_fragment(config.get('_simulation_village', 'konoha'), '', -1), format_fragment(t(' LOTE '), '', -1), format_fragment(batch_id, '', -1), ' | ', format_fragment(t(POLICIES[policy]), '', -1), format_fragment(t(' | Reglas '), '', -1), format_fragment(fingerprint(config), '', -1)]),config)

        @group.command(name='ayuda',description='Explicar giros, confirmación y recuperación')
        async def help_command(i:discord.Interaction):
            import hardcore_a
            if hardcore_a.active(self.config):
                self.require(i)
                await i.response.send_message('Hardcore A: 80% principal genético y 20% sin principal (incluye 5% Lee). Elige cualquiera de los seis órdenes antes de girar. Tres giros por sección, pero la categoría y la oportunidad del 60% para un secundario compatible se fijan al nacer. Los secundarios obligatorios son excepciones. /asignacion simular permite elegir modalidad, estrategia de giros y cupos actuales sin gastar cupos reales. Las fichas iniciadas antes conservan sus reglas.',ephemeral=True)
                return
            self.require(i)
            await i.response.send_message(t('📜 Crea /mi_ficha → /asignacion iniciar → elige tu aldea. Cada sección tiene tres giros independientes. N/BP y N/BS pueden volver a sortearse; confirmar no gasta giros de la siguiente sección. /asignacion camino elige tu camino al terminar, si eres elegible.\n🌀 Tres giros TOTALES por sección; no tres adicionales. Cada giro reemplaza el anterior.\n🔒 Solo Confirmar bloquea la sección y habilita la siguiente; el tercer giro deja el resultado visible. No se puede regresar.\n✅ Al terminar se guarda en la ficha; completa las capturas y envía al staff.\n📌 /asignacion estado consulta resultados. /asignacion iniciar recupera paneles caducados.\nStaff: historial, congelar, reabrir, probar, grafica_elementos, grafica_principales, grafica_secundarios, graficas_pruebas y simular (100–1000). Solo dueño: configurar (panel de balance), reiniciar con motivo y confirmación.\nN/BP y N/BS son resultados válidos sin bloodline.'),ephemeral=True)

        @group.command(name='equipar',description='Alternar slots si tu nacimiento concede acceso ampliado')
        async def equip_slots(i:discord.Interaction,elementos:str|None=None,secundarios:str|None=None):
            self.require(i);await self.ctx['access'](i)
            from birth_store_v8 import equip
            row=self.store.get(i.guild_id,i.user.id)
            if not row:raise ValueError(t('Completa primero tu nacimiento.'))
            def parse(value):return [x.strip().lower().replace(' ','-') for x in value.split(',') if x.strip() and x.strip().lower() not in ('none','ninguno')] if value is not None else None
            async with self.ctx['OPERATIONS']:
                equip(self.store,i.guild_id,i.user.id,row['revision'],parse(elementos),parse(secundarios))
            await i.response.send_message(t('v8.equip_saved'),embed=self.ctx['embed'](self.ctx['STORE'].find(i.guild_id,i.user.id)),ephemeral=True)

        @group.command(name='cupos',description='Consultar reservas de bloodlines exclusivos (staff)')
        async def slots(i:discord.Interaction):
            self.require(i,staff=True)
            from birth_store_v8 import occupied
            counts=occupied(self.store,i.guild_id,-1,self.config) if is_v8(self.config) else {}
            text='\n'.join(f'{k}: {v}' for k,v in sorted(counts.items())) or t('v8.no_claims')
            await i.response.send_message(t('v8.claims_note')+'\n'+text,ephemeral=True)

        self.bot.tree.add_command(group)
