"""Three independent rerolls per section. No immutable bloodline type gate."""
from i18n import t
import copy
from assignment_domain import Engine, choose
from birth_v71 import BirthEngine


def upgrade_config(config):
    c=copy.deepcopy(config)
    if c.get('birth_policy',{}).get('version')!=1:return c
    policy=c['birth_policy'];types=policy.pop('types')
    policy['version']=2
    c['balanced']['main_none_percent']=types['secondary_only']+types['neither']
    secondary_budget=types['both']+types['secondary_only']
    for key in ('secondary_percent_with_main','secondary_percent_without_main'):
        c['balanced'][key][0]=secondary_budget
    c['balanced']['none_requires_secondary_common']=False
    c['balance_revision']='7.2 · Tres giros libres'
    return c


class FreeBirthEngine(BirthEngine):
    def elements(self):
        # No hidden birth type: do not reroll elements behind the player's back.
        return Engine.elements(self)

    def distribution(self,group,elements,mains=(),selected=(),family=None,slot=0):
        if group=='secondary' and set(mains)&set(self.c['balanced']['taijutsu_mains']):return []
        balance=self.c['balanced'];entries=self.candidates(group,elements,mains,selected,family)
        fixed=balance[group+'_fixed'];rare=[];weights=[]
        for key,_ in entries:
            if key in fixed:
                value=fixed[key]
                if key=='blood' and 'dokei' in mains:
                    value=self.c['secondary'][key]['percent_with_main']['dokei']
                rare.append((key,value))
            else:
                weight=balance[group+'_weights'].get(key,0)
                if self.birth['village'] in self.c[group][key].get('villages',[]):
                    weight*=self.c['birth_policy']['village_multiplier']
                weights.append((key,weight))
        if group=='main':budget=balance['family_percent'] if family else 100-balance['main_none_percent']
        else:budget=balance['secondary_percent_with_main' if mains else 'secondary_percent_without_main'][slot]
        rare_total=sum(p for _,p in rare)
        if rare_total>budget+1e-9:raise ValueError(t('Los porcentajes fijos exceden el presupuesto del slot.'))
        total=sum(w for _,w in weights)
        # Missing ordinary mass remains empty, never boosts an exceptional rare.
        return rare+[(k,(budget-rare_total)*w/total) for k,w in weights] if total else rare

    def main(self,elements):
        return Engine.main(self,elements)

    def secondary(self,elements,mains):
        if len(mains)>=4:return []
        first=choose(self.distribution('secondary',elements,mains),self.draw)
        if first is None:return []
        result=[first]
        for slot in range(1,4-len(mains)):
            extra=choose(self.distribution('secondary',elements,mains,result,slot=slot),self.draw)
            if extra:result.append(extra)
        return result
