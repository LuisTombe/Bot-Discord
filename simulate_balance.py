"""Offline reproducible simulation of legal sequential player decisions; no live data."""
import json,random
from collections import Counter
from pathlib import Path
from assignment_domain import Engine,load_config,final_data
RARE={'rinnegan','tengoku','sengoku','cobra','jayramaki'}
def sample(engine,policy):
    from birth_v8 import is_v8
    if is_v8(engine.c):
        from birth_simulation_v8 import sample_v8
        return sample_v8(engine,policy)
    original=engine
    if engine.c.get('birth_policy'):
        from birth_v71 import new_birth, engine_for
        birth=new_birth(engine.c,engine.c.get('_simulation_village','konoha'),engine.draw)
        original.last_birth=birth
        engine=engine_for(engine.c,engine.draw,birth)
    attempts=1 if policy=='first' else 3
    for turn in range(attempts):
        e=engine.elements()
        if len(e)>=(3 if policy=='hunt' else 2):break
    for turn in range(attempts):
        m=engine.main(e)
        if (bool(set(m)&RARE) if policy=='hunt' else bool(m)):break
    for turn in range(attempts):
        s=engine.secondary(e,m)
        if s or 'kenichi' in m or 'ryuji-kenichi' in m:break
    return e,m,s

def run(config,n,policy,seed=5304):
    engine=Engine(config,random.Random(seed).random);counts=Counter();mains=Counter();seconds=Counter();elements=Counter()
    for _ in range(n):
        e,m,s=sample(engine,policy)
        final=final_data(dict(results=dict(elements=e,main=m,secondary=s),rules=config,rules_hash='simulation',birth=getattr(engine,'last_birth',None)))
        fm=final['mains'];fs=final['secondary'];lee=bool(set(fm)&{'kenichi','ryuji-kenichi'})
        category='lee_gai' if lee else 'both' if fm and fs else 'main_only' if fm else 'secondary_only' if fs else 'empty'
        counts[category]+=1;mains.update(fm);seconds.update(fs);elements[str(len(e))]+=1
    return {'n':n,'policy':policy,'types':dict(counts),'mains':dict(mains),'secondary':dict(seconds),'elements':dict(elements)}
if __name__=='__main__':
    import argparse,sqlite3
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',default='data/worldx-v2.sqlite3')
    parser.add_argument('--mode',choices=['hardcore','normal','easy'])
    parser.add_argument('--count',type=int,default=1000)
    parser.add_argument('--seed',type=int,default=5304)
    parser.add_argument('--output',default='balance_simulation.json')
    args=parser.parse_args()
    db=sqlite3.connect(Path(args.database).resolve().as_uri()+'?mode=ro',uri=True)
    rows=db.execute('SELECT config FROM balance_active').fetchall();db.close()
    if len(rows)!=1:parser.error('Se requiere exactamente una configuración activa en esta base.')
    if not 1<=args.count<=100000:parser.error('count debe estar entre 1 y 100000')
    c=json.loads(rows[0][0])
    if args.mode:c['birth_v8']['strategy']=args.mode
    results=[run(c,args.count,p,args.seed) for p in ('first','practical','hunt')]
    Path(args.output).write_text(json.dumps({'mode':c['birth_v8'].get('strategy','hardcore'),'profile':c['birth_v8'].get('hardcore_profile'),'seed':args.seed,'results':results},indent=2))
    for r in results:print(r['policy'],r['types'])
