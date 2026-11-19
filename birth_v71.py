"""Birth-type gates are sampled once, independently of three ability rerolls.
Legacy Engine remains intact for saved snapshots. No dynamic population quotas.
"""
from i18n import t
import math
from assignment_domain import Engine, choose

TYPES = {'main_only': 'Linaje principal', 'both': 'Principal y secundario',
         'secondary_only': 'Camino elemental', 'neither': 'Ninja sin bloodlines'}
PATHS = {'shinobi': 'Shinobi', 'samurai': 'Samurái', 'breathing': 'Aspirante a respiraciones'}

def new_birth(config, village, draw):
    from birth_v8 import is_v8, fresh_birth
    if is_v8(config):
        if village not in config['birth_policy']['villages']:raise ValueError(t('Selecciona una aldea de origen válida.'))
        return fresh_birth(village,config,draw)
    if village not in config['birth_policy']['villages']:
        raise ValueError(t('Selecciona una aldea de origen válida.'))
    if config['birth_policy'].get('version')==2:
        return {'type':'free','village':village,'path':'shinobi','free_spins':True}
    entries=list(config['birth_policy']['types'].items())
    return {'type':choose(entries,draw,100), 'village':village, 'path':'shinobi'}

def has_phase(birth, group):
    return birth.get('free_spins',False) or group=='elements' or birth['type'] in (('main_only','both') if group=='main' else ('secondary_only','both'))

def engine_for(config, draw=None, birth=None):
    from birth_v8 import is_v8, BirthEngineV8
    if is_v8(config):return BirthEngineV8(config,draw,birth)
    if config.get('birth_policy'):
        if not birth:raise ValueError(t('Selecciona primero la aldea de origen.'))
        if config['birth_policy'].get('version')==2:
            from birth_v72 import FreeBirthEngine
            return FreeBirthEngine(config,draw,birth)
        return BirthEngine(config,draw,birth)
    return Engine(config,draw)

class BirthEngine(Engine):
    def __init__(self,config,draw=None,birth=None):
        super().__init__(config,draw)
        self.birth=birth

    def candidates(self,group,elements,mains=(),selected=(),family=None):
        entries=super().candidates(group,elements,mains,selected,family)
        entries=[(k,p) for k,p in entries if not self.c[group][k].get('event_only')]
        if group=='main' and self.birth['type']=='both':
            entries=[(k,p) for k,p in entries if k not in self.c['balanced']['taijutsu_mains']]
        return entries

    def viable_elements(self,elements):
        # A guaranteed phase must have an ordinary filler. Rare candidates never
        # become guaranteed when their compatible common pool is empty.
        for group in ('main','secondary'):
            if has_phase(self.birth,group):
                if not any(self.c['balanced'][group+'_weights'].get(k,0)>0
                           for k,_ in self.candidates(group,elements)):
                    return False
        return True

    def elements(self):
        # Conditional sampling retains original distribution over viable outcomes.
        # Bounded failure leaves the transaction/spin untouched; never invents elements.
        for _ in range(2000):
            values=super().elements()
            if self.viable_elements(values):return values
        raise ValueError(t('No se pudo obtener una combinación viable. Staff: revisa pesos y requisitos; el giro no se gastó.'))

    def distribution(self,group,elements,mains=(),selected=(),family=None,slot=0):
        if not has_phase(self.birth,group):return []
        if group=='secondary' and set(mains)&set(self.c['balanced']['taijutsu_mains']):return []
        entries=self.candidates(group,elements,mains,selected,family)
        balance=self.c['balanced'];fixed=balance[group+'_fixed']
        fixed_entries=[];weights=[]
        for key,_ in entries:
            if key in fixed:
                value=fixed[key]
                if key=='blood' and 'dokei' in mains:
                    value=self.c['secondary'][key]['percent_with_main']['dokei']
                fixed_entries.append((key,value))
            else:
                weight=balance[group+'_weights'].get(key,0)
                if self.birth['village'] in self.c[group][key].get('villages',[]):
                    weight*=self.c['birth_policy']['village_multiplier']
                weights.append((key,weight))
        budget=100
        if family:budget=balance['family_percent']
        elif group=='secondary' and slot:
            budget=balance['secondary_percent_with_main' if mains else 'secondary_percent_without_main'][slot]
        fixed_total=sum(p for _,p in fixed_entries)
        if fixed_total>budget+1e-9:raise ValueError(t('Los porcentajes raros exceden el presupuesto del slot.'))
        total=sum(w for _,w in weights)
        if not total and not family and slot==0:
            raise ValueError(t('No hay candidatos comunes compatibles para esta fase. El giro no se gastó.'))
        return fixed_entries+[(k,(budget-fixed_total)*w/total) for k,w in weights] if total else fixed_entries

    def main(self,elements):
        if not has_phase(self.birth,'main'):return []
        primary=choose(self.distribution('main',elements),self.draw)
        if primary is None:raise ValueError(t('Configuración principal sin resultado viable.'))
        result=[primary];family=self.c['main'][primary].get('family')
        limit=3 if self.birth['type']=='both' else 4
        if family:
            for _ in range(limit-1):
                extra=choose(self.distribution('main',elements,selected=result,family=family),self.draw)
                if extra:result.append(extra)
        return result

    def secondary(self,elements,mains):
        if not has_phase(self.birth,'secondary'):return []
        return super().secondary(elements,mains)


def validate_birth(c):
    p=c['birth_policy']
    if p.get('version') not in (1,2):raise ValueError(t('Versión de política de nacimiento desconocida.'))
    for key in ('secondary_percent_with_main','secondary_percent_without_main'):
        if p['version']==1 and c['balanced'][key][0]!=100:raise ValueError(t('El primer secundario de un tipo con secundarios debe conservar presupuesto 100%.'))
    if p['version']==1 and (set(p['types'])!=set(TYPES) or any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in p['types'].values()) or abs(sum(p['types'].values())-100)>1e-8):
        raise ValueError(t('Los cuatro tipos de nacimiento deben sumar exactamente 100%.'))
    if not isinstance(p['village_multiplier'],(int,float)) or not math.isfinite(p['village_multiplier']) or not 1<=p['village_multiplier']<=1.5:
        raise ValueError(t('La afinidad de aldea debe estar entre 1 y 1.5.'))
    if not p['villages'] or len(p['villages'])>25:raise ValueError('Define entre 1 y 25 aldeas de origen.')
    for group in ('main','secondary'):
        for key,item in c[group].items():
            if not set(item.get('villages',[]))<=set(p['villages']):raise ValueError('Afinidad de aldea desconocida: '+key)
        # A requirement-free common main protects all possible element rolls.
    if not any(w>0 and not c['main'][k]['requires'] and k not in c['balanced']['taijutsu_mains']
               and not c['main'][k].get('event_only') for k,w in c['balanced']['main_weights'].items()):
        raise ValueError(t('Conserva al menos un principal común sin requisitos.'))
    if p['version']==2:return
    # Verify reachability under actual element-count and repetition settings.
    from itertools import product
    for kind,pct in p['types'].items():
        if not pct:continue
        engine=BirthEngine(c,birth={'type':kind,'village':next(iter(p['villages']))})
        possible=False
        for n in range(1,5):
            if not c['element_count_weights'][str(n)]:continue
            for seq in product(('earth','water','fire','wind','lightning'),repeat=n):
                repeats=n-len(set(seq))
                if repeats and c['element_repeat_birth_percent']==0:continue
                if repeats>1 and c['element_extra_repeat_percent']==0:continue
                if n>1 and not repeats and c['element_repeat_birth_percent']==100:continue
                if engine.viable_elements(seq):possible=True;break
            if possible:break
        if not possible:raise ValueError(t('No existe combinación elemental alcanzable para ')+TYPES[kind])
