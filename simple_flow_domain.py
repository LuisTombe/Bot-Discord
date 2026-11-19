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

MISSION_PAY_RANGES = {
    'rpg_azul': ('RPG · Misión de estrella (Azul)', 'D', 500, 1000, 'Encargo civil de estrella azul en modo RPG.'),
    'rpg_roja': ('RPG · Misión de estrella (Roja)', 'D', 1001, 2500, 'Encargo de estrella roja en modo RPG.'),
    'rpg_verde': ('RPG · Misión de pergamino (Verde / Bandidos / NPCs)', 'D', 2501, 4999, 'Derrotar bandidos o NPCs de pergamino verde en modo RPG.'),
    'D': ('Open World · Rango D (Dificultad Baja)', 'D', 5000, 9999, 'Entregar pergamino, Robar pergamino, Entregar cargamento, Robar cargamento.'),
    'C': ('Open World · Rango C (Dificultad Moderada)', 'C', 10000, 24999, 'Proteger cargamento, Destruir cargamento, Proteger/escoltar persona, Ejecutar/asesinar persona.'),
    'B': ('Open World · Rango B (Dificultad Alta)', 'B', 25000, 49999, 'Capturar/secuestrar objetivo, o encargos D/C/B enfrentando enemigos Chunin, Medical Corps o Police Corps.'),
    'A': ('Open World · Rango A (Dificultad Muy Alta)', 'A', 50000, 99999, 'Completar cualquier objetivo con oposición de nivel Jounin, ANBU o Jounin Sensei.'),
    'S': ('Open World · Rango S (Dificultad Extrema)', 'S', 100000, 500000, 'Completar cualquier objetivo con oposición de nivel Subkage, Sannin, Kage o Akatsuki.'),
}

MISSION_TYPE_BASE_RANK = {
    'normal_azul': ('D', 'rpg_azul'),
    'normal_roja': ('D', 'rpg_roja'),
    'normal_verde': ('D', 'rpg_verde'),
    'recuperar_scroll': ('D', 'D'),
    'robar_scroll': ('D', 'D'),
    'entregar': ('D', 'D'),
    'robar_carga': ('D', 'D'),
    'proteger_carga': ('C', 'C'),
    'destruir_carga': ('C', 'C'),
    'proteger_persona': ('C', 'C'),
    'ejecutar': ('C', 'C'),
    'proteger_objetivo': ('C', 'C'),
    'capturar': ('B', 'B'),
    'tanda_misiones': ('C', 'C'),
}

ENEMY_ROLE_TO_MISSION_RANK = {
    'academia': 'D',
    'estudiante': 'D',
    'genin': 'D',
    'chunin': 'B',
    'chunnin': 'B',
    'medical': 'B',
    'police': 'B',
    'jounin': 'A',
    'anbu': 'A',
    'sensei': 'A',
    'jounin-sensei': 'A',
    'subkage': 'S',
    'sub-kage': 'S',
    'sannin': 'S',
    'kage': 'S',
    'kages': 'S',
    'akatsuki': 'S',
}

RANK_ORDER = ('D', 'C', 'B', 'A', 'S')

def max_mission_rank(*ranks):
    valid = [r for r in ranks if r in RANK_ORDER]
    return max(valid, key=RANK_ORDER.index) if valid else 'D'

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
            try:
                reg=self.ms.registered(g,u);link=self.db.execute('SELECT registration FROM simple_profile_links WHERE guild=? AND user=?',(g,u)).fetchone()
                if link and link[0] and not str(link[0]).startswith('no-reg:') and link[0]!=self.registration_key(reg):
                    raise ValueError('Tu ficha cambió. Abre Mi perfil para verificar sus nuevos datos.')
            except ValueError:
                pass
            try:
                e=self.e.player(g,u)
            except ValueError:
                e={'rank':'genin','tier':1,'village':0}
            if not p.get('shinobi_rank'):
                p['shinobi_rank']=e['rank']
            if p.get('shinobi_rank') in SHINOBI_RANKS:
                p['rank_level']=max(e['tier'],SHINOBI_RANKS[p['shinobi_rank']][1])
            else:
                p['rank_level']=e['tier']
            p['village']=self.e.village(g,e['village'])['name'] if e.get('village') else p.get('village','Por verificar')
            # Solo usa niveles y estadísticas de una autorización aprobada más reciente.
            previous=self.db.execute('SELECT verified_at FROM market_profiles WHERE guild=? AND user=?',(g,u)).fetchone()
            if previous:
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
        try:reg=self.ms.registered(g,u);d=reg['data']
        except ValueError:d={}
        try:e=self.e.player(g,u)
        except ValueError:e={'tier':1,'rank':'genin','village':0}
        old=self.m.one('SELECT data FROM market_profiles WHERE guild=? AND user=?',(g,u))
        p=json.loads(old['data']) if old else {}
        # Valores de identidad aprobados, nunca añadidos por el comprador.
        mains=d.get('mains') or ([d['main']] if d.get('main') and d.get('main')!='none' else [])
        vname=self.e.village(g,e['village'])['name'] if e.get('village') else 'Por verificar'
        p.update(rank_level=e['tier'],shinobi_rank=p.get('shinobi_rank') or e.get('rank','genin'),village=vname,elements=d.get('elements',[]),bloodlines=list(dict.fromkeys(mains+d.get('secondary',[]))))
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
        data.setdefault('race','humano')
        if data.get('level') in (None,''):data['level']=200
        for k in ('chi','nin','tai','hp'):
            if data.get(k) in (None,''):data[k]=900
        with atomic(self.db):
            if self.db.execute("SELECT 1 FROM simple_profile_requests WHERE guild=? AND user=? AND state='pending'",(g,u)).fetchone():raise ValueError('Ya tienes una actualización pendiente. Consulta Ver mi perfil.')
            try:reg=self.ms.registered(g,u);reg_key=self.registration_key(reg)
            except ValueError:reg_key='no-reg:'+str(u)
            fresh=self.seed_profile(g,u)
            for key in ('rank_level','village','elements','bloodlines'):data[key]=fresh[key]
            data.setdefault('shinobi_rank',fresh.get('shinobi_rank','genin'))
            data.setdefault('bloodline_levels',{})
            data.setdefault('tags',[])
            cur=self.db.execute('INSERT INTO simple_profile_requests(guild,user,data,registration,proof,created) VALUES(?,?,?,?,?,?)',(g,u,dump(data),reg_key,dump(proof),self.ms.now()))
            return cur.lastrowid

    def detect_member_shinobi_rank(self,g,member_or_roles):
        """Lee los roles de Discord del jugador/enemigo (por ID de eco_ranks o por nombre de rol) y devuelve su rango shinobi."""
        role_ids=set()
        role_names=[]
        if hasattr(member_or_roles,'roles'):
            for r in member_or_roles.roles:
                role_ids.add(r.id)
                role_names.append(r.name.lower().strip())
        elif isinstance(member_or_roles,(list,set,tuple)):
            for x in member_or_roles:
                if isinstance(x,int):role_ids.add(x)
                elif hasattr(x,'id'):role_ids.add(x.id);role_names.append(getattr(x,'name','').lower())
                else:role_names.append(str(x).lower())
        rank_rows=self.e.rows('SELECT key,role,monthly,tier FROM eco_ranks WHERE guild=?',(g,))
        matched=[r for r in rank_rows if r['role'] in role_ids]
        if matched:
            best=max(matched,key=lambda x:(x['tier'],x['monthly']))
            return best['key']
        order=['akatsuki','kage','sannin','subkage','sub-kage','sensei','jounin-sensei','anbu','jounin','police','medical','chunin','chunnin','genin','estudiante','academia']
        for candidate in order:
            for rname in role_names:
                if candidate in rname:
                    return candidate.replace('chunnin','chunin').replace('sub-kage','subkage').replace('jounin-sensei','sensei').replace('estudiante','academia')
        return 'genin'

    def calculate_difficulty(self,g,base_key_or_rank,enemy_members=()):
        """Calcula la dificultad final (Rango D, C, B, A o S) y el intervalo de pago [min, max] por misión."""
        if base_key_or_rank in MISSION_PAY_RANGES:
            label,base_letter,lo,hi,desc=MISSION_PAY_RANGES[base_key_or_rank]
            pay_key=base_key_or_rank
        elif base_key_or_rank in MISSION_TYPE_BASE_RANK:
            base_letter,pay_key=MISSION_TYPE_BASE_RANK[base_key_or_rank]
            label,_,lo,hi,desc=MISSION_PAY_RANGES[pay_key]
        else:
            base_letter='D';pay_key='D'
            label,_,lo,hi,desc=MISSION_PAY_RANGES['D']
        enemy_ranks=[]
        enemy_letters=[]
        for em in (enemy_members or ()):
            srank=self.detect_member_shinobi_rank(g,em)
            enemy_ranks.append((getattr(em,'id',0),srank))
            e_letter=ENEMY_ROLE_TO_MISSION_RANK.get(srank,'D')
            enemy_letters.append(e_letter)
        final_letter=max_mission_rank(base_letter,*enemy_letters)
        # Si los enemigos subieron el rango por encima del rango base, usar el intervalo de pago del nuevo rango Open World
        if final_letter!=base_letter or (pay_key.startswith('rpg_') and enemy_letters and max_mission_rank(*enemy_letters)!='D'):
            pay_key=final_letter
            label,_,lo,hi,desc=MISSION_PAY_RANGES[pay_key]
        return {
            'pay_key':pay_key,
            'label':label,
            'base_rank':base_letter,
            'final_rank':final_letter,
            'min_pay':lo,
            'max_pay':hi,
            'description':desc,
            'enemies':enemy_ranks,
        }

    def pay_missions_command(self,g,actor,players,base_key,amount_per_mission,count=1,enemies=(),reason='Pago de misiones verificado por Staff',report_id=None):
        """Paga misiones desde comando: cada integrante recibe completo (paga * numero_misiones) y suma +count misiones del rango."""
        if not players:raise ValueError('Debes indicar al menos un integrante para recibir el pago.')
        if not 1<=count<=100:raise ValueError('El número de misiones debe estar entre 1 y 100.')
        player_ids=[]
        for p in players:
            uid=p.id if hasattr(p,'id') else int(p)
            if uid not in player_ids:player_ids.append(uid)
        if len(player_ids)>4:raise ValueError('El máximo de integrantes en squad en Shindo Life es 4.')
        calc=self.calculate_difficulty(g,base_key,enemies)
        final_rank=calc['final_rank']
        lo_unit,hi_unit=calc['min_pay'],calc['max_pay']
        if not lo_unit<=amount_per_mission<=hi_unit:
            raise ValueError(f'Para **{calc["label"]}** (Rango **{final_rank}**), la paga por misión debe estar entre **{lo_unit:,} y {hi_unit:,} Ryo**. Ingresaste: {amount_per_mission:,} Ryo.')
        per_player_total=amount_per_mission*count
        total_pay=per_player_total*len(player_ids)
        with atomic(self.db):
            self.e.live(g)
            first_profile=self.e.profile(g,player_ids[0])
            village=first_profile['village'] if first_profile and first_profile.get('village') else 0
            # Pago completo (no dividido) para cada integrante de la squad
            recipients={str(uid):per_player_total for uid in player_ids}
            session=self.daily_session(g,player_ids[0])
            now=self.ms.now()
            if report_id:
                r=self.ms.get(report_id)
                if not r or r['guild']!=g:raise ValueError('Reporte inexistente.')
                rid=report_id
                self.db.execute("UPDATE mission_reports SET state='approved',revision=revision+1,reviewer=?,reason=? WHERE id=?",(actor,reason[:300],rid))
            else:
                d={
                    'simple_flow':True,'batch_flow':True,'batch_count':count,
                    'world':'rpg' if calc['pay_key'].startswith('rpg_') else 'open_world',
                    'type':base_key,'mission_rank':final_rank,'members':player_ids,
                    'enemies':[{'user':uid,'rank':sr} for uid,sr in calc['enemies']],
                    'notes':reason,'amount':per_player_total,'total_paid':total_pay,
                }
                cur=self.db.execute(
                    "INSERT INTO mission_reports(guild,session,owner,kind,state,revision,data,created,submitted,granted,reason,reviewer) VALUES(?,?,?,'mission','approved',1,?,?,?,?,?,?)",
                    (g,session['id'],player_ids[0],dump(d),now,now,now,reason[:300],actor)
                )
                rid=cur.lastrowid
            self.db.execute('DELETE FROM mission_participants WHERE report=?',(rid,))
            for uid in player_ids:
                self.db.execute('INSERT OR IGNORE INTO mission_participants VALUES(?,?,0)',(rid,uid))
            # Acreditar exactamente +count misiones del rango final_rank a cada integrante
            for uid in player_ids:
                self.db.execute('INSERT OR REPLACE INTO mission_credits VALUES(?,?,?)',(rid,uid,final_rank))
            for extra in range(count-1):
                for uid in player_ids:
                    self.db.execute('INSERT OR REPLACE INTO mission_credits VALUES(?,?,?)',(-(rid*100+extra+1),uid,final_rank))
            self.ms.log(g,rid,actor,'approve',{'count':count,'rank':final_rank,'per_player':per_player_total,'total_pay':total_pay,'recipients':recipients,'enemies':calc['enemies']})
            ref=f'mission:cmd:{rid}:{now}'
            v_key=f'v:{village}'
            source_key=v_key if (village and self.e.balance(g,v_key)>=total_pay) else 'system'
            tx=self.e._move(g,ref,actor,f'Pago {count}x Misión Rango {final_rank} (#{rid})',[(source_key,-total_pay)]+[(f'u:{u}',m) for u,m in recipients.items()])
            if village:
                self.db.execute('INSERT OR REPLACE INTO eco_mission_payments VALUES(?,?,?,?,?,?,0)',(rid,g,village,tx,1,dump(recipients)))
            return {'report_id':rid,'tx':tx,'recipients':recipients,'per_player':per_player_total,'final_rank':final_rank,'count':count,'total_pay':total_pay,'calc':calc}

    def review_profile(self,g,actor,ident,approve,reason='Revisado por Staff',shinobi_rank=None,allow_self=False):
        r=self.m.one('SELECT * FROM simple_profile_requests WHERE id=? AND guild=?',(ident,g))
        if not r or r['state']!='pending':raise ValueError('Solicitud resuelta o inexistente.')
        if r['user']==actor and not allow_self:raise ValueError('Otro supervisor debe verificar tu perfil.')
        # set_profile abre su propia transacción: se hace todo en una sola aquí.
        with atomic(self.db):
            if approve:
                p=json.loads(r['data'])
                ep=self.e.profile(g,r['user']) or {'rank':'genin','tier':1}
                chosen_rank=shinobi_rank or p.get('shinobi_rank') or ep.get('rank','genin')
                if chosen_rank not in SHINOBI_RANKS:chosen_rank=ep.get('rank','genin')
                p['shinobi_rank']=chosen_rank
                p['rank_level']=max(ep.get('tier',1),SHINOBI_RANKS.get(chosen_rank,('Genin',1))[1])
                self.db.execute('INSERT INTO market_profiles VALUES(?,?,?,?,?) ON CONFLICT(guild,user) DO UPDATE SET data=excluded.data,verified_by=excluded.verified_by,verified_at=excluded.verified_at',(g,r['user'],dump(p),actor,self.m.now()))
                self.db.execute('INSERT INTO simple_profile_links VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET registration=excluded.registration',(g,r['user'],r['registration']))
                self.m.audit(g,actor,'profile_verified_form',{'user':r['user'],'profile':p,'shinobi_rank':chosen_rank})
            self.db.execute('UPDATE simple_profile_requests SET state=?,reviewer=?,reason=? WHERE id=?',('approved' if approve else 'rejected',actor,reason,ident))
