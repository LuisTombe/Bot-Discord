"""Owner-only Discord balance editor with preview, confirmation and rollback."""
from i18n import format_fragment, t
import copy
import io
import json
import discord
from discord import app_commands
from assignment_domain import fingerprint
from balance_control import BalanceControl, NATURES, item_value, set_value, set_requirements, number, validate_editor

LABELS={'main':'Principales','secondary':'Secundarios / elementales','elements':'Elementos de chakra'}
NAMES=dict(zip(NATURES,['Tierra','Agua','Fuego','Viento','Rayo']))

def changes(old,new,path=''):
    if isinstance(old,dict) and isinstance(new,dict):
        result=[]
        for k in sorted(old.keys()|new.keys()):
            result+=changes(old.get(k),new.get(k),path+'.'+k if path else k)
        return result
    if old!=new:return [f'{path}: {old} → {new}']
    return []

class BalanceUI:
    def __init__(self,system):
        self.s=system;self.control=BalanceControl(system)

    def require(self,i):
        self.s.require(i)
        if not self.s.ctx['owner'](i):raise ValueError(t('Solo el dueño de la aplicación puede configurar el balance.'))

    def card(self,group='main',key=None):
        from birth_v8 import is_v8
        if is_v8(self.s.config):
            from birth_ui_v8 import balance_card
            return balance_card(self,group,key)
        c=self.s.config
        e=discord.Embed(title=t('⚙️ World X · Control de probabilidades'),color=0xE89436)
        e.description=(t('Elige categoría y nombre. **Peso** reparte la probabilidad entre candidatos compatibles; **% fijo** es por intento de slot compatible, no por nacimiento.\n'
                       'Los cambios requieren confirmación. Se aplican a nuevas asignaciones y pruebas; las iniciadas conservan sus reglas.'))
        if c.get('birth_policy',{}).get('version')==2:
            e.add_field(name=t('Tres giros independientes'),value=''.join([format_fragment(t('Sin principal: '), '', -1), format_fragment(c['balanced']['main_none_percent'], ''.join(['g']), -1), format_fragment(t('% por primer intento compatible. Secundarios: presupuesto '), '', -1), format_fragment(c['balanced']['secondary_percent_with_main'][0], ''.join(['g']), -1), format_fragment(t('% con principal / '), '', -1), format_fragment(c['balanced']['secondary_percent_without_main'][0], ''.join(['g']), -1), format_fragment(t('% sin principal. No se compensan los rerolls.'), '', -1)]),inline=False)
        elif c.get('birth_policy'):
            from birth_v71 import TYPES
            e.add_field(name=t('Tipos de nacimiento · probabilidad final'),value=' · '.join(f'{TYPES[k]}: {v:g}%' for k,v in c['birth_policy']['types'].items()),inline=False)
            e.add_field(name=t('Afinidad de origen'),value=''.join([format_fragment(t('Multiplicador de peso: ×'), '', -1), format_fragment(c['birth_policy']['village_multiplier'], ''.join(['g']), -1), format_fragment(t('. Los porcentajes fijos raros no reciben bono.'), '', -1)]),inline=False)
        if key:
            item=c[group][key];mode,value=item_value(c,group,key)
            req=' + '.join(f'{t(NAMES[k])} ×{v}' for k,v in item.get('requires',{}).items()) or t('Sin requisito elemental')
            e.add_field(name=item['label'],value=''.join([format_fragment(t('Porcentaje fijo') if mode == 'fixed' else t('Peso relativo'), '', -1), ': **', format_fragment(value, ''.join(['g']), -1), format_fragment('%' if mode == 'fixed' else '', '', -1), '**\n', format_fragment(req, '', -1)]),inline=False)
            if item.get('main_required'):e.add_field(name=t('Principal obligatorio'),value=c['main'][item['main_required']]['label'])
            if key=='blood':e.add_field(name=t('Excepción Dokei'),value=''.join([format_fragment(item.get('percent_with_main', {}).get('dokei', 0), ''.join(['g']), -1), format_fragment(t('% por slot si Blood usa % fijo.'), '', -1)]))
            if item.get('event_only'):e.add_field(name=t('Solo evento / jinchūriki'),value=t('Excluido del sorteo ordinario.'))
            if c.get('birth_policy'):
                e.add_field(name=t('Aldeas afines'),value=', '.join(c['birth_policy']['villages'][v] for v in item.get('villages',[])) or t('Sin bonificación de origen'))
            if item.get('family'):e.add_field(name=t('Familia compatible'),value=item['family'])
            if group=='elements':e.add_field(name=t('Requisitos de elementos raros'),value=t('Se comprueban contra los elementos que salieron ANTES. Las cinco naturalezas base no pueden desactivarse ni tener requisitos.'),inline=False)
            if group=='main' and key in c['balanced']['taijutsu_mains']:e.add_field(name=t('Regla de taijutsu'),value=t('Conserva la prohibición de usar chakra y secundarios.'))
        e.set_footer(text=''.join([format_fragment(t('Balance '), '', -1), format_fragment(c.get('balance_revision'), '', -1), format_fragment(t(' · Reglas '), '', -1), format_fragment(fingerprint(c), '', -1), format_fragment(t(' · Solo dueño · 0 desactiva el candidato'), '', -1)]))
        return e

    async def open(self,i):
        self.require(i)
        await i.response.send_message(embed=self.card(),view=self.view(),ephemeral=True)

    def view(self,group='main',key=None,page=0):
        from birth_v8 import is_v8
        if is_v8(self.s.config):
            from birth_ui_v8 import balance_view
            return balance_view(self,group,key,page)
        ui=self;c=self.s.config;expected=fingerprint(c)
        class Panel(self.s.ctx['SafeView']):
            def __init__(self):super().__init__(timeout=900)
            async def interaction_check(self,i):ui.require(i);return True
        v=Panel()
        cat=discord.ui.Select(placeholder=t('1 · Categoría'),options=[discord.SelectOption(label=t(label),value=g,default=g==group) for g,label in LABELS.items()],row=0)
        async def category(i):
            g=cat.values[0];await i.response.edit_message(embed=ui.card(g),view=ui.view(g))
        cat.callback=category;v.add_item(cat)
        items=list(c[group]);page=max(0,min(page,(len(items)-1)//25));part=items[page*25:(page+1)*25]
        picker=discord.ui.Select(placeholder=''.join([format_fragment(t('2 · Elige nombre · página '), '', -1), format_fragment(page + 1, '', -1)]),options=[discord.SelectOption(label=c[group][k]['label'],value=k,default=k==key) for k in part],row=1)
        async def picked(i):
            k=picker.values[0];await i.response.edit_message(embed=ui.card(group,k),view=ui.view(group,k,page))
        picker.callback=picked;v.add_item(picker)
        def button(label,callback,row=2,disabled=False,style=discord.ButtonStyle.secondary):
            b=discord.ui.Button(label=label,row=row,disabled=disabled,style=style);b.callback=callback;v.add_item(b)
        async def previous(i):await i.response.edit_message(embed=ui.card(group),view=ui.view(group,page=page-1))
        async def next_page(i):await i.response.edit_message(embed=ui.card(group),view=ui.view(group,page=page+1))
        button('◀',previous,disabled=page==0);button('▶',next_page,disabled=(page+1)*25>=len(items))
        async def chance(i):
            await i.response.send_modal(ui.value_modal(c,group,key,expected))
        async def requirements(i):await i.response.send_modal(ui.requirements_modal(c,group,key,expected))
        button(t('Editar chance'),chance,disabled=key is None,style=discord.ButtonStyle.primary)
        button(t('Combinación elemental'),requirements,disabled=key is None or (group=='elements' and key in NATURES))
        async def villages(i):await i.response.send_message(t('Selecciona las aldeas afines; puedes dejar la selección vacía.'),view=ui.villages_view(c,group,key,expected),ephemeral=True)
        if c.get('birth_policy'):button(t('Aldeas afines'),villages,disabled=key is None or group=='elements')
        async def mode(i):
            old_mode,value=item_value(c,group,key);new_mode='fixed' if old_mode=='weight' else 'weight'
            await i.response.send_modal(ui.value_modal(c,group,key,expected,mode_override=new_mode))
        async def parent(i):await i.response.send_message(t('Elige el principal obligatorio, o Ninguno.'),view=ui.parent_view(c,key,expected),ephemeral=True)
        button(t('Cambiar peso ↔ % fijo'),mode,row=3,disabled=key is None or group=='elements')
        button(t('Principal requerido'),parent,row=3,disabled=key is None or group!='secondary')
        async def general(i):await i.response.send_message(t('Elige qué regla general editar.'),view=ui.general_view(c,expected),ephemeral=True)
        button(t('Ajustes generales'),general,row=3)
        async def history(i):
            rows=ui.control.history();text='\n'.join(f"#{r['id']} · {r['created']} UTC · actor {r['actor']} · {r['reason']}" for r in rows) or t('Sin cambios desde el panel.')
            await i.response.send_message(text[:1900],ephemeral=True)
        async def undo(i):await ui.propose(i,ui.control.previous(),expected,t('Deshacer último cambio'))
        async def export(i):
            data=json.dumps(ui.s.config,ensure_ascii=False,indent=2).encode()
            await i.response.send_message(t('Configuración activa exportada. El respaldo completo se hace con /registro respaldo.'),file=discord.File(io.BytesIO(data),filename='asignacion_config.json'),ephemeral=True)
        async def refresh(i):await i.response.edit_message(embed=ui.card(),view=ui.view())
        button('Historial',history,row=4);button(t('Deshacer último'),undo,row=4);button(t('Exportar JSON'),export,row=4);button(t('Actualizar panel'),refresh,row=4)
        return v

    def value_modal(self,c,group,key,expected,mode_override=None):
        ui=self;mode,value=item_value(c,group,key)
        mode=mode_override or mode
        class Value(self.s.ctx['SafeModal']):
            def __init__(self):
                super().__init__(title=t('Editar ')+(t('porcentaje fijo') if mode=='fixed' else t('peso relativo')),timeout=600)
                self.amount=discord.ui.TextInput(label=c[group][key]['label'][:45],default=f'{value:g}',placeholder=t('0 desactiva; acepta coma o punto'),max_length=20)
                self.add_item(self.amount);self.dokei=None
                if group=='secondary' and key=='blood' and mode=='fixed':
                    self.dokei=discord.ui.TextInput(label=t('Porcentaje de Blood si tiene Dokei'),default=str(c[group][key]['percent_with_main']['dokei']),max_length=20)
                    self.add_item(self.dokei)
            async def on_submit(self,i):
                ui.require(i)
                candidate=set_value(c,group,key,mode,self.amount.value,self.dokei.value if self.dokei else None)
                await ui.propose(i,candidate,expected,t('Chance de ')+c[group][key]['label'])
        return Value()

    def requirements_modal(self,c,group,key,expected):
        ui=self
        class Requirements(self.s.ctx['SafeModal']):
            def __init__(self):
                super().__init__(title=t('Combinación: 0 = no requiere'),timeout=600)
                for nature in NATURES:self.add_item(discord.ui.TextInput(label=t(NAMES[nature])+t(' (cantidad de 0 a 4)'),default=str(c[group][key].get('requires',{}).get(nature,0)),max_length=1))
            async def on_submit(self,i):
                ui.require(i);candidate=set_requirements(c,group,key,[x.value for x in self.children])
                await ui.propose(i,candidate,expected,t('Combinación de ')+c[group][key]['label'])
        return Requirements()

    def parent_view(self,c,key,expected,page=0):
        ui=self
        class Parents(self.s.ctx['SafeView']):
            def __init__(self):super().__init__(timeout=600)
            async def interaction_check(self,i):ui.require(i);return True
        v=Parents();items=list(c['main'])[page*24:(page+1)*24]
        select=discord.ui.Select(options=[discord.SelectOption(label=t('Ninguno'),value='__none__')]+[discord.SelectOption(label=c['main'][k]['label'],value=k) for k in items])
        async def picked(i):
            candidate=copy.deepcopy(c);item=candidate['secondary'][key];item.pop('main_required',None)
            if select.values[0]!='__none__':item['main_required']=select.values[0]
            await ui.propose(i,candidate,expected,t('Principal requerido por ')+item['label'])
        select.callback=picked;v.add_item(select)
        b=discord.ui.Button(label=t('Otra página'))
        async def turn(i):await i.response.edit_message(view=ui.parent_view(c,key,expected,1-page))
        b.callback=turn;v.add_item(b);return v

    def villages_view(self,c,group,key,expected):
        ui=self
        class Villages(self.s.ctx['SafeView']):
            def __init__(self):super().__init__(timeout=600)
            async def interaction_check(self,i):ui.require(i);return True
        view=Villages();villages=c['birth_policy']['villages']
        picker=discord.ui.Select(min_values=0,max_values=len(villages),options=[discord.SelectOption(label=v,value=k,default=k in c[group][key].get('villages',[])) for k,v in villages.items()])
        async def picked(i):
            candidate=copy.deepcopy(c);candidate[group][key]['villages']=list(picker.values)
            await ui.propose(i,candidate,expected,t('Aldeas afines de ')+c[group][key]['label'])
        picker.callback=picked;view.add_item(picker);return view

    def general_view(self,c,expected):
        ui=self
        class General(self.s.ctx['SafeView']):
            def __init__(self):super().__init__(timeout=600)
            async def interaction_check(self,i):ui.require(i);return True
        v=General()
        options=[('main',t('Principales adicionales')),('with',t('Secundarios extra con principal')),('without',t('Secundarios extra sin principal')),('slots',t('Cantidad de elementos')),('repeat',t('Repetición de elementos'))] if c.get('birth_policy',{}).get('version')==1 else [('main',t('N/BP y principales adicionales')),('with',t('Slots secundarios con principal')),('without',t('Slots secundarios sin principal')),('slots',t('Cantidad de elementos')),('repeat',t('Repetición de elementos'))]
        if c.get('birth_policy'):options+=[('birth',t('Afinidad de aldea') if c['birth_policy'].get('version')==2 else t('Tipos de nacimiento y bono de aldea'))]
        for kind,label in options:
            b=discord.ui.Button(label=label)
            async def click(i,kind=kind):await i.response.send_modal(ui.general_modal(c,expected,kind))
            b.callback=click;v.add_item(b)
        return v

    def general_modal(self,c,expected,kind):
        ui=self;b=c['balanced']
        if kind=='birth' and c['birth_policy'].get('version')==2:fields=[(t('Multiplicador aldea (1 a 1.5)'),c['birth_policy']['village_multiplier'])]
        elif kind=='birth':
            from birth_v71 import TYPES
            fields=[(TYPES[k]+' (%)',c['birth_policy']['types'][k]) for k in TYPES]+[(t('Multiplicador aldea (1 a 1.5)'),c['birth_policy']['village_multiplier'])]
        elif kind=='main' and c.get('birth_policy',{}).get('version')==1:fields=[(t('Principales extra de familia (%)'),b['family_percent'])]
        elif kind=='main':fields=[(t('N/BP por giro (%)'),b['main_none_percent']),(t('Principales extra de familia (%)'),b['family_percent'])]
        elif kind in ('with','without'):
            key='secondary_percent_'+('with_main' if kind=='with' else 'without_main')
            fields=[(''.join([format_fragment(t('Intento de slot '), '', -1), format_fragment(n + 1, '', -1), format_fragment(t(': presupuesto (%)'), '', -1)]),v) for n,v in enumerate(b[key]) if c.get('birth_policy',{}).get('version')!=1 or n>0]
        elif kind=='slots':fields=[(''.join([format_fragment(t('Peso para nacer con '), '', -1), format_fragment(n, '', -1), format_fragment(t(' elemento(s)'), '', -1)]),c['element_count_weights'][str(n)]) for n in range(1,5)]
        else:fields=[(t('Repetición en nacimiento multielemento (%)'),c['element_repeat_birth_percent']),(t('Repetición adicional (%)'),c['element_extra_repeat_percent'])]
        class General(self.s.ctx['SafeModal']):
            def __init__(self):
                super().__init__(title=t('Ajustes generales'),timeout=600)
                for label,value in fields:self.add_item(discord.ui.TextInput(label=label,default=str(value),max_length=20))
            async def on_submit(self,i):
                ui.require(i);candidate=copy.deepcopy(c);values=[number(x.value,10000 if kind=='slots' else 100) for x in self.children]
                if kind=='birth' and c['birth_policy'].get('version')==2:candidate['birth_policy']['village_multiplier']=values[0]
                elif kind=='birth':
                    from birth_v71 import TYPES
                    candidate['birth_policy']['types']=dict(zip(TYPES,values[:4]));candidate['birth_policy']['village_multiplier']=values[4]
                elif kind=='main' and c.get('birth_policy',{}).get('version')==1:candidate['balanced']['family_percent']=values[0]
                elif kind=='main':candidate['balanced'].update(main_none_percent=values[0],family_percent=values[1])
                elif kind in ('with','without'):candidate['balanced'][key]=([100]+values) if c.get('birth_policy',{}).get('version')==1 else values
                elif kind=='slots':candidate['element_count_weights']={str(n+1):v for n,v in enumerate(values)}
                else:candidate.update(element_repeat_birth_percent=values[0],element_extra_repeat_percent=values[1])
                await ui.propose(i,candidate,expected,t('Ajustes generales: ')+kind)
        return General()

    async def propose(self,i,candidate,expected,reason):
        self.require(i)
        if fingerprint(self.s.config)!=expected:raise ValueError(t('El panel quedó desactualizado. Vuelve a abrir /asignacion configurar.'))
        validate_editor(candidate)
        differences=changes(self.s.config,candidate)
        if not differences:raise ValueError(t('No cambiaste ningún valor.'))
        ui=self
        class Confirm(self.s.ctx['SafeView']):
            def __init__(self):super().__init__(timeout=300);self.used=False
            async def interaction_check(self,i):ui.require(i);return True
            @discord.ui.button(label='Guardar y aplicar',style=discord.ButtonStyle.success)
            async def save(self,i,button):
                ui.require(i)
                async with ui.s.ctx['OPERATIONS']:
                    if self.used:raise ValueError(t('Este cambio ya fue procesado.'))
                    ui.control.commit(expected,candidate,i.user.id,reason);self.used=True
                self.stop()
                await i.response.edit_message(content=t('✅ Guardado. Ya se aplica a nuevos nacimientos y simulaciones. Puedes seguir editando.'),embed=ui.card(),view=ui.view())
            @discord.ui.button(label='Cancelar')
            async def cancel(self,i,button):
                ui.require(i);self.used=True;self.stop()
                await i.response.edit_message(content=t('Cancelado. No se aplicó este cambio.'),embed=None,view=None)
        translations={'balanced':'Balance','main_fixed':t('Principales / porcentaje fijo'),'main_weights':t('Principales / peso'),'secondary_fixed':t('Secundarios / porcentaje fijo'),'secondary_weights':t('Secundarios / peso'),'main':t('Principal'),'secondary':t('Secundario'),'elements':t('Elemento'),'requires':t('Requiere'),'main_required':t('Principal obligatorio'),'weight':t('Peso'),'percent_with_main':t('Porcentaje especial por principal'),'dokei':'Dokei','main_none_percent':t('Sin principal (%)'),'family_percent':t('Principal extra de familia (%)'),'secondary_percent_with_main':t('Presupuestos de secundarios con principal'),'secondary_percent_without_main':t('Presupuestos de secundarios sin principal'),'element_count_weights':t('Pesos de cantidad de elementos'),'element_repeat_birth_percent':t('Repetición elemental (%)'),'element_extra_repeat_percent':t('Repetición adicional (%)'),'balance_revision':t('Versión')}
        translations.update({k:t(v) for k,v in NAMES.items()})
        for group in LABELS:
            translations.update({k:v['label'] for k,v in candidate[group].items()})
        readable=[]
        for line in differences:
            path,values=line.split(': ',1)
            readable.append(' · '.join(translations.get(p,p) for p in path.split('.'))+': '+values.replace('None',t('sin valor')))
        e=discord.Embed(title=t('Confirmar cambio · ')+reason[:180],color=0xE89436,
            description='\n'.join(readable)[:2500])
        e.add_field(name=t('Efecto'),value=t('Los pesos NO son porcentajes. % fijo es por slot compatible. Cambiar requisitos cambia quién puede obtenerlo. 0 lo desactiva. No modifica fichas ni asignaciones iniciadas.'),inline=False)
        if not candidate.get('birth_policy') and candidate['balanced']['secondary_percent_without_main'][0]<100:
            e.add_field(name=t('Garantía modificada'),value=t('Ya no se garantiza un secundario al nacer sin principal. Si acaba sin bloodlines, el motor puede convertirlo en Lee.'),inline=False)
        await i.response.send_message(embed=e,view=Confirm(),ephemeral=True)

    def install_alias(self):
        ui=self
        alias=app_commands.Group(name='asignar',description='Panel de control del balance (dueño)',guild_only=True)
        @alias.command(name='probabilidades',description='Editar pesos, porcentajes y combinaciones desde un menú (dueño)')
        async def open_panel(i:discord.Interaction):await ui.open(i)
        self.s.bot.tree.add_command(alias)
