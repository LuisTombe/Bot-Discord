"""Isolated population ledger: never reads/writes production reservations."""
import copy
from birth_v8 import BirthEngineV8,PHASES,fresh_birth,capacity_keys,final

def sample_v8(original,policy):
    c=original.c;draw=original.draw
    import hardcore_a
    if not hasattr(original,'v8_ledger'):original.v8_ledger=dict(c.get('_simulation_occupied',{}));original.v8_ticket=0;original.v8_positions=[]
    ticket=original.v8_ticket+1;original.v8_ticket=ticket
    birth=fresh_birth(c.get('_simulation_village','konoha'),c,draw);birth.update(ticket=ticket,quota=None)
    default=list(hardcore_a.ORDER) if hardcore_a.active(c) else ['main','secondary','elements'] if birth.get('population_group') else list(PHASES)
    order=list(c.get('_simulation_order',default))
    if len(order)!=3 or set(order)!=set(PHASES):raise ValueError('Orden de simulación inválido')
    state=dict(rules=c,rules_hash='simulation',results={},used=dict.fromkeys(PHASES,0),stage=0,order=order,birth=birth)
    for name in order:
        for _ in range(1 if policy=='first' else 3):
            engine=BirthEngineV8(c,draw,birth,state,original.v8_ledger)
            values,extras=engine.roll(name);state['results'][name]=values;state['extras']=extras;state['used'][name]+=1
            if name=='elements':accept=len(values)>=(3 if policy=='hunt' else 2)
            elif name=='main' and policy=='hunt':accept=any(c['main'][k].get('fixed_percent') is not None or c['main'][k].get('quota') for k in values)
            else:accept=bool(values)
            if accept:break
        state['stage']+=1
    final(state) # Validate each simulated outcome, not just aggregate it.
    for key in capacity_keys(c,state['results']['main'],state['results']['secondary'],birth):original.v8_ledger[key]=original.v8_ledger.get(key,0)+1
    original.last_birth=birth;original.last_state=copy.deepcopy(state)
    return tuple(state['results'][k] for k in PHASES)
