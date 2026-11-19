"""Persistent assignments with weighted common pools and fixed compatible-slot odds.
Rules are snapshotted per birth; live edits affect only new assignments.
"""
from i18n import format_fragment, t
import hashlib
import json
import math
import secrets
from collections import Counter
from pathlib import Path

STAGES = ('elements', 'main', 'secondary')
PROTECTED = ('elements', 'element_draws', 'main', 'mains', 'secondary',
             'assignment_complete', 'assignment_rules', 'chakra_usable', 'assignment_version',
             'birth_type', 'birth_village', 'birth_path', 'samurai_eligible',
             'element_access', 'secondary_access', 'birth_order', 'birth_ticket', 'birth_secondary')
EDITABLE = ('draft', 'correction', 'rejected')


def load_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    validate_config(config)
    return config


def validate_config(c):
    from birth_v8 import is_v8, validate
    if is_v8(c):
        validate(c);return
    if c.get('version') != 5 or c.get('spins') != 3 or c.get('element_draws') != 4 or c.get('bloodline_slots') != 4:
        raise ValueError(t('Asignación: versión 5, tres giros y cuatro slots requeridos.'))
    if 'element_count_weights' in c:
        weights=c['element_count_weights']
        if set(weights)!={'1','2','3','4'} or any(not isinstance(w,(int,float)) or not math.isfinite(w) or w<0 for w in weights.values()) or sum(weights.values())<=0:
            raise ValueError(t('Pesos de cantidad elemental inválidos.'))
        for key in ('element_repeat_birth_percent','element_extra_repeat_percent'):
            value=c.get(key)
            if not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=100:
                raise ValueError(t('Probabilidad de repetición inválida.'))
    if 'balanced' in c:
        b=c['balanced']
        for group in ('main','secondary'):
            fixed=b[group+'_fixed'];weights=b[group+'_weights']
            if set(fixed)&set(weights) or set(fixed)|set(weights)!=set(c[group]):raise ValueError(t('Balance incompleto o duplicado.'))
            for v in [*fixed.values(),*weights.values()]:
                if not isinstance(v,(int,float)) or not math.isfinite(v) or v<0:raise ValueError(t('Peso o porcentaje inválido.'))
            if sum(fixed.values())>100:raise ValueError(t('Los porcentajes fijos exceden 100%.'))
        for key in ('secondary_percent_with_main','secondary_percent_without_main'):
            if len(b[key])!=4 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=100 for v in b[key]):raise ValueError(t('Slots secundarios inválidos.'))
        if not 0<=b['main_none_percent']<=100 or not 0<=b['family_percent']<=100:raise ValueError(t('Balance principal inválido.'))
        if sum(b['main_fixed'].values())>100-b['main_none_percent']:raise ValueError(t('Los porcentajes fijos principales superan el espacio disponible tras N/BP.'))
        for family in {v.get('family') for v in c['main'].values()}-{None}:
            if sum(v for k,v in b['main_fixed'].items() if c['main'][k].get('family')==family)>b['family_percent']:
                raise ValueError(t('Los porcentajes fijos de una familia superan su presupuesto de principales adicionales.'))
        if sum(b['secondary_fixed'].values())>100:raise ValueError(t('Masa rara secundaria excesiva.'))
        if not set(b['taijutsu_mains'])<=set(c['main']):raise ValueError(t('Linaje taijutsu desconocido.'))
    for group, key in [('elements', 'weight'), ('main', 'percent'), ('secondary', 'percent')]:
        for ident, item in c[group].items():
            n = item[key]
            if not isinstance(n, (int, float)) or not math.isfinite(n) or not 0 <= n <= 100:
                raise ValueError(''.join([format_fragment(t('Probabilidad inválida: '), '', -1), format_fragment(ident, '', -1)]))
            if not item['label'] or len(item['label']) > 80:
                raise ValueError(t('Etiqueta inválida.'))
            for element, amount in item.get('requires', {}).items():
                if element not in ('earth','water','fire','wind','lightning') or not isinstance(amount,int) or not 1 <= amount <= 4:
                    raise ValueError(''.join([format_fragment(t('Requisito inválido: '), '', -1), format_fragment(ident, '', -1)]))
            for parent, value in item.get('percent_with_main', {}).items():
                if parent not in c['main'] or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0 <= value <= (c.get('balanced',{}).get(group+'_fixed',{}).get(ident,100) if 'balanced' in c else n):
                    raise ValueError(t('Probabilidad condicional inválida.'))
            if item.get('main_required') and item['main_required'] not in c['main']:
                raise ValueError(t('Bloodline requerido inexistente.'))
        if group != 'elements' and sum(x[key] for x in c[group].values()) > 100:
            raise ValueError(''.join([format_fragment(group, '', -1), format_fragment(t(' supera 100%.'), '', -1)]))
    for base in ('earth','water','fire','wind','lightning'):
        if c['elements'][base]['weight'] <= 0 or c['elements'][base]['requires']:
            raise ValueError(t('Las cinco naturalezas base deben poder salir.'))

    if c.get('birth_policy'):
        from birth_v71 import validate_birth
        validate_birth(c)


def fingerprint(c):
    return hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:12]


def enough(counts, requires):
    return all(counts[e] >= n for e, n in requires.items())


def affinity(c, elements):
    return Counter(c['elements'][e]['affinity'] for e in elements if c['elements'][e]['affinity'])


def choose(entries, draw, total=100):
    point = draw() * total
    for key, weight in entries:
        if point < weight:
            return key
        point -= weight
    return None


class Engine:
    def __init__(self, config, draw=None):
        self.c = config
        self.draw = draw or secrets.SystemRandom().random

    def elements(self):
        # Old saved rule snapshots retain exactly their original algorithm.
        weights=self.c.get('element_count_weights')
        if weights is None:
            result=[]
            for _ in range(self.c['element_draws']):
                counts=affinity(self.c,result)
                eligible=[(k,v['weight']) for k,v in self.c['elements'].items() if not v.get('event_only') and enough(counts,v['requires'])]
                result.append(choose(eligible,self.draw,sum(w for _,w in eligible)))
            return result
        entries=[(int(k),w) for k,w in weights.items()]
        count=choose(entries,self.draw,sum(w for _,w in entries))
        repeat_at=None
        if count>1 and self.draw()*100<self.c['element_repeat_birth_percent']:
            repeat_at=1+int(self.draw()*(count-1))
        result=[]
        def nature(key):return self.c['elements'][key]['affinity'] or key
        for slot in range(count):
            repeated=repeat_at is not None and (slot==repeat_at or (slot>repeat_at and self.draw()*100<self.c['element_extra_repeat_percent']))
            seen={nature(key) for key in result}
            counts=affinity(self.c,result)
            eligible=[(k,v['weight']) for k,v in self.c['elements'].items()
                      if not v.get('event_only') and enough(counts,v['requires']) and ((nature(k) in seen)==repeated)]
            if not eligible:raise ValueError(t('No existen elementos compatibles con la regla de repetición.'))
            result.append(choose(eligible,self.draw,sum(w for _,w in eligible)))
        return result

    def candidates(self, group, elements, mains=(), selected=(), family=None):
        counts = affinity(self.c, elements)
        result = []
        for key, item in self.c[group].items():
            if key in selected or not enough(counts, item['requires']):
                continue
            if item.get('main_required') and item['main_required'] not in mains:
                continue
            if family and (item.get('family') != family or not enough(counts, self.c.get('family_requirements', {}).get(family, {}))):
                continue
            p = next((p for m,p in item.get('percent_with_main',{}).items() if m in mains), item['percent'])
            result.append((key,p))
        return result

    def distribution(self, group, elements, mains=(), selected=(), family=None, slot=0):
        entries=self.candidates(group,elements,mains,selected,family)
        balance=self.c.get('balanced')
        if not balance:return entries
        if group=='secondary' and set(mains)&set(balance['taijutsu_mains']):return []
        eligible=dict(entries)
        fixed=balance[group+'_fixed']
        fixed_entries=[]
        for key in eligible:
            if key in fixed:
                value=fixed[key]
                if key=='blood' and 'dokei' in mains:value=self.c['secondary']['blood']['percent_with_main']['dokei']
                fixed_entries.append((key,value))
        if group=='main':
            none=balance['main_none_percent']
            if balance.get('none_requires_secondary_common'):
                secondary=self.candidates('secondary',elements)
                if not any(balance['secondary_weights'].get(k,0)>0 for k,_ in secondary):none=0
            budget=balance['family_percent'] if family else 100-none
        else:budget=balance['secondary_percent_with_main' if mains else 'secondary_percent_without_main'][slot]
        # Fixed rare mass is never renormalized upward when commons are ineligible.
        remaining=max(0,budget-sum(p for _,p in fixed_entries))
        weights=[(key,balance[group+'_weights'][key]) for key in eligible if key not in fixed]
        total=sum(w for _,w in weights)
        return fixed_entries+[(key,remaining*w/total) for key,w in weights] if total else fixed_entries

    def main(self, elements):
        primary = choose(self.distribution('main', elements), self.draw)
        if primary is None:
            return []
        result = [primary]
        family = self.c['main'][primary].get('family')
        # Only explicit families can receive more than one main; never unrelated clans.
        if family:
            for _ in range(self.c['bloodline_slots'] - 1):
                extra = choose(self.distribution('main', elements, selected=result, family=family), self.draw)
                if extra:
                    result.append(extra)
        return result

    def secondary(self, elements, mains):
        taijutsu=self.c.get('balanced',{}).get('taijutsu_mains',['kenichi'])
        if set(mains)&set(taijutsu):
            return []
        result = []
        for slot in range(self.c['bloodline_slots'] - len(mains)):
            item = choose(self.distribution('secondary', elements, mains, result, slot=slot), self.draw)
            if item:
                result.append(item)
        return result


def final_data(state):
    from birth_v8 import is_v8, final
    if is_v8(state.get('rules',{})):return final(state)
    raw = state['results']['elements']
    mains = list(state['results']['main'])
    secondary = list(state['results']['secondary'])
    balanced=state.get('rules',{}).get('balanced')
    if not state.get('birth') and not mains and not secondary and (balanced or len(set(raw)) == 1):
        mains = ['kenichi']
    lee = bool(set(mains)&set(balanced['taijutsu_mains'] if balanced else ['kenichi']))
    result = dict(elements=list(dict.fromkeys(raw[:1] if lee else raw)), element_draws=raw,
                main=mains[0] if mains else 'none', mains=mains, secondary=[] if lee else secondary,
                assignment_complete=True, assignment_version=5, chakra_usable=not lee,
                assignment_rules=state['rules_hash'])
    if state.get('birth'):
        birth=state['birth']
        kind=('both' if mains and secondary else 'main_only' if mains else 'secondary_only' if secondary else 'neither') if birth.get('free_spins') else birth['type']
        result.update(assignment_version=72 if birth.get('free_spins') else 71,birth_type=kind,birth_village=birth['village'],
                      birth_path=birth.get('path','shinobi'),samurai_eligible=bool(not mains and secondary and raw))
    return result


class AssignmentStore:
    def __init__(self, registration_store, config):
        self.reg = registration_store
        self.db = registration_store.db
        self.config = config
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS assignments (
          guild INTEGER NOT NULL, user INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
          state TEXT NOT NULL, PRIMARY KEY(guild,user));
        CREATE TABLE IF NOT EXISTS assignment_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER, user INTEGER, actor INTEGER,
          action TEXT, detail TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
        ''')

        from birth_store_v8 import setup
        setup(self.db)
        from wx_rewards.villages import setup as setup_villages
        setup_villages(self.db)

    def get(self, guild, user):
        row = self.db.execute('SELECT * FROM assignments WHERE guild=? AND user=?',(guild,user)).fetchone()
        return dict(row, state=json.loads(row['state'])) if row else None

    def fresh_state(self):
        return dict(stage=0, used={s:0 for s in STAGES}, results={}, frozen=False,
                    rules=self.config, rules_hash=fingerprint(self.config))

    def audit(self, guild, user, actor, action, detail):
        self.db.execute('INSERT INTO assignment_audit(guild,user,actor,action,detail) VALUES(?,?,?,?,?)',
                        (guild,user,actor,action,json.dumps(detail,ensure_ascii=False)))

    def require_registration(self, guild, user):
        row = self.reg.find(guild,user)
        if not row or row['state'] not in EDITABLE:
            raise ValueError(t('Crea primero tu ficha con /mi_ficha. Debe estar en borrador o devuelta para corregir.'))
        if row['data'].get('catalog_version') != 5:
            raise ValueError(t('Ficha anterior conservada. Solo el dueño puede habilitar una nueva asignación con /asignacion reiniciar.'))
        return row

    def start(self, guild, user):
        self.require_registration(guild,user)
        with self.db:
            initial_revision=0
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='reward_settings'").fetchone():
                epoch=self.db.execute('SELECT epoch FROM reward_settings WHERE guild=?',(guild,)).fetchone()
                initial_revision=(epoch[0] if epoch else 0)*(1<<40)
            self.db.execute('INSERT OR IGNORE INTO assignments(guild,user,revision,state) VALUES(?,?,?,?)',
                            (guild,user,initial_revision,json.dumps(self.fresh_state())))
        self.upgrade_open_birth(guild,user)
        from birth_store_v8 import repair_unstarted_order
        repair_unstarted_order(self,guild,user)
        from wx_rewards.villages import reserve
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current=self.get(guild,user)
            if current and current['state'].get('birth'):
                reserve(self.db,guild,user,current['state']['birth']['village'])
        return self.get(guild,user)

    def upgrade_open_birth(self,guild,user):
        if self.config.get('birth_policy',{}).get('version')!=2:return
        from birth_v72 import upgrade_config
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.get(guild,user)
            if not row:return
            state=row['state']
            if state['stage']==3 or state['frozen'] or state['rules'].get('birth_policy',{}).get('version')!=1:return
            previous=json.loads(json.dumps(state))
            state['rules']=upgrade_config(state['rules']);state['rules_hash']=fingerprint(state['rules'])
            if state.get('birth'):
                state['birth'].update(type='free',free_spins=True)
            # Revisit an automatically empty main only if no later spins exist.
            if state['stage']==2 and state['used']['main']==0 and not state['results'].get('main') and state['used']['secondary']==0:
                state['stage']=1
            for phase in range(state['stage'],3):
                name=STAGES[phase]
                if state['used'][name]==0:state['results'].pop(name,None)
            self.db.execute('UPDATE assignments SET revision=revision+1,state=? WHERE guild=? AND user=?',(json.dumps(state),guild,user))
            self.audit(guild,user,0,'upgrade_free_spins',{'previous':previous,'rules':state['rules_hash']})

    def mutate(self, guild, user, revision, stage, action, draw=None):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            reg = self.require_registration(guild,user)
            row = self.get(guild,user)
            if not row or row['revision'] != revision:
                raise ValueError(t('Este panel cambió. Abre /asignacion iniciar para continuar sin perder giros.'))
            state = row['state']
            if state['frozen'] or state['stage'] == 3 or state['stage'] != stage:
                raise ValueError(t('Sección bloqueada, confirmada o todavía no disponible.'))
            from birth_v8 import is_v8
            v8=is_v8(state['rules'])
            name = state.get('order',STAGES)[stage]
            from birth_v71 import engine_for, has_phase
            if state['rules'].get('birth_policy') and not state.get('birth'):
                raise ValueError(t('Selecciona primero la aldea de origen.'))
            if v8:
                from birth_store_v8 import mutate
                name=mutate(self,guild,user,state,action,draw)
            elif action == 'spin':
                if state.get('birth') and not has_phase(state['birth'],name):
                    raise ValueError(t('Tu nacimiento no incluye esta sección. Confirma para continuar.'))
                if state['used'][name] >= 3:
                    raise ValueError(t('Ya usaste tus tres giros.'))
                engine = engine_for(state['rules'],draw,state.get('birth'))
                if name == 'elements': result = engine.elements()
                elif name == 'main': result = engine.main(state['results']['elements'])
                else: result = engine.secondary(state['results']['elements'],state['results']['main'])
                state['results'][name] = result
                state['used'][name] += 1
            elif action == 'confirm':
                if name not in state['results']:
                    raise ValueError(t('Debes girar al menos una vez antes de confirmar.'))
                state['stage'] += 1
                if state.get('birth') and state['stage']<3:
                    next_name=STAGES[state['stage']]
                    if not has_phase(state['birth'],next_name):state['results'][next_name]=[]
            else:
                raise ValueError(t('Acción inválida.'))
            self.db.execute('UPDATE assignments SET revision=revision+1,state=? WHERE guild=? AND user=?',
                            (json.dumps(state),guild,user))
            self.audit(guild,user,user,action,{'section':name,'used':state['used'][name],
                                            'result':state['results'][name],'rules':state['rules_hash']})
            if state['stage'] == 3:
                data = dict(reg['data']);data.pop('clan',None);data.update(final_data(state))
                self.db.execute('UPDATE registrations SET data=?,revision=revision+1,updated=CURRENT_TIMESTAMP WHERE id=?',
                                (json.dumps(data),reg['id']))
                self.reg.db.execute('INSERT INTO audit(registration,actor,action,detail) VALUES(?,?,?,?)',
                                    (reg['id'],user,'assignment_complete',json.dumps(final_data(state))))
        return self.get(guild,user)

    def admin(self, guild, user, actor, revision, action, reason, registration_revision):
        if not reason.strip() or len(reason) > 180:
            raise ValueError(t('Escribe un motivo de 1 a 180 caracteres.'))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.get(guild,user)
            reg = self.reg.find(guild,user)
            if not reg or reg['revision'] != registration_revision or (row['revision'] if row else -1) != revision:
                raise ValueError(t('La ficha o asignación cambió. Repite el comando.'))
            if action == 'reset':
                if reg['state'] not in EDITABLE:
                    raise ValueError(t('Primero reabre la ficha con /registro reabrir. Así se retira su publicación anterior.'))
                from birth_store_v8 import import_history
                from birth_v8 import is_v8
                if is_v8(self.config):import_history(self,guild,self.config)
                state = self.fresh_state()
                self.db.execute('DELETE FROM birth_v8_claims WHERE guild=? AND user=?',(guild,user))
                data = {k:v for k,v in reg['data'].items() if k not in PROTECTED and k != 'clan'}
                from identity_domain import clear_identity
                clear_identity(data)
                data['catalog_version'] = 5
                self.db.execute('UPDATE registrations SET data=?,revision=revision+1 WHERE id=?',(json.dumps(data),reg['id']))
            elif action in ('freeze','unfreeze'):
                if not row or row['state']['stage'] == 3:
                    raise ValueError(t('No hay una asignación en curso. Para bloquear una ficha terminada usa /registro cerrar.'))
                state = row['state'];state['frozen'] = action == 'freeze'
            else:
                raise ValueError(t('Acción desconocida.'))
            self.audit(guild,user,actor,action,{'reason':reason,'previous':row,'registration':reg['id']})
            self.db.execute('INSERT INTO assignments(guild,user,revision,state) VALUES(?,?,?,?) '
                            'ON CONFLICT(guild,user) DO UPDATE SET revision=excluded.revision,state=excluded.state',
                            (guild,user,revision+1,json.dumps(state)))
        return self.get(guild,user)

    def choose_village(self,guild,user,revision,village,draw=None):
        from birth_v71 import new_birth
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.require_registration(guild,user)
            row=self.get(guild,user)
            if not row or row['revision']!=revision:raise ValueError(t('Panel desactualizado. Abre /asignacion iniciar.'))
            state=row['state']
            if state['frozen'] or state['stage']!=0 or state.get('birth') or any(state['used'].values()):
                raise ValueError(t('El origen ya está fijado o la asignación está bloqueada.'))
            if not state['rules'].get('birth_policy'):raise ValueError(t('Esta asignación conserva sus reglas anteriores.'))
            from wx_rewards.villages import reserve, canonical, normalize
            village=canonical(village);normalize(state['rules'])
            reserve(self.db,guild,user,village)
            state['rules_hash']=fingerprint(state['rules'])
            state['birth']=new_birth(state['rules'],village,draw or secrets.SystemRandom().random)
            from birth_v8 import is_v8
            if is_v8(state['rules']):
                from birth_store_v8 import origin
                origin(self,guild,user,state,draw)
            self.db.execute('UPDATE assignments SET revision=revision+1,state=? WHERE guild=? AND user=?',(json.dumps(state),guild,user))
            self.audit(guild,user,user,'birth_origin',state['birth'])
        return self.get(guild,user)

    def choose_path(self,guild,user,revision,path):
        from birth_v71 import PATHS
        if path not in PATHS:raise ValueError(t('Camino desconocido.'))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            reg=self.require_registration(guild,user);row=self.get(guild,user)
            if not row or row['revision']!=revision:raise ValueError(t('La asignación cambió. Repite el comando.'))
            state=row['state']
            if state['stage']!=3 or state['frozen'] or not state.get('birth'):raise ValueError(t('Termina primero el nacimiento v7.1.'))
            if state['birth'].get('path_confirmed'):raise ValueError(t('El camino ya fue confirmado; solicita revisión al dueño si necesitas cambiarlo.'))
            if path!='shinobi' and not final_data(state)['samurai_eligible']:
                raise ValueError(t('Samurái/respiraciones requiere elementos, secundario y ningún principal.'))
            state['birth'].update(path=path,path_confirmed=True)
            data=dict(reg['data']);data.update(final_data(state))
            self.db.execute('UPDATE assignments SET revision=revision+1,state=? WHERE guild=? AND user=?',(json.dumps(state),guild,user))
            self.db.execute('UPDATE registrations SET data=?,revision=revision+1,updated=CURRENT_TIMESTAMP WHERE id=?',(json.dumps(data),reg['id']))
            self.audit(guild,user,user,'birth_path',{'path':path})
        return self.get(guild,user)

    def history(self, guild, user):
        return [dict(r) for r in self.db.execute('SELECT * FROM assignment_audit WHERE guild=? AND user=? ORDER BY id DESC LIMIT 15',(guild,user))]
