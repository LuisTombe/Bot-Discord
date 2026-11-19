"""Hardcore A: immutable origin gates; conditional candidate weights."""
import copy

ORDER = ['main','elements','secondary']
POWERFUL = {'akuma','jokei','senko','shizen','dokei','clay'}
INTERMEDIATE = {'glacier','sand','storm','black-lightning','dangan','wanziame','seishin','saberu','paper','ink'}
SECONDARY_POWERFUL = {'dust','explosion','inferno','typhoon','magma'}
SECONDARY_INTERMEDIATE = {'ice','lava','wood','tsunami','gold-sand','bolt','blood','frost'}

def active(c):
    p=c.get('birth_v8',{})
    return p.get('strategy','hardcore')=='hardcore' and p.get('hardcore_profile')=='A'

def upgrade(c):
    c=copy.deepcopy(c)
    c['birth_v8'].update(strategy='hardcore',hardcore_profile='A')
    from birth_v8 import historical_keys
    for group in ('main','secondary'):
        for k,v in c[group].items():
            unique=bool(historical_keys(c,[k] if group=='main' else [],[k] if group=='secondary' else []))
            powerful=POWERFUL if group=='main' else SECONDARY_POWERFUL
            intermediate=INTERMEDIATE if group=='main' else SECONDARY_INTERMEDIATE
            v['hardcore_a_weight']=.01 if unique else .35 if k in powerful else .8 if k in intermediate else 1.
    c['balance_revision']='8.2 · Hardcore A'
    return c

def origin(draw):
    x=draw()
    return dict(population_group='principal' if x<.8 else 'no_principal' if x<.95 else 'lee',
                secondary_granted=draw()<.6,hardcore_profile='A')

def weights(engine,group,results,selected,family,slot):
    from birth_v8 import historical_keys
    entries=[]
    for k,v in engine.c[group].items():
        if k in selected or v.get('event_only') or k=='kenichi':continue
        if family and v.get('family')!=family:continue
        values=engine.expanded(list(selected)+[k]) if group=='main' else list(selected)+[k]
        if engine.closure(dict(results,**{group:values}),{} if group=='main' else None) is None:continue
        unique=bool(historical_keys(engine.c,values if group=='main' else [],values if group=='secondary' else []))
        powerful=POWERFUL if group=='main' else SECONDARY_POWERFUL
        intermediate=INTERMEDIATE if group=='main' else SECONDARY_INTERMEDIATE
        weight=.01 if unique else .35 if k in powerful else .8 if k in intermediate else 1.
        weight=v.get('hardcore_a_weight',weight)
        if weight:entries.append((k,weight))
    if group=='main':
        budget=15 if family else .5 if engine.birth.get('population_group')=='lee' else 100
    else:budget=100 if not selected else [15,5,2,0][min(len(selected)-1,3)]
    total=sum(w for _,w in entries)
    return [(k,w/total*budget) for k,w in entries] if total else []

def secondary(engine,results,extras):
    from assignment_domain import choose
    plan=engine.closure(results)
    if plan is None:raise ValueError('Combinación incompatible')
    values=list(plan['secondary'])
    candidates=engine.distributions('secondary',results,values)
    eligible=bool(candidates) and not plan['special']
    engine.state['secondary_audit']=dict(eligible=eligible,mandatory=bool(values),granted=engine.birth['secondary_granted'])
    if plan['special'] or not engine.birth['secondary_granted']:return values,extras
    for slot in range(4-len(plan['main'])-len(values)):
        entries=engine.distributions('secondary',results,values,slot=slot)
        k=choose(entries,engine.draw)
        if not k:break
        values.append(k)
    return values,extras
