"""Bilingual birth cards, order selection and version-aware owner editor."""
import copy
import io
import itertools
import json
from collections import Counter
import discord
from i18n import t
import hardcore_a
from assignment_domain import fingerprint
from birth_v8 import PHASES,phase,BirthEngineV8
from birth_store_v8 import order,occupied

def label(name):return t('v8.phase.'+name)

def card(system,row,index):
    s=row['state'];c=s['rules'];name=phase(s,index);value=s['results'].get(name)
    text=t('Todavía no has girado.') if value is None else '\n'.join(f"{c[name][k]['label']} ×{n}" for k,n in Counter(value).items()) or t('v8.empty.'+name)
    status=t('❄️ Congelado') if s['frozen'] else t('🔒 Confirmado') if index<s['stage'] else t('🌀 Tu turno') if index==s['stage'] else t('⏳ Confirma la sección anterior')
    e=discord.Embed(title='🌀 '+label(name),description=f'**{status}**\n\n{text}',color=0xE89436)
    e.add_field(name='Modalidad',value={'hardcore':'Hardcore','normal':'Normal','easy':'Fácil'}[c['birth_v8'].get('strategy','hardcore')])
    e.add_field(name=t('Giros de esta sección'),value=f"{s['used'][name]}/3")
    e.add_field(name=t('Aldea de origen'),value=c['birth_policy']['villages'].get(s.get('birth',{}).get('village'),t('Pendiente')))
    e.add_field(name=t('v8.order'),value=' → '.join(label(k) for k in s.get('order',PHASES)),inline=False)
    e.add_field(name=t('Tu decisión'),value='Hardcore A: categoría y oportunidad de secundario fijas desde el origen. Tres giros cambian el resultado, no esas oportunidades.' if hardcore_a.active(c) else t('v8.roll_rules'),inline=False)
    if s.get('birth'):
        engine=BirthEngineV8(c,birth=s['birth'],state=s);plan=engine.closure(s['results'])
        if plan:
            pending=[]
            for g in ['main','secondary']:
                pending += [c[g][k]['label'] for k in plan[g] if k not in s['results'].get(g,[])]
            if pending:e.add_field(name=t('v8.reserved'),value=', '.join(pending),inline=False)
            if 'elements' not in s['results'] and not plan['special']:
                reqs=[' + '.join(c['elements'][k]['label']+(' ×'+str(n) if n>1 else '') for k,n in r.items()) or t('v8.none_required') for r in plan['requirements']]
                e.add_field(name=t('v8.required_elements'),value=(' / '.join(dict.fromkeys(reqs)))[:1000],inline=False)
            if plan['all_elements']:e.add_field(name=t('v8.entitlements'),value=t('v8.all_elements'),inline=False)
        if s['birth'].get('quota'):e.add_field(name=t('v8.quota'),value=t('v8.quota_notice',lineage=c['main'][s['birth']['quota']]['label']),inline=False)
    e.set_footer(text='World X v8 · '+s['rules_hash']+' · /asignacion iniciar')
    return e

def order_view(system,row):
    view=system.ctx['SafeView'](timeout=900)
    picker=discord.ui.Select(placeholder=t('v8.choose_order'),options=[discord.SelectOption(label=' → '.join(label(k) for k in seq),value=','.join(seq)) for seq in itertools.permutations(PHASES)])
    async def chosen(i):
        system.require(i);await system.ctx['access'](i)
        if i.user.id!=row['user']:raise ValueError(t('Este nacimiento pertenece a otro jugador.'))
        async with system.ctx['OPERATIONS']:
            fresh=order(system.store,i.guild_id,i.user.id,row['revision'],picker.values[0].split(','))
        await i.response.edit_message(content=t('v8.order_saved'),embed=system.card(fresh,0),view=system.view(fresh,0))
    picker.callback=chosen;view.add_item(picker);return view

async def odds(system,i,row):
    s=row['state']
    if s['stage']==3:raise ValueError(t('v8.finished_odds'))
    name=phase(s);r=dict(s['results']);r.pop(name,None)
    engine=BirthEngineV8(s['rules'],birth=s['birth'],state=s,occupied=occupied(system.store,i.guild_id,i.user.id,s['rules']))
    if name=='elements':text=t('v8.element_odds')+'\n'+json.dumps(s['rules']['birth_v8']['element_counts'])
    elif name=='main' and s['birth'].get('quota'):
        text=s['rules']['main'][s['birth']['quota']]['label']+': 100%\n\n'+t('v8.odds_note')
    else:
        entries=engine.distributions(name,r)
        text='\n'.join(f"{s['rules'][name][k]['label']}: {p:.4g}%" for k,p in entries)
        text+='\n\n'+t('v8.odds_note')
    if hardcore_a.active(s['rules']):text='Hardcore A: pesos condicionados a la categoría y compatibilidad; NO son porcentajes de población. Secundario opcional: oportunidad 60% fijada al nacer; obligatorios exceptuados.\n\n'+text
    await i.response.send_message(embed=discord.Embed(title=t('v8.odds_title'),description=text[:4000],color=0xE89436),ephemeral=True)

def balance_card(ui,group='main',key=None):
    c=ui.s.config
    e=discord.Embed(title=t('v8.balance_title'),description=t('v8.balance_help'),color=0xE89436)
    if key:e.add_field(name=c[group][key]['label'],value='```json\n'+json.dumps(c[group][key],ensure_ascii=False,indent=2)[:950]+'\n```',inline=False)
    else:e.add_field(name=t('v8.order'),value=t('v8.balance_overview'),inline=False)
    e.add_field(name='Modalidad',value={'hardcore':'Hardcore','normal':'Normal','easy':'Fácil · 80% principal / 15% secundario / 5% Lee'}[c['birth_v8'].get('strategy','hardcore')],inline=False)
    if hardcore_a.active(c):e.add_field(name='Propuesta A activa',value='80% principal genético · 20% sin él (incluye 5% Lee). Secundario opcional: 60% si es compatible. Pesos 1 / 0,8 / 0,35; únicos 0,01. Orden elegido por el jugador antes de girar.',inline=False)
    e.set_footer(text='World X v8.2 · '+fingerprint(c));return e

def balance_view(ui,group='main',key=None,page=0):
    c=ui.s.config;expected=fingerprint(c)
    class Panel(ui.s.ctx['SafeView']):
        def __init__(self):super().__init__(timeout=900)
        async def interaction_check(self,i):ui.require(i);return True
    view=Panel()
    strategy=discord.ui.Select(placeholder='Modalidad de probabilidades',row=4,options=[discord.SelectOption(label=n,value=k,default=c['birth_v8'].get('strategy','hardcore')==k) for k,n in [('hardcore','Hardcore'),('normal','Normal'),('easy','Fácil')]])
    async def change_strategy(i):
        ui.require(i)
        candidate=copy.deepcopy(c);candidate['birth_v8']['strategy']=strategy.values[0]
        async with ui.s.ctx['OPERATIONS']:
            ui.control.commit(expected,candidate,i.user.id,'Cambiar modalidad: '+strategy.values[0])
        await i.response.edit_message(embed=balance_card(ui,group,key),view=balance_view(ui,group,key,page))
    strategy.callback=change_strategy;view.add_item(strategy)
    categories=discord.ui.Select(placeholder=t('1 · Categoría'),options=[discord.SelectOption(label=label(g),value=g,default=g==group) for g in ['main','secondary']])
    async def category(i):await i.response.edit_message(embed=balance_card(ui,categories.values[0]),view=balance_view(ui,categories.values[0]))
    categories.callback=category;view.add_item(categories)
    keys=list(c[group]);page=max(0,min(page,(len(keys)-1)//25))
    picker=discord.ui.Select(placeholder=t('v8.pick_lineage'),options=[discord.SelectOption(label=c[group][k]['label'],value=k,default=k==key) for k in keys[page*25:(page+1)*25]])
    async def picked(i):await i.response.edit_message(embed=balance_card(ui,group,picker.values[0]),view=balance_view(ui,group,picker.values[0],page))
    picker.callback=picked;view.add_item(picker)
    def button(text,callback,row=2,disabled=False):
        b=discord.ui.Button(label=text,row=row,disabled=disabled);b.callback=callback;view.add_item(b)
    async def prev(i):await i.response.edit_message(embed=balance_card(ui,group),view=balance_view(ui,group,page=page-1))
    async def nxt(i):await i.response.edit_message(embed=balance_card(ui,group),view=balance_view(ui,group,page=page+1))
    button('◀',prev,disabled=page==0);button('▶',nxt,disabled=(page+1)*25>=len(keys))
    async def edit(i):await i.response.send_modal(config_modal(ui,c,expected,group,key))
    async def general(i):await i.response.send_modal(config_modal(ui,c,expected,'birth_v8',None))
    async def export(i):await i.response.send_message(file=discord.File(io.BytesIO(json.dumps(c,ensure_ascii=False,indent=2).encode()),filename='worldx-birth-v8.json'),ephemeral=True)
    async def undo(i):await ui.propose(i,ui.control.previous(),expected,t('Deshacer último cambio'))
    button(t('v8.edit_rule'),edit,disabled=key is None);button(t('v8.general'),general)
    button(t('Exportar JSON'),export,row=3);button(t('Deshacer último'),undo,row=3)
    return view

def config_modal(ui,c,expected,group,key):
    class Edit(ui.s.ctx['SafeModal']):
        def __init__(self):
            super().__init__(title=t('v8.edit_rule'),timeout=600)
            original=c[group][key] if key else c[group]
            self.value=discord.ui.TextInput(label=t('v8.json_rule'),style=discord.TextStyle.paragraph,default=json.dumps(original,ensure_ascii=False,indent=2),max_length=3500)
            self.reason=discord.ui.TextInput(label=t('Motivo'),min_length=3,max_length=180)
            self.add_item(self.value);self.add_item(self.reason)
        async def on_submit(self,i):
            ui.require(i)
            try:value=json.loads(self.value.value)
            except (ValueError,TypeError):raise ValueError(t('v8.invalid_json'))
            if not isinstance(value,dict):raise ValueError(t('v8.invalid_json'))
            candidate=copy.deepcopy(c)
            if key:candidate[group][key]=value
            else:candidate[group]=value
            try:await ui.propose(i,candidate,expected,self.reason.value)
            except (KeyError,TypeError):raise ValueError(t('v8.invalid_json'))
    return Edit()
