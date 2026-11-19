from i18n import format_fragment, t
import json
from missions_domain import timestamp,formatted
from evidence import safe_path

BLUE={'azul_hierba':'Arrancar hierba','azul_gato':'Recuperar gato','azul_oficina':'Limpiar oficina','azul_compras':'Entregar compras','azul_mensaje':'Entregar mensaje','azul_grafiti':'Quitar grafitis','azul_limpieza':'Limpieza de la aldea'}

def install(system,eco,ctx):
    ms=system.store;original=ms.validate_mission;auth=ms.validate_auth;submit=ms.submit;decision=ms.decision;totals=ms.totals
    system.config['types'].update({k:{'label':v,'base_rank':'D','translations':system.config['types'].get(k,{}).get('translations',{})} for k,v in BLUE.items()})
    def validate_auth(row):
        profile=eco.profile(row['guild'],row['owner'])
        if profile and profile['active']:
            rank=profile['rank'];given=row['data'].get('rank')
            if given!=rank and {given,rank}!={'subkage','sannin'}:raise ValueError(t('Tu rango debe coincidir con el rol económico acreditado.'))
            village=eco.village(row['guild'],profile['village'])
            row['data']['village']=village['name']
        return auth(row)
    def validate(row):
        if row['session']==0:
            d=row['data']
            if d.get('type') not in BLUE:raise ValueError(t('Fuera de Open World solo se admiten los encargos azules D.'))
            a=timestamp(d.get('start',''));b=timestamp(d.get('end',''))
            if a>b or b>ms.now():raise ValueError(t('Horario inválido: el final debe ser posterior al inicio y no estar en el futuro.'))
            if d.get('members')!=[row['owner']]:raise ValueError(t('Los encargos azules se reportan individualmente.'))
            reg=ms.registered(row['guild'],row['owner']);profile=eco.player(row['guild'],row['owner'])
            refs=[{'user':row['owner'],'authorization':0,'username':reg['data']['roblox_name'],'rank':profile['rank'],'stats':t('No aplica'),'level':t('No aplica')}]
            return 'D',refs
        rank,refs=original(row)
        override=row['data'].get('classification')
        if override:rank=override['rank']
        return rank,refs
    def evidence(row):
        proofs=row['data'].get('proofs') or ([{'path':row['proof'],'digest':row['digest']}] if row['proof'] else [])
        if len(proofs)<1:raise ValueError(t('Adjunta al menos una captura completa.'))
        digests=set()
        for proof in proofs:
            if not safe_path(ms.root,proof['path']).is_file():raise ValueError(t('Falta una captura archivada.'))
            if proof['digest'] in digests:raise ValueError(t('No repitas la misma imagen en las pruebas.'))
            digests.add(proof['digest'])
        if row['kind']=='mission':
            conflict=ms.proof_conflict(row)
            if conflict:raise ValueError(f'Una imagen ya pertenece al reporte #{conflict}. No repitas la misión.')
    def submit_new(ident,revision,user):
        row=ms.get(ident);evidence(row);return submit(ident,revision,user)
    def decide(ident,revision,actor,action,reason,demo=False):
        row=ms.get(ident)
        if action=='approve':
            evidence(row)
            for user in row['data'].get('members',[row['owner']]):
                death=eco.db.execute('SELECT MAX(ended) FROM wx_lives WHERE guild=? AND user=?',(row['guild'],user)).fetchone()[0]
                if death and row['created']<=death:
                    raise ValueError(t('El reporte pertenece a un personaje fallecido. Revisa los participantes y el historial.'))
        return decision(ident,revision,actor,action,reason,actor==ctx.get('BOT_OWNER'))
    def current_totals(g,u,session=None):
        last=eco.db.execute('SELECT MAX(ended) FROM wx_lives WHERE guild=? AND user=?',(g,u)).fetchone()[0]
        if not last:return totals(g,u,session)
        result={r:0 for r in 'DCBAS'}
        for row in ms.all(g):
            if row['state']=='approved' and row['kind']=='mission' and row['created']>last and (session is None or row['session']==session) and u in [p['user'] for p in row['participants']]:result[row['data']['mission_rank']]+=1
        return result
    ms.validate_auth=validate_auth;ms.validate_mission=validate;ms.submit=submit_new;ms.decision=decide;ms.totals=current_totals
