"""World X v8: explicit constraints, independent rerolls, no hidden birth-class gate.
Pure solver is shared by SQLite operations and isolated simulations.
"""
import copy
import itertools
import math
import secrets
from collections import Counter
from assignment_domain import choose
from i18n import t
import hardcore_a
NATURES=('earth','water','fire','wind','lightning')
PHASES=('elements','main','secondary')
LIFETIME_UNIQUE=frozenset(['ashura-akuma', 'shindai-akuma', 'aidens-son-mud', 'apollo-sand', 'arahaki-jokei', 'ashen-storm', 'azim-senko', 'bankai-akuma', 'borumaki', 'cobra', 'dio-senko', 'doom-shadow', 'eternal', 'giovanni-shizen', 'hair', 'jayramaki', 'jotaro-shizen', 'kabu-cobra', 'mecha-spirit', 'narumaki', 'odin-saberu', 'raion-akuma', 'raion-rengoku', 'rengoku', 'riser-akuma', 'rune-koncho', 'rykan-shizen', 'ryuji-kenichi', 'satori-akuma', 'sengoku', 'shiro-glacier', 'shiver-akuma', 'tengoku', 'tetsuo-kaijin', 'vine', 'yang-narumaki'])

def is_v8(c):return c.get('birth_policy',{}).get('version')==3

def validate(c):
    if c.get('spins')!=3 or c.get('bloodline_slots')!=4 or c.get('element_draws')!=4:raise ValueError('v8: 3 spins / 4 slots')
    p=c['birth_v8']
    if p.get('strategy','hardcore') not in ('hardcore','normal','easy'):raise ValueError('Modalidad desconocida')
    def numeric(x,limit=100):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and 0<=x<=limit
    if set(p['element_counts'])!=set('01234') or abs(sum(p['element_counts'].values())-100)>1e-8 or not all(numeric(x) for x in p['element_counts'].values()):raise ValueError('v8: element counts must total 100')
    for k in ['main_percent','repeat_percent','family_extra_percent','inferno_variant_percent']:
        if not numeric(p[k]):raise ValueError('v8: invalid '+k)
    for k,n in [('secondary_with_main',3),('secondary_without_main',4)]:
        if len(p[k])!=n or not all(numeric(x) for x in p[k]):raise ValueError('v8: invalid '+k)
    if not 1<=p['affinity_multiplier']<=1.5:raise ValueError('v8: affinity multiplier 1..1.5')
    if type(p['rare_caps_per_block']) is not int or not 1<=p['rare_caps_per_block']<=20:raise ValueError('v8: exclusive cohort cap 1..20')
    if p['quota_block']!=100 or p['quota_lineages']!=['rengoku','sengoku']:raise ValueError('v8: quota policy must be 1 Rengoku + 1 Sengoku per 100 tickets')
    if not c['birth_policy']['villages'] or len(c['birth_policy']['villages'])>25:raise ValueError('v8: villages')
    for group in ['main','secondary']:
        for key,item in c[group].items():
            if not numeric(item.get('hardcore_a_weight',1),10000):raise ValueError('Peso Hardcore A inválido: '+key)
            if not item.get('label') or len(item['label'])>80:raise ValueError('v8: label '+key)
            if not numeric(item.get('weight',0),10000) or not numeric(item.get('fixed_percent',0)):raise ValueError('v8: weight '+key)
            if item.get('cap') is not None and (type(item['cap']) is not int or item['cap']<1):raise ValueError('v8: cap '+key)
            if group=='secondary' and item.get('has_mode'):raise ValueError('v8: mode must be primary '+key)
            for req in item.get('requires_any',[item['requires']]):
                if not set(req)<=set(NATURES) or any(type(n) is not int or not 1<=n<=4 for n in req.values()) or sum(req.values())>4:raise ValueError('v8: requirements '+key)
            if not set(item.get('village_weights',{}))<=set(c['birth_policy']['villages']) or not all(numeric(x,10000) for x in item.get('village_weights',{}).values()):raise ValueError('v8: village weights '+key)
            for g,field in [('main','mandatory_main'),('secondary','mandatory_secondary')]:
                if not set(item.get(field,[]))<=set(c[g]):raise ValueError('v8: missing dependency '+key)
    if hardcore_a.active(c) and not any(v.get('hardcore_a_weight',1)>0 and not v.get('requires') and not v.get('special') and not v.get('cap') and k not in LIFETIME_UNIQUE and not v.get('event_only') for k,v in c['main'].items()):raise ValueError('Hardcore A requiere al menos un principal común sin requisitos con peso positivo')
    if sum(v.get('fixed_percent',0) for v in c['main'].values())>p['main_percent']:raise ValueError('v8: rare mass exceeds main budget')
    if sum(v.get('fixed_percent',0) for v in c['secondary'].values())>min(p['secondary_with_main']+p['secondary_without_main']):raise ValueError('v8: rare mass exceeds secondary budget')
    if not any(v.get('weight',0)>0 and not v['requires'] for v in c['main'].values()):raise ValueError('v8: keep common requirement-free clans')


def fresh_birth(village,config=None,draw=None):
    b=dict(type='free',village=village,path='shinobi',free_spins=True)
    if config and hardcore_a.active(config):
        b.update(hardcore_a.origin(draw or secrets.SystemRandom().random))
    elif config and config['birth_v8'].get('strategy')=='easy':
        x=(draw or secrets.SystemRandom().random)()
        b['population_group']='principal' if x<.8 else 'secondary' if x<.95 else 'lee'
    return b

def historical_keys(c,mains,seconds):
    keys={}
    for group,values in [('main',mains),('secondary',seconds)]:
        for k in values:
            v=c[group][k]
            if v.get('cap') or k in LIFETIME_UNIQUE:
                keys['history:'+k]=1
    return keys

def phase(state,index=None):return state.get('order',PHASES)[state['stage'] if index is None else index]

def capacity_keys(c,mains,seconds,birth=None):
    keys={}
    for group,values in [('main',mains),('secondary',seconds)]:
        for k in values:
            v=c[group][k]
            if v.get('cap'):keys['history:'+k]=1
    if keys and birth and birth.get('ticket') and c['birth_v8'].get('strategy','hardcore')=='hardcore' and not hardcore_a.active(c):
        keys['@rare:'+str((birth['ticket']-1)//100)]=c['birth_v8']['rare_caps_per_block']
    keys.update(historical_keys(c,mains,seconds))
    return keys

class BirthEngineV8:
    def __init__(self,config,draw=None,birth=None,state=None,occupied=None):
        self.c=config;self.p=config['birth_v8'];self.draw=draw or secrets.SystemRandom().random
        self.birth=birth or fresh_birth(next(iter(config['birth_policy']['villages'])))
        self.state=state or {'results':{},'birth':self.birth}
        self.occupied=occupied or {}

    def closure(self,results,extras=None):
        """Return reserved bloodlines + feasible requirement alternatives, or None.
        Missing phases may be filled later; present phases (including []) are binding.
        """
        c=self.c;r=results;m=list(r.get('main',[]));s=list(r.get('secondary',[]))
        bucket=self.birth.get('population_group')
        if bucket=='lee':
            if 'main' in r and (len(m)!=1 or c['main'][m[0]].get('special')!='taijutsu'):return None
            if 'main' not in r:m=['kenichi']
        if bucket in ('secondary','no_principal') and m:return None
        if bucket=='principal' and 'main' in r and (not m or any(c['main'][k].get('special')=='taijutsu' for k in m)):return None
        if bucket=='secondary' and 'secondary' in r and not s:return None
        forced=self.birth.get('quota')
        if forced:
            if 'main' in r and forced not in m:return None
            if forced not in m:m.insert(0,forced)
        if 'elements' in r and not r['elements'] and not m:
            if 'main' in r:return None
            m=['kenichi']
        for k in list(m):
            for dep in c['main'][k].get('mandatory_main',[]):
                if dep not in m:
                    if 'main' in r:return None
                    m.append(dep)
        mandatory=[]
        for k in m:mandatory+=c['main'][k].get('mandatory_secondary',[])
        extra=extras if extras is not None else self.state.get('extras',{})
        if extra.get('inferno') and any(c['main'][k].get('akuma') for k in m):mandatory.append('inferno')
        water=r.get('elements',[]).count('water')
        if water>=2 and not set(m)&{'dangan','wanziame'}:mandatory.append('tsunami')
        for dep in mandatory:
            if dep not in s:
                if 'secondary' in r:return None
                s.append(dep)
        if len(m)+len(s)>4 or len(set(m+s))!=len(m+s):return None
        special=next((c['main'][k].get('special') for k in m if c['main'][k].get('special')),None)
        if special:
            if any(self.occupied.get(k,0)>=cap for k,cap in capacity_keys(c,m,s,self.birth).items()):return None
            if len(m)!=1 or (s!=['menza'] if special=='mecha' else bool(s)):return None
            if 'elements' in r and r['elements']:return None
            return dict(main=m,secondary=s,requirements=[{}],all_elements=False,special=special,min_elements=0)
        if 'elements' in r and len(r['elements'])==0:return None
        if 'elements' in r and len(r['elements'])==1 and s:return None
        if 'menza' in s:return None
        if 'inferno' in s and not any(c['main'][k].get('akuma') for k in m):return None
        families={c['main'][k].get('family') for k in m}&{'sand','storm'}
        if families:
            if len(families)>1:return None
            f=next(iter(families))
            if any(c['main'][k].get('family')!=f for k in m) or any(c['secondary'][k].get('family')!=f for k in s):return None
        if 'gold-sand' in s and (any(c['main'][k].get('family')!='sand' for k in m) or any(k!='gold-sand' for k in s)):return None
        for k,cap in capacity_keys(c,m,s,self.birth).items():
            if self.occupied.get(k,0)>=cap:return None
        all_elements=any(c['main'][k].get('all_elements') for k in m)
        options=[{}]
        for group,values in [('main',m),('secondary',s)]:
            for k in values:
                reqs=c[group][k].get('requires_any',[c[group][k]['requires']])
                options=[dict(a|b,**{e:max(a.get(e,0),b.get(e,0)) for e in a.keys()|b.keys()}) for a in options for b in reqs]
        bonus=extra.get('elements',[]) if 'minakami' in m else []
        for opt in options:
            for e in bonus:opt[e]=max(1,opt.get(e,0))
        min_elements=max([2 if s else 1]+[c['main'][k].get('min_elements',1) for k in m])
        if all_elements:min_elements=4
        options=[o for o in options if sum(o.values())<=4 or all_elements]
        if 'elements' in r:
            e=r['elements'];cnt=Counter(e)
            options=[o for o in options if (all_elements or all(cnt[k]>=n for k,n in o.items())) and len(e)>= (1 if all_elements else min_elements)]
        if not options:return None
        # Check future principal feasibility before accepting an early element/secondary roll.
        if bucket=='principal' and 'main' not in r:
            feasible=False
            for k,v in c['main'].items():
                if v.get('event_only') or v.get('special')=='taijutsu':continue
                if hardcore_a.active(c) and v.get('hardcore_a_weight',1)<=0:continue
                if self.closure(dict(r,main=self.expanded([k])),{}) is not None:
                    feasible=True;break
            if not feasible:return None
        return dict(main=m,secondary=s,requirements=options,all_elements=all_elements,special=None,min_elements=min_elements)

    def expanded(self,m):
        result=list(m)
        for k in list(result):
            for dep in self.c['main'][k].get('mandatory_main',[]):
                if dep not in result:result.append(dep)
        return result

    def distributions(self,group,results,selected=(),family=None,slot=0):
        if hardcore_a.active(self.c):return hardcore_a.weights(self,group,results,selected,family,slot)
        rare=[];weights=[]
        for k,v in self.c[group].items():
            if k in selected or v.get('event_only') or k=='kenichi':continue
            if family and v.get('family')!=family:continue
            if group=='main':values=self.expanded(list(selected)+[k])
            else:values=list(selected)+[k]
            proposal=dict(results);proposal[group]=values
            if not self.closure(proposal,{} if group=='main' else None):continue
            if self.p.get('strategy')=='normal':
                weights.append((k,1));continue
            fixed=v.get('fixed_percent',1 if v.get('quota') else None)
            if fixed is not None:rare.append((k,fixed));continue
            weight=v.get('village_weights',{}).get(self.birth['village'],v.get('weight',0))
            if 'elements' in results and v['requires']:weight*=self.p['affinity_multiplier']
            if weight:weights.append((k,weight))
        if group=='main':budget=self.p['family_extra_percent'] if family else self.p['main_percent']
        else:
            percentages=self.p['secondary_with_main' if results.get('main') else 'secondary_without_main']
            budget=percentages[min(slot,len(percentages)-1)]
        bucket=self.birth.get('population_group')
        if group=='main' and not family and bucket=='principal':budget=100
        if group=='secondary' and not selected and bucket=='secondary':budget=100
        total=sum(w for _,w in weights);mass=max(0,budget-sum(w for _,w in rare))
        return rare+[(k,mass*w/total) for k,w in weights] if total else rare

    def roll(self,name):
        r=copy.deepcopy(self.state.get('results',{}));r.pop(name,None)
        extras=copy.deepcopy(self.state.get('extras',{}))
        if name=='elements':
            plan=self.closure(r)
            if plan is None:raise ValueError(t('v8.incompatible'))
            if plan['special']:return [],extras
            reqs=plan['requirements'];req=reqs[min(int(self.draw()*len(reqs)),len(reqs)-1)]
            if plan['all_elements']:req={}
            base=[k for k,n in req.items() for _ in range(n)]
            minimum=max(len(base),plan['min_elements'])
            # Completely unconstrained origin may roll zero; zero is the Lee path.
            if 'main' not in r and not plan['main'] and not plan['secondary']:minimum=0
            if self.birth.get('population_group') in ('principal','no_principal'):minimum=max(minimum,1)
            if self.birth.get('population_group')=='secondary':minimum=max(minimum,2)
            weights=[(int(k),w) for k,w in self.p['element_counts'].items() if int(k)>=minimum]
            count=choose(weights,self.draw,sum(w for _,w in weights))
            if count is None:raise ValueError(t('v8.incompatible'))
            if count==0:return [],extras
            for _ in range(200):
                values=list(base)
                while len(values)<count:
                    repeat=bool(values) and self.draw()*100<self.p['repeat_percent']
                    candidates=list(dict.fromkeys(values)) if repeat else [k for k in NATURES if k not in values]
                    if not candidates:candidates=list(NATURES)
                    values.append(candidates[min(int(self.draw()*len(candidates)),len(candidates)-1)])
                if self.closure(dict(r,elements=values)):return values,extras
            raise ValueError(t('v8.incompatible'))
        if name=='main':
            extras={}
            if self.birth.get('population_group')=='lee':
                k=choose(self.distributions('main',r),self.draw)
                return [k or 'kenichi'],extras
            if self.birth.get('population_group') in ('secondary','no_principal'):return [],extras
            if self.birth.get('quota'):return [self.birth['quota']],extras
            if 'elements' in r and not r['elements']:
                options=self.distributions('main',r)
                k=choose(options,self.draw)
                return [k or 'kenichi'],extras
            k=choose(self.distributions('main',r),self.draw)
            values=self.expanded([k]) if k else []
            if k:
                family=self.c['main'][k].get('family')
                if family in ('sand','storm'):
                    for _ in range(3):
                        extra=choose(self.distributions('main',r,values,family),self.draw)
                        if extra:values.append(extra)
                if k!='akuma' and any(self.c['main'][v].get('akuma') for v in values) and self.draw()*100<self.p['inferno_variant_percent']:
                    candidate={'inferno':True}
                    if self.closure(dict(r,main=values),candidate):extras=candidate
                if k=='minakami':
                    for e,p in self.c['main'][k]['optional_elements'].items():
                        if self.draw()*100<p:
                            candidate=copy.deepcopy(extras);candidate.setdefault('elements',[]).append(e)
                            if self.closure(dict(r,main=values),candidate):extras=candidate
            if self.closure(dict(r,main=values),extras) is None:
                # Double water with a previously empty secondary requires Dangan/Wanziame.
                forced=[k for k in ['dangan','wanziame'] if self.closure(dict(r,main=[k]),{})]
                if forced:values=[forced[min(int(self.draw()*len(forced)),len(forced)-1)]]
                else:raise ValueError(t('v8.incompatible'))
            return values,extras
        if name=='secondary' and hardcore_a.active(self.c):return hardcore_a.secondary(self,r,extras)
        if name=='secondary':
            plan=self.closure(r)
            if not plan:raise ValueError(t('v8.incompatible'))
            values=list(plan['secondary'])
            if plan['special'] or ('elements' in r and len(r['elements'])==1):return values,extras
            for slot in range(4-len(plan['main'])-len(values)):
                k=choose(self.distributions('secondary',r,values,slot=slot),self.draw)
                if k:values.append(k)
                elif not values:break
            return values,extras
        raise ValueError('Unknown phase')

    # Legacy read-only callers. Interactive v8 uses state-aware roll().
    def distribution(self,group,elements,mains=(),selected=(),family=None,slot=0):
        r={'elements':elements}
        if group=='secondary':r['main']=list(mains)
        return self.distributions(group,r,selected,family,slot)


def final(state):
    c=state['rules'];r=state['results'];engine=BirthEngineV8(c,birth=state['birth'],state=state)
    plan=engine.closure(r)
    if plan is None:raise ValueError(t('v8.incompatible'))
    m=r['main'];s=r['secondary'];raw=r['elements'];special=plan['special']
    eligible=bool(raw and ((len(raw)==1 and not s and all(not c['main'][k]['requires'] and not c['main'][k].get('special') and not c['main'][k].get('all_elements') and c['main'][k].get('min_elements',1)==1 for k in m)) or (len(raw)>=2 and not m)))
    access=next((c['main'][k].get('secondary_access') for k in m if c['main'][k].get('secondary_access')),None)
    loadout=state.get('loadout',{})
    return dict(elements=list(dict.fromkeys(loadout.get('elements',raw))),element_draws=raw,mains=m,main=m[0] if m else 'none',secondary=loadout.get('secondary',s),birth_secondary=s,
        probability_mode=c['birth_v8'].get('strategy','hardcore'),probability_profile=c['birth_v8'].get('hardcore_profile') if hardcore_a.active(c) else None,secondary_audit=state.get('secondary_audit'),
        assignment_complete=True,assignment_version=80,assignment_rules=state['rules_hash'],chakra_usable=not special,
        birth_type='taijutsu' if special=='taijutsu' else 'mecha' if special else 'both' if m and s else 'main_only' if m else 'secondary_only' if s else 'neither',
        birth_village=state['birth']['village'],birth_path=state['birth'].get('path','shinobi'),samurai_eligible=eligible,
        element_access=list(NATURES) if plan['all_elements'] else list(dict.fromkeys(raw)),secondary_access=access,
        birth_order=list(state.get('order',PHASES)),birth_ticket=state['birth'].get('ticket'))
