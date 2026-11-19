"""Flujo sencillo sin IA. Conserva las tablas, identidades y libros contables existentes."""
import json
from datetime import datetime, timedelta
from missions_domain import BOGOTA, MissionStore, timestamp, classify, stats_values
from economy_domain import MISSION_REWARDS
from simple_transactions import atomic
from wx_market.domain import dump, digest

NORMAL={'normal_azul':('Azul · encargo civil',500),
        'normal_verde':('Verde · combate NPC',1000),
        'normal_roja':('Roja · entrega',750)}

SHINOBI_RANKS = {
    'academia': ('Estudiante de academia', 0),
    'genin': ('Genin', 1),
    'chunin': ('Chunnin', 2),
    'medical': ('Medical Corps', 2),
    'police': ('Police Corps', 2),
    'jounin': ('Jounin', 3),
    'anbu': ('Anbu', 3),
    'sensei': ('Jounin Sensei', 4),
    'subkage': ('Subkage', 4),
    'sannin': ('Sannin', 4),
    'kage': ('Kage', 5),
}

def day_end(now):
    d=datetime.fromtimestamp(now,BOGOTA)
    return int((d.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)).timestamp())

class SimpleFlow:
    def __init__(self,ms,eco,market):
        self.ms=ms;self.e=eco;self.m=market;self.db=ms.db
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS simple_profile_requests(
          id INTEGER PRIMARY KEY,guild INTEGER NOT NULL,user INTEGER NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending',data TEXT NOT NULL,registration TEXT NOT NULL,
          proof TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',reviewer INTEGER,
          created INTEGER NOT NULL,message INTEGER,channel INTEGER);
        CREATE UNIQUE INDEX IF NOT EXISTS simple_profile_pending ON simple_profile_requests(guild,user) WHERE state='pending';
        CREATE TABLE IF NOT EXISTS simple_profile_links(guild INTEGER,user INTEGER,registration TEXT NOT NULL,PRIMARY KEY(guild,user));
        CREATE TABLE IF NOT EXISTS simple_forum_posts(report INTEGER PRIMARY KEY,thread INTEGER,message INTEGER,revision INTEGER NOT NULL DEFAULT -1);
        CREATE TABLE IF NOT EXISTS simple_notifications(kind TEXT,ident INTEGER,revision INTEGER,user INTEGER,PRIMARY KEY(kind,ident,revision,user));
        ''')
        eco.simple_flow=self
        for k,(label,_) in NORMAL.items():ms.config['types'][k]={'label':label,'base_rank':'D'}
        ms.config['types']['tanda_misiones']={'label':'Tanda de misiones (5+ capturas)','base_rank':'C'}
        self.install()

    def limits(self,row):
        kind=row['data'].get('type','')
        if row['data'].get('batch_flow'):
            return [500, 10000000]
        if kind in NORMAL:
            n=NORMAL[kind][1];return [n,n]
        if kind.startswith('azul_'):return [500,500]
        return self.e.settings(row['guild']).get('mission_rewards',MISSION_REWARDS).get(row['data'].get('mission_rank'),[0,0])

    def daily_session(self,g,u):
        now=self.ms.now()
        with atomic(self.db):
            session=self.ms.current(g)
            # Cierra tanto sesiones antiguas como automáticas al cambio de día.
            if session and day_end(session['opened'])<=now:
                self.db.execute('UPDATE world_sessions SET closed=? WHERE id=?',(day_end(session['opened']),session['id']))
                session=None
            if not session:
                label='Actividad '+datetime.fromtimestamp(now,BOGOTA).strftime('%d/%m/%Y')
                cur=self.db.execute('INSERT INTO world_sessions(guild,label,leader,opened) VALUES(?,?,?,?)',(g,label,u,now))
                self.ms.log(g,None,u,'automatic_session',{'session':cur.lastrowid})
                session=self.ms.session(cur.lastrowid)
        return session

    def active(self,g,u):
        s=self.ms.current(g)
        if not s or day_end(s['opened'])<=self.ms.now():raise ValueError('Pulsa Comenzar misiones y espera la autorización del staff de hoy.')
        a=self.ms.authorization(s['id'],u)
        if not a or a['state']!='approved' or a['ended'] is not None:raise ValueError('Necesitas una autorización activa. Pulsa Comenzar misiones.')
        return a

    def install(self):
        old_validate=self.ms.validate_mission
        old_auth=self.ms.validate_auth
        def validate_auth(row):
            if row['data'].get('simple_flow'):
                if day_end(self.ms.session(row['session'])['opened'])<=self.ms.now():raise ValueError('Esta autorización es de otro día. El jugador debe comenzar una sesión nueva.')
            return old_auth(row)
        def validate(row):
            if row['data'].get('batch_flow'):
                members=row['data'].get('members',[row['owner']])
                refs=[]
                for user in members:
                    reg=self.ms.registered(row['guild'],user)
                    ep=self.e.player(row['guild'],user)
                    refs.append({'user':user,'authorization':0,'username':reg['data'].get('roblox_name',''),'rank':ep['rank'],'stats':'En captura','level':'En captura'})
                return row['data'].get('mission_rank','C'),refs
            if not row['data'].get('simple_flow'):return old_validate(row)
            # También los encargos normales usan autorización individual real.
            result=MissionStore.validate_mission(self.ms,row)
            if timestamp(row['data']['end'])>=day_end(self.ms.session(row['session'])['opened']):raise ValueError('La misión supera el cierre diario de la autorización.')
            if row['data'].get('type') in NORMAL and (row['data'].get('world')!='rpg' or row['data'].get('enemy')!='none'):
                raise ValueError('Los encargos normales no admiten rango enemigo.')
            return result
        self.ms.validate_auth=validate_auth;self.ms.validate_mission=validate
        original_decision=self.ms.decision
        self._decision=original_decision
        def decision(ident,revision,actor,action,reason,demo=False):
            row=self.ms.get(ident)
            if row and row['data'].get('simple_flow') and row['kind']=='mission' and action=='approve':
                raise ValueError('Usa Aprobar y pagar: el saldo y el contador deben actualizarse juntos.')
            return original_decision(ident,revision,actor,action,reason,demo)
        self.ms.decision=decision
        old_submit=self.ms.submit
        def submit(ident,revision,user):
            row=self.ms.get(ident)
            if row and row['kind']=='mission' and not row['data'].get('simple_flow'):
                raise ValueError('Continúa este borrador desde Panel → Mis reportes para usar autorización y pago integrado.')
            return old_submit(ident,revision,user)
        self.ms.submit=submit
        old_profile=self.m.profile
        def profile(g,u):
            p=old_profile(g,u)
            reg=self.ms.registered(g,u);link=self.db.execute('SELECT registration FROM simple_profile_links WHERE guild=? AND user=?',(g,u)).fetchone()
            if link and link[0]!=self.registration_key(reg):raise ValueError('Tu ficha cambió. Abre Ver mi perfil para verificar sus nuevos datos.')
            e=self.e.player(g,u)
            if not p.get('shinobi_rank'):
                p['shinobi_rank']=e['rank']
            if p.get('shinobi_rank') in SHINOBI_RANKS:
                p['rank_level']=max(e['tier'],SHINOBI_RANKS[p['shinobi_rank']][1])
            else:
                p['rank_level']=e['tier']
            p['village']=self.e.village(g,e['village'])['name']
            # Solo usa niveles y estadísticas de una autorización aprobada más reciente.
            previous=self.db.execute('SELECT verified_at FROM market_profiles WHERE guild=? AND user=?',(g,u)).fetchone()
            auth=self.db.execute("SELECT data FROM mission_reports WHERE guild=? AND owner=? AND kind='authorization' AND state='approved' AND granted>? ORDER BY granted DESC LIMIT 1",(g,u,previous[0])).fetchone()
            if auth:
                d=json.loads(auth[0]);p.update(dict(zip(('chi','nin','tai','hp'),stats_values(d['stats']))));p['level']=int(d['level'])
            return p
        self.m.profile=profile

    def approve_pay(self,g,actor,ident,revision,n):
        with atomic(self.db):
            r=self.ms.get(ident)
            if not r or r['guild']!=g or r['kind']!='mission' or r['revision']!=revision or r['state']!='pending':raise ValueError('El reporte cambió o ya fue resuelto.')
            if actor in {r['owner'],*r['data'].get('members',[])}:raise ValueError('Otro supervisor debe aprobar tu misión.')
            village=self.e.player(g,r['owner'])['village']
            approved=self._decision(ident,revision,actor,'approve','Evidencias verificadas por el supervisor; aprobación y pago conjunto.')
            count=max(1,int(r['data'].get('batch_count',1)))
            if count>1:
                rank=approved['data'].get('mission_rank','D')
                users=sorted(x['user'] for x in approved['participants']) or [r['owner']]
                for extra in range(count-1):
                    for u in users:
                        self.db.execute('INSERT OR IGNORE INTO mission_credits VALUES(?,?,?)',(-(ident*100+extra+1),u,rank))
            tx,recipients=self.e.pay_mission(g,actor,ident,village,n,'squad',approved['revision'])
            return tx,recipients

    def attest_all(self,ident,revision,actor):
        """Constancia humana explícita; no inventa una fecha extraída de la captura."""
        with atomic(self.db):
            r=self.ms.get(ident)
            if not r or r['state']!='pending' or r['revision']!=revision:raise ValueError('Reporte desactualizado.')
            if actor in {r['owner'],*r['data'].get('members',[])}:raise ValueError('No puedes verificar tu propio reporte.')
            p=self.ms.evidence_policy;proofs=p.validate_files(r)
            for j,proof in enumerate(proofs):
                self.db.execute('INSERT OR REPLACE INTO mission_evidence_checks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (ident,revision,actor,j,proof['digest'],0,'Verificada visualmente; sin transcripción','',dump(p.checklist(r)),'Supervisor confirma fecha/hora, identidad y condiciones de todas las imágenes.',self.ms.now()))
            self.ms.log(r['guild'],ident,actor,'visual_check_all',{'revision':revision,'checklist':p.checklist(r),'images':len(proofs)})

    @staticmethod
    def registration_key(reg):return digest({'id':reg['id'],'data':reg['data']})

    def seed_profile(self,g,u):
        reg=self.ms.registered(g,u);d=reg['data'];e=self.e.player(g,u)
        old=self.m.one('SELECT data FROM market_profiles WHERE guild=? AND user=?',(g,u))
        p=json.loads(old['data']) if old else {}
        # Valores de identidad aprobados, nunca añadidos por el comprador.
        mains=d.get('mains') or ([d['main']] if d.get('main') and d.get('main')!='none' else [])
        p.update(rank_level=e['tier'],shinobi_rank=p.get('shinobi_rank') or e.get('rank','genin'),village=self.e.village(g,e['village'])['name'],elements=d.get('elements',[]),bloodlines=list(dict.fromkeys(mains+d.get('secondary',[]))))
        p.setdefault('race','humano')
        p.setdefault('level',200)
        for k in ('chi','nin','tai','hp'):p.setdefault(k,900)
        p.setdefault('bloodline_levels',{})
        p.setdefault('tags',[])
        latest=self.db.execute("SELECT data FROM mission_reports WHERE guild=? AND owner=? AND kind='authorization' AND state='approved' ORDER BY granted DESC LIMIT 1",(g,u)).fetchone()
        if latest:
            a=json.loads(latest[0]);p['level']=int(a['level']);p.update(dict(zip(('chi','nin','tai','hp'),stats_values(a['stats']))))
        return p

    def propose_profile(self,g,u,data,proof):
        data=dict(data)
        if 'race' in data and data.get('race') not in ('humano','shark','snake','puppet'):raise ValueError('Selecciona humano, shark, snake o puppet.')
        data.setdefault('race','humano')
        if data.get('level') in (None,''):data['level']=200
        for k in ('chi','nin','tai','hp'):
            if data.get(k) in (None,''):data[k]=900
        for k in ('level','chi','nin','tai','hp'):
            if type(data.get(k)) is not int or not 0<=data[k]<=18000:raise ValueError('Nivel o estadísticas inválidos.')
        if not 1<=data['level']<=1000:raise ValueError('Nivel entre 1 y 1000.')
        with atomic(self.db):
            if self.db.execute("SELECT 1 FROM simple_profile_requests WHERE guild=? AND user=? AND state='pending'",(g,u)).fetchone():raise ValueError('Ya tienes una actualización pendiente. Consulta Ver mi perfil.')
            reg=self.ms.registered(g,u)
            fresh=self.seed_profile(g,u)
            for key in ('rank_level','village','elements','bloodlines'):data[key]=fresh[key]
            data.setdefault('shinobi_rank',fresh.get('shinobi_rank','genin'))
            data.setdefault('bloodline_levels',{})
            data.setdefault('tags',[])
            if set(data.get('bloodline_levels',{}))-set(data['bloodlines']):raise ValueError('Tus bloodlines cambiaron. Abre el perfil de nuevo.')
            cur=self.db.execute('INSERT INTO simple_profile_requests(guild,user,data,registration,proof,created) VALUES(?,?,?,?,?,?)',(g,u,dump(data),self.registration_key(reg),dump(proof),self.ms.now()))
            return cur.lastrowid

    def review_profile(self,g,actor,ident,approve,reason,shinobi_rank=None):
        r=self.m.one('SELECT * FROM simple_profile_requests WHERE id=? AND guild=?',(ident,g))
        if not r or r['state']!='pending':raise ValueError('Solicitud resuelta o inexistente.')
        if r['user']==actor:raise ValueError('Otro supervisor debe verificar tu perfil.')
        reg=self.ms.registered(g,r['user'])
        if approve and self.registration_key(reg)!=r['registration']:raise ValueError('La ficha cambió. Pide una nueva actualización.')
        # set_profile abre su propia transacción: se hace todo en una sola aquí.
        with atomic(self.db):
            if approve:
                p=json.loads(r['data']);e=self.e.player(g,r['user'])
                chosen_rank=shinobi_rank or p.get('shinobi_rank') or e.get('rank','genin')
                if chosen_rank not in SHINOBI_RANKS:chosen_rank=e.get('rank','genin')
                p['shinobi_rank']=chosen_rank
                p['rank_level']=max(e['tier'],SHINOBI_RANKS.get(chosen_rank,('Genin',1))[1])
                self.db.execute('INSERT INTO market_profiles VALUES(?,?,?,?,?) ON CONFLICT(guild,user) DO UPDATE SET data=excluded.data,verified_by=excluded.verified_by,verified_at=excluded.verified_at',(g,r['user'],dump(p),actor,self.m.now()))
                self.db.execute('INSERT INTO simple_profile_links VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET registration=excluded.registration',(g,r['user'],r['registration']))
                self.m.audit(g,actor,'profile_verified_form',{'user':r['user'],'profile':p,'shinobi_rank':chosen_rank})
            self.db.execute('UPDATE simple_profile_requests SET state=?,reviewer=?,reason=? WHERE id=?',('approved' if approve else 'rejected',actor,reason,ident))
