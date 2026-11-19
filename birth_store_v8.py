"""SQLite reservations and ordered phases; called inside AssignmentStore write locks."""
import copy
import json
import secrets
from birth_v8 import BirthEngineV8,PHASES,phase,capacity_keys,is_v8,historical_keys
from i18n import t


def setup(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS birth_hardcore_a_origins(guild INTEGER NOT NULL,user INTEGER NOT NULL,origin TEXT NOT NULL,PRIMARY KEY(guild,user));
    CREATE TABLE IF NOT EXISTS birth_history_unique(
      guild INTEGER NOT NULL,kind TEXT NOT NULL,user INTEGER NOT NULL,
      PRIMARY KEY(guild,kind,user));
    CREATE TABLE IF NOT EXISTS birth_population_groups(
      guild INTEGER NOT NULL,user INTEGER NOT NULL,bucket TEXT NOT NULL,
      PRIMARY KEY(guild,user));
    CREATE TABLE IF NOT EXISTS birth_history_imported(id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS birth_v8_tickets(
      guild INTEGER NOT NULL,user INTEGER NOT NULL,number INTEGER NOT NULL,quota TEXT,completed INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(guild,user),UNIQUE(guild,number));
    CREATE TABLE IF NOT EXISTS birth_v8_blocks(
      guild INTEGER NOT NULL,number INTEGER NOT NULL,rengoku INTEGER NOT NULL,sengoku INTEGER NOT NULL,
      PRIMARY KEY(guild,number));
    CREATE TABLE IF NOT EXISTS birth_v8_claims(
      guild INTEGER NOT NULL,user INTEGER NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,
      PRIMARY KEY(guild,user,kind));
    ''')
    if 'completed' not in {r[1] for r in db.execute('PRAGMA table_info(birth_v8_tickets)')}:
        db.execute('ALTER TABLE birth_v8_tickets ADD COLUMN completed INTEGER NOT NULL DEFAULT 0')
        db.commit()


def origin(store,guild,user,state,draw):
    """One non-recyclable ticket per Discord ID, including across owner resets.
    Historical unique claims never renew; A origin gates also survive resets.
    """
    db=store.db
    row=db.execute('SELECT number,quota,completed FROM birth_v8_tickets WHERE guild=? AND user=?',(guild,user)).fetchone()
    if not row:
        number=db.execute('SELECT COALESCE(MAX(number),0)+1 FROM birth_v8_tickets WHERE guild=?',(guild,)).fetchone()[0]
        db.execute('INSERT INTO birth_v8_tickets(guild,user,number,quota) VALUES(?,?,?,NULL)',(guild,user,number))
    else:number=row[0]
    state['birth'].update(ticket=number,quota=None)
    import hardcore_a
    if hardcore_a.active(state['rules']):
        old=db.execute('SELECT origin FROM birth_hardcore_a_origins WHERE guild=? AND user=?',(guild,user)).fetchone()
        gates=json.loads(old[0]) if old else {k:state['birth'][k] for k in ('population_group','secondary_granted','hardcore_profile')}
        db.execute('INSERT OR IGNORE INTO birth_hardcore_a_origins VALUES(?,?,?)',(guild,user,json.dumps(gates)))
        state['birth'].update(gates)
    elif state['rules']['birth_v8'].get('strategy')=='easy':
        old=db.execute('SELECT bucket FROM birth_population_groups WHERE guild=? AND user=?',(guild,user)).fetchone()
        bucket=old[0] if old else state['birth']['population_group']
        db.execute('INSERT OR IGNORE INTO birth_population_groups VALUES(?,?,?)',(guild,user,bucket))
        state['birth']['population_group']=bucket
        # The player chooses the order after selecting their village.


def import_history(store,guild,c):
    """Recover finalized awards from live records and retained reset audit snapshots."""
    aliases=c['birth_v8']['aliases']
    def remember(user,state):
        if not isinstance(state,dict) or state.get('stage')!=3:return
        r=state.get('results',{})
        m=[aliases.get(k,k) for k in r.get('main',[])]
        sec=[aliases.get(k,k) for k in r.get('secondary',[])]
        keys=historical_keys(c,[k for k in m if k in c['main']],[k for k in sec if k in c['secondary']])
        for key in keys:store.db.execute('INSERT OR IGNORE INTO birth_history_unique VALUES(?,?,?)',(guild,key,user))
    for row in store.db.execute('SELECT user,state FROM assignments WHERE guild=?',(guild,)).fetchall():
        remember(row['user'],json.loads(row['state']))
    for row in store.db.execute("SELECT id,user,detail FROM assignment_audit WHERE guild=? AND action='reset' AND id NOT IN (SELECT id FROM birth_history_imported)",(guild,)).fetchall():
        detail=json.loads(row['detail']);previous=detail.get('previous') or {}
        remember(row['user'],previous.get('state'))
        store.db.execute('INSERT OR IGNORE INTO birth_history_imported VALUES(?)',(row['id'],))


def occupied(store,guild,user,c):
    """Include legacy active assignments, so upgrading cannot duplicate a unique.
    Pending results reserve slots; no timeout releases a displayed award silently.
    """
    if store.db.in_transaction:import_history(store,guild,c)
    else:
        with store.db:import_history(store,guild,c)
    counts={}
    seen=set()
    for row in store.db.execute('SELECT user,kind FROM birth_history_unique WHERE guild=?',(guild,)):
        seen.add((row['user'],row['kind']));counts[row['kind']]=counts.get(row['kind'],0)+1
    for row in store.db.execute('SELECT user,kind FROM birth_v8_claims WHERE guild=? AND user!=?',(guild,user)):
        pair=(row['user'],row['kind'])
        if pair not in seen:seen.add(pair);counts[row['kind']]=counts.get(row['kind'],0)+1
    aliases=c['birth_v8']['aliases']
    for row in store.db.execute('SELECT user,state FROM assignments WHERE guild=? AND user!=?',(guild,user)):
        s=json.loads(row['state'])
        r=s.get('results',{})
        m=[aliases.get(k,k) for k in r.get('main',[])];sec=[aliases.get(k,k) for k in r.get('secondary',[])]
        m=[k for k in m if k in c['main']];sec=[k for k in sec if k in c['secondary']]
        for key in capacity_keys(c,m,sec,s.get('birth')):
            pair=(row['user'],key)
            if pair not in seen:seen.add(pair);counts[key]=counts.get(key,0)+1
    return counts


def reserve(store,guild,user,state):
    c=state['rules'];taken=occupied(store,guild,user,c)
    engine=BirthEngineV8(c,birth=state['birth'],state=state,occupied=taken)
    plan=engine.closure(state['results'])
    if plan is None:raise ValueError(t('v8.incompatible'))
    keys=capacity_keys(c,plan['main'],plan['secondary'],state['birth'])
    # Explicitly validate special paths too, independent of solver short circuits.
    if any(taken.get(k,0)>=cap for k,cap in keys.items()):raise ValueError(t('v8.unique_taken'))
    store.db.execute('DELETE FROM birth_v8_claims WHERE guild=? AND user=?',(guild,user))
    for k in keys:
        store.db.execute('INSERT INTO birth_v8_claims VALUES(?,?,?,?)',(guild,user,k,'final' if state['stage']==3 else 'pending'))


def mutate(store,guild,user,state,action,draw=None):
    if not state.get('order'):raise ValueError(t('v8.choose_order'))
    state['birth']['quota']=None  # retired recurring v8 quotas
    name=phase(state)
    if action=='spin':
        if state['used'][name]>=3:raise ValueError(t('Ya usaste tus tres giros.'))
        engine=BirthEngineV8(state['rules'],draw,state['birth'],state,occupied(store,guild,user,state['rules']))
        values,extras=engine.roll(name)
        state['results'][name]=values;state['extras']=extras;state['used'][name]+=1
    elif action=='confirm':
        if name not in state['results']:raise ValueError(t('Debes girar al menos una vez antes de confirmar.'))
        state['stage']+=1
    else:raise ValueError(t('Acción inválida.'))
    reserve(store,guild,user,state)
    if state['stage']==3:
        for key in historical_keys(state['rules'],state['results'].get('main',[]),state['results'].get('secondary',[])):
            store.db.execute('INSERT OR IGNORE INTO birth_history_unique VALUES(?,?,?)',(guild,key,user))
        store.db.execute('UPDATE birth_v8_tickets SET completed=1 WHERE guild=? AND user=?',(guild,user))
    return name



def automatic_unstarted(store,guild,user,state):
    import hardcore_a
    if state.get('order_selected') or state.get('stage')!=0 or state.get('frozen') or any(state.get('used',{}).values()) or state.get('results'):return False
    if not state.get('birth') or not state.get('order'):return False
    c=state['rules']
    if not (hardcore_a.active(c) or c.get('birth_v8',{}).get('strategy')=='easy'):return False
    # A manual order chosen for this origin must not be mistaken for an automatic one.
    last_origin=store.db.execute("SELECT COALESCE(MAX(id),0) FROM assignment_audit WHERE guild=? AND user=? AND action='birth_origin'",(guild,user)).fetchone()[0]
    return not store.db.execute("SELECT 1 FROM assignment_audit WHERE guild=? AND user=? AND action='birth_order' AND id>?",(guild,user,last_origin)).fetchone()

def repair_unstarted_order(store,guild,user):
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        row=store.get(guild,user)
        if not row or not automatic_unstarted(store,guild,user,row['state']):return
        state=row['state'];previous=state.pop('order')
        store.db.execute('UPDATE assignments SET revision=revision+1,state=? WHERE guild=? AND user=?',(json.dumps(state),guild,user))
        store.audit(guild,user,user,'repair_auto_order',{'previous':previous})

def order(store,guild,user,revision,values):
    if len(values)!=3 or set(values)!=set(PHASES):raise ValueError(t('v8.choose_order'))
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        store.require_registration(guild,user);row=store.get(guild,user)
        if not row or row['revision']!=revision:raise ValueError(t('Panel desactualizado. Abre /asignacion iniciar.'))
        state=row['state']
        if not is_v8(state['rules']) or not state.get('birth') or (state.get('order') and not automatic_unstarted(store,guild,user,state)) or state['frozen'] or any(state['used'].values()):raise ValueError(t('v8.order_locked'))
        state['order']=list(values);state['order_selected']=True
        store.db.execute('UPDATE assignments SET revision=revision+1,state=? WHERE guild=? AND user=?',(json.dumps(state),guild,user))
        store.audit(guild,user,user,'birth_order',{'order':values})
    return store.get(guild,user)


def equip(store,guild,user,revision,elements=None,secondary=None):
    """Change active slots only for a birth with an explicit broad entitlement.
    Original spins/results and exclusive reservations never change.
    """
    from birth_v8 import final,NATURES
    from identity_domain import signature
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        row=store.get(guild,user);reg=store.reg.find(guild,user)
        if not row or row['revision']!=revision or not reg or reg['state'] not in ('draft','correction','rejected','approved'):raise ValueError(t('v8.equip_locked'))
        state=row['state']
        if not is_v8(state['rules']) or state['stage']!=3 or state['frozen']:raise ValueError(t('v8.equip_locked'))
        data=final(state);loadout=copy.deepcopy(state.get('loadout',{}));c=state['rules']
        if elements is None and secondary is None:raise ValueError(t('v8.equip_empty'))
        if elements is not None:
            if len(data['element_access'])!=5 or not 1<=len(elements)<=4 or not set(elements)<=set(NATURES):raise ValueError(t('v8.equip_elements'))
            loadout['elements']=list(elements)
        if secondary is not None:
            if not data.get('secondary_access'):raise ValueError(t('v8.equip_secondary'))
            allowed={k for k,v in c['secondary'].items() if not v.get('has_mode') and not v.get('event_only') and not v.get('cap') and not v.get('mecha_only') and not v.get('akuma_only') and not v.get('family')}
            if len(secondary)!=len(set(secondary)) or not set(secondary)<=allowed or len(secondary)+len(data['mains'])>4:raise ValueError(t('v8.equip_secondary'))
            loadout['secondary']=list(secondary)
        candidate=dict(state['results']);candidate.update(loadout)
        if BirthEngineV8(c,birth=state['birth'],state=state).closure(candidate) is None:raise ValueError(t('v8.incompatible'))
        before=state.get('loadout',{});state['loadout']=loadout
        updated=dict(reg['data']);updated.update(final(state))
        if updated.get('identity_confirmed'):updated['identity_signature']=signature(updated)
        store.db.execute('UPDATE assignments SET revision=revision+1,state=? WHERE guild=? AND user=?',(json.dumps(state),guild,user))
        store.db.execute('UPDATE registrations SET revision=revision+1,data=?,updated=CURRENT_TIMESTAMP WHERE id=?',(json.dumps(updated),reg['id']))
        if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='identity_public_updates'").fetchone():
            store.db.execute('INSERT INTO identity_public_updates VALUES(?,?) ON CONFLICT(registration) DO UPDATE SET revision=excluded.revision',(reg['id'],reg['revision']+1))
        store.audit(guild,user,user,'birth_equip',{'before':before,'after':loadout})
    return store.get(guild,user)
