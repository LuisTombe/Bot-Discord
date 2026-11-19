"""World X v7 policies. All financial changes share the existing atomic ledger."""
from i18n import t
import calendar
import json
import secrets
from collections import Counter
from datetime import datetime, timedelta
from fractions import Fraction
from economy_domain import TZ, amount, dump, period

BONUS={'academia':0,'genin':5,'chunin':10,'medical':20,'police':15,'jounin':40,'anbu':30,'sensei':50,'subkage':30,'sannin':30,'kage':10,'akatsuki':0}
REWARDS={'D':[5000,9999],'C':[10000,24999],'B':[25000,49999],'A':[50000,99999],'S':[100000,500000]}

def week(ts):
    d=datetime.fromtimestamp(ts,TZ).replace(hour=0,minute=0,second=0,microsecond=0)
    start=d-timedelta(days=d.weekday())
    return int(start.timestamp()),int((start+timedelta(days=7)).timestamp())

def split_reward(n,roster):
    weights={str(p['user']):100+BONUS.get(p['rank'],0) for p in roster}
    if not weights:raise ValueError(t('No hay participantes.'))
    total=sum(weights.values());result={u:n*w//total for u,w in weights.items()}
    order=sorted(weights,key=lambda u:(-(n*weights[u]%total),int(u)))
    for u in order[:n-sum(result.values())]:result[u]+=1
    return result

class Policies:
    def __init__(self,eco):
        self.e=eco;self.db=eco.db;self.reg=eco.reg;self.ms=eco.missions
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS wx_policy(guild INTEGER PRIMARY KEY,effective INTEGER NOT NULL,authority INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS wx_loans(id INTEGER PRIMARY KEY,guild INTEGER NOT NULL,lender TEXT NOT NULL,borrower TEXT NOT NULL,principal INTEGER NOT NULL,interest INTEGER NOT NULL,due INTEGER NOT NULL,terms TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'offered',accepted INTEGER,paid INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS wx_grants(guild INTEGER,village INTEGER,month TEXT,PRIMARY KEY(guild,village,month));
        CREATE TABLE IF NOT EXISTS wx_village_start(guild INTEGER,village INTEGER,starts INTEGER,PRIMARY KEY(guild,village));
        CREATE TABLE IF NOT EXISTS wx_exempt(guild INTEGER,user INTEGER,week INTEGER,reason TEXT,actor INTEGER,PRIMARY KEY(guild,user,week));
        CREATE TABLE IF NOT EXISTS wx_lives(id INTEGER PRIMARY KEY,guild INTEGER,user INTEGER,ended INTEGER,archive TEXT,legacy TEXT,claimed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS wx_events(id INTEGER PRIMARY KEY,guild INTEGER,village INTEGER,name TEXT,budget INTEGER,spent INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'open',details TEXT);
        CREATE TABLE IF NOT EXISTS wx_event_payments(event INTEGER,reference TEXT,tx INTEGER,PRIMARY KEY(event,reference));
        ''')
    def ensure(self,g):
        if not self.db.execute('SELECT 1 FROM wx_policy WHERE guild=?',(g,)).fetchone():
            with self.db:
                self.db.execute('INSERT INTO wx_policy(guild,effective) VALUES(?,?)',(g,self.e.now()))
                d=self.e.settings(g);d['mission_rewards']=REWARDS
                self.db.execute('INSERT INTO eco_settings VALUES(?,?) ON CONFLICT(guild) DO UPDATE SET data=excluded.data',(g,dump(d)))
        return dict(self.db.execute('SELECT * FROM wx_policy WHERE guild=?',(g,)).fetchone())
    def count(self,g,u,start,end,pending=False):
        states="('pending')" if pending else "('approved')"
        rows=self.db.execute(f"SELECT r.data FROM mission_reports r JOIN mission_participants p ON p.report=r.id WHERE r.guild=? AND p.user=? AND r.kind='mission' AND r.state IN {states}",(g,u))
        from missions_domain import timestamp
        counts=Counter()
        for r in rows:
            d=json.loads(r[0])
            try:t=timestamp(d['end'])
            except (KeyError,ValueError):continue
            if start<=t<end:counts[d.get('mission_rank','D')]+=max(1,int(d.get('batch_count',1)))
        return dict(counts)
    def activity(self,g,u,month):
        d=datetime.strptime(month,'%Y-%m').replace(tzinfo=TZ);end=(d.replace(day=28)+timedelta(days=4)).replace(day=1)
        days=[];weeks=[];cur=d
        while cur<end:
            counts=self.count(g,u,int(cur.timestamp()),int((cur+timedelta(days=1)).timestamp()))
            days.append({'fecha':cur.strftime('%Y-%m-%d'),**{r:counts.get(r,0) for r in 'DCBAS'},'total':sum(counts.values())});cur+=timedelta(days=1)
        w,_=week(int(d.timestamp()))
        while w<int(end.timestamp()):
            counts=self.count(g,u,w,w+604800);pending=sum(self.count(g,u,w,w+604800,True).values())
            weeks.append({'inicio':datetime.fromtimestamp(w,TZ).strftime('%Y-%m-%d'),'total':sum(counts.values()),'pendientes':pending,'cumple':sum(counts.values())>=10,'exento':bool(self.db.execute('SELECT 1 FROM wx_exempt WHERE guild=? AND user=? AND week=?',(g,u,w)).fetchone())});w+=604800
        return {'diario':days,'semanal':weeks,'mensual':{r:sum(x[r] for x in days) for r in 'DCBAS'}}
    def payroll(self,g,now=None):
        now=self.e.now() if now is None else now;effective=self.ensure(g)['effective']
        first=self.db.execute('SELECT MIN(start) FROM eco_service WHERE guild=?',(g,)).fetchone()[0]
        if first is None:return
        start,end=period(first)
        while end<=now:
            # Wait for the final week's closure, except old periods before activation.
            if end>effective and week(end-1)[1]>now:break
            if self.db.execute('SELECT 1 FROM eco_periods WHERE guild=? AND start=?',(g,start)).fetchone():start,end=period(end);continue
            services=self.e.rows('SELECT * FROM eco_service WHERE guild=? AND start<? AND (end IS NULL OR end>?)',(g,end,start))
            totals={};waiting=False
            for s in services:
                death=self.db.execute('SELECT MAX(ended) FROM wx_lives WHERE guild=? AND user=?',(g,s['user'])).fetchone()[0]
                if death and s['end'] is not None and s['end']<=death:continue
                a=max(start,s['start']);b=min(end,s['end'] or end);cursor=a;earned=Fraction(0)
                first_user=self.db.execute('SELECT MIN(start) FROM eco_service WHERE guild=? AND user=? AND start>=?',(g,s['user'],death or 0)).fetchone()[0]
                while cursor<b:
                    ws,we=week(cursor);stop=min(we,b)
                    exempt=cursor<effective or first_user>ws or bool(self.db.execute('SELECT 1 FROM wx_exempt WHERE guild=? AND user=? AND week=?',(g,s['user'],ws)).fetchone())
                    count=sum(self.count(g,s['user'],max(ws,death or 0),we).values())
                    if not exempt and count<10 and sum(self.count(g,s['user'],max(ws,death or 0),we,True).values()):waiting=True
                    if exempt or count>=10:earned+=Fraction(s['monthly']*(stop-cursor),2*(end-start))
                    cursor=stop
                key=(s['village'],s['user']);totals[key]=totals.get(key,Fraction(0))+earned
            if waiting:break
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                for (v,u),n in totals.items():
                    if int(n):self.db.execute('INSERT OR IGNORE INTO eco_payroll VALUES(?,?,?,?,?,?,0)',(g,start,end,v,u,int(n)))
                self.db.execute('INSERT OR IGNORE INTO eco_periods VALUES(?,?,?)',(g,start,end))
                self.e.audit(g,0,'activity_payroll',{'start':start,'end':end,'claims':{str(k):int(v) for k,v in totals.items()}})
            start,end=period(end)
    def monthly(self,g):
        self.ensure(g);now=self.e.now();d=datetime.fromtimestamp(now,TZ);current=int(d.replace(day=1,hour=0,minute=0,second=0,microsecond=0).timestamp())
        for v in self.e.villages(g):
            row=self.db.execute('SELECT starts FROM wx_village_start WHERE guild=? AND village=?',(g,v['id'])).fetchone()
            if not row:
                # First grant next month. Initial capital is separate; no retroactive windfall.
                nxt=(d.replace(day=28)+timedelta(days=4)).replace(day=1,hour=0,minute=0,second=0,microsecond=0)
                with self.db:self.db.execute('INSERT INTO wx_village_start VALUES(?,?,?)',(g,v['id'],int(nxt.timestamp())))
                continue
            cursor=row[0]
            while cursor<=current:
                date=datetime.fromtimestamp(cursor,TZ);key=date.strftime('%Y-%m')
                with self.db:
                    self.db.execute('BEGIN IMMEDIATE')
                    if not self.db.execute('SELECT 1 FROM wx_grants WHERE guild=? AND village=? AND month=?',(g,v['id'],key)).fetchone():
                        self.e._move(g,f'grant:{v["id"]}:{key}',0,t('Financiación mensual'), [('system',-10000000),(f'v:{v["id"]}',10000000)])
                        self.db.execute('INSERT INTO wx_grants VALUES(?,?,?)',(g,v['id'],key))
                cursor=int(((date.replace(day=28)+timedelta(days=4)).replace(day=1)).timestamp())
    def collect(self,g,entries,ref):
        # Called inside the original transaction. Collections themselves do not recurse.
        gains=Counter()
        for key,n in entries:
            if key.startswith('v:'):gains[key]+=n
        for key,n in gains.items():
            available=min(max(0,n)//2,self.e.balance(g,key))
            if not available:continue
            for loan in self.e.rows("SELECT * FROM wx_loans WHERE guild=? AND borrower=? AND state='active' AND due<=? ORDER BY due,id",(g,key,self.e.now())):
                debt=loan['principal']+loan['interest']-loan['paid'];pay=min(available,debt)
                if not pay:continue
                self.e._raw_move(g,f'collection:{ref}:{loan["id"]}',0,t('Cobro de deuda vencida'),[(key,-pay),(loan['lender'],pay)])
                self.db.execute("UPDATE wx_loans SET paid=paid+?,state=? WHERE id=?",(pay,'paid' if pay==debt else 'active',loan['id']));available-=pay
    def loan(self,g,ident):
        r=self.db.execute('SELECT * FROM wx_loans WHERE guild=? AND id=?',(g,ident)).fetchone()
        if not r:raise ValueError(t('Contrato inexistente.'))
        return dict(r)
    def offer(self,g,lender,borrower,n,rate,due,terms):
        self.e.live(g);amount(n)
        if lender==borrower or lender.split(':')[0]!=borrower.split(':')[0]:raise ValueError(t('Las partes deben ser diferentes y del mismo tipo.'))
        from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
        try:pct=Decimal(str(rate).replace(',','.'))
        except InvalidOperation:raise ValueError(t('Interés inválido.'))
        if not pct.is_finite() or not 0<=pct<=10000:raise ValueError(t('Interés único entre 0 y 10000 %.'))
        interest=int((Decimal(n)*pct/100).quantize(Decimal(1),rounding=ROUND_HALF_UP))
        amount(n+interest)
        if due<=self.e.now() or not 5<=len(terms.strip())<=1500:raise ValueError(t('Vencimiento futuro y condiciones de 5 a 1500 caracteres.'))
        if lender.startswith('u:'):
            if n>100000:raise ValueError(t('Máximo personal: 100.000 Ryo por semana.'))
            self.e.player(g,int(lender[2:]));self.e.player(g,int(borrower[2:]))
        else:self.e.village(g,int(lender[2:]));self.e.village(g,int(borrower[2:]))
        with self.db:
            cur=self.db.execute('INSERT INTO wx_loans(guild,lender,borrower,principal,interest,due,terms,created) VALUES(?,?,?,?,?,?,?,?)',(g,lender,borrower,n,interest,due,terms,self.e.now()))
            self.e.audit(g,0,'loan_offer',{'id':cur.lastrowid,'lender':lender,'borrower':borrower})
        return cur.lastrowid
    def accept(self,g,a,ident):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');r=self.loan(g,ident)
            if r['state']!='offered' or r['due']<=self.e.now():raise ValueError(t('Oferta cerrada o vencida.'))
            if r['lender'].startswith('u:'):
                for field in ('lender','borrower'):
                    self.e.player(g,int(r[field][2:]));ws,we=week(self.e.now())
                    total=self.db.execute(f'SELECT COALESCE(SUM(principal),0) FROM wx_loans WHERE guild=? AND {field}=? AND accepted>=? AND accepted<?',(g,r[field],ws,we)).fetchone()[0]
                    if total+r['principal']>100000:raise ValueError(t('Una parte supera su límite semanal de 100.000 Ryo.'))
            self.e._move(g,f'loan:{ident}:accept',a,t('Préstamo aceptado'),[(r['lender'],-r['principal']),(r['borrower'],r['principal'])])
            self.db.execute("UPDATE wx_loans SET state='active',accepted=? WHERE id=?",(self.e.now(),ident))
    def repay(self,g,a,ident,n):
        amount(n)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');r=self.loan(g,ident);remaining=r['principal']+r['interest']-r['paid']
            if r['state']!='active' or n>remaining:raise ValueError(t('Importe superior al saldo o contrato cerrado.'))
            self.e._move(g,f'loan:{ident}:repay:{r["paid"]}',a,t('Devolución voluntaria'),[(r['borrower'],-n),(r['lender'],n)])
            self.db.execute('UPDATE wx_loans SET paid=paid+?,state=? WHERE id=?',(n,'paid' if n==remaining else 'active',ident))
    def war(self,g,a,loser,winner,pct,reason,expected,ref):
        if not 5<=pct<=100 or loser==winner or len(reason.strip())<10:raise ValueError(t('Porcentaje 5–100, aldeas distintas y acuerdo/motivo obligatorio.'))
        self.e.village(g,loser);self.e.village(g,winner)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');balance=self.e.balance(g,f'v:{loser}')
            if balance!=expected:raise ValueError(t('El tesoro cambió. Abre otra vista previa.'))
            n=balance*pct//100;amount(n)
            tx=self.e._move(g,ref,a,t('Guerra: ')+reason,[(f'v:{loser}',-n),(f'v:{winner}',n)])
            self.e.audit(g,a,'war',{'loser':loser,'winner':winner,'percent':pct,'basis':balance,'amount':n,'reason':reason})
            return tx,n
    def death(self,g,u,a,reason,revision,cleanup=None):
        if len(reason.strip())<5:raise ValueError(t('Motivo obligatorio.'))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');r=self.reg.find(g,u)
            if not r or r['state']!='approved' or r['revision']!=revision:raise ValueError(t('Ficha modificada o no aprobada.'))
            if self.db.execute('SELECT 1 FROM eco_payroll WHERE guild=? AND user=? AND paid=0',(g,u)).fetchone():raise ValueError(t('Resuelve primero la nómina ya liquidada y pendiente.'))
            if self.db.execute('SELECT 1 FROM wx_lives WHERE guild=? AND user=? AND claimed=0',(g,u)).fetchone():raise ValueError(t('Hay un legado pendiente.'))
            if self.db.execute("SELECT 1 FROM mission_reports r JOIN mission_participants p ON p.report=r.id LEFT JOIN eco_mission_payments pay ON pay.report=r.id WHERE r.guild=? AND p.user=? AND r.state='approved' AND pay.report IS NULL",(g,u)).fetchone():raise ValueError(t('Resuelve primero los pagos de misiones aprobadas del personaje.'))
            lots=self.e.rows('SELECT * FROM eco_inventory WHERE guild=? AND user=? AND quantity>0',(g,u));categories={'arma':[],'mode':[],'tecnica':[]}
            for lot in lots:
                d=json.loads(lot['data']);kind='arma' if d['kind']=='arma' else d.get('legacy_type') if d['kind']=='permiso' else None
                if d['kind']=='permiso' and kind not in ('mode','tecnica','ninguno'):raise ValueError(t('Clasifica los permisos existentes con /legado clasificar antes del sorteo.'))
                if kind in categories:categories[kind].append(lot)
            chosen=[secrets.choice(items) for items in categories.values() if items];money=self.e.balance(g,f'u:{u}');keep=money//4
            archive={'registration':r,'inventory':lots,'balance':money,'reason':reason,'actor':a}
            legacy={'money':keep,'items':[{'item':x['item'],'data':json.loads(x['data'])} for x in chosen]}
            cur=self.db.execute('INSERT INTO wx_lives(guild,user,ended,archive,legacy) VALUES(?,?,?,?,?)',(g,u,self.e.now(),dump(archive),dump(legacy)));life=cur.lastrowid
            self.e._move(g,f'death:{life}',a,reason,[(f'u:{u}',-money),(f'legacy:{life}',keep),('system',money-keep)])
            self.db.execute('UPDATE eco_inventory SET quantity=0 WHERE guild=? AND user=?',(g,u))
            self.db.execute("UPDATE eco_deals SET state='cancelled' WHERE guild=? AND (seller=? OR buyer=?) AND state IN ('open','accepted')",(g,u,u))
            self.db.execute("UPDATE wx_loans SET state='cancelled' WHERE guild=? AND (lender=? OR borrower=?) AND state='offered'",(g,f'u:{u}',f'u:{u}'))
            self.db.execute('UPDATE eco_service SET end=? WHERE guild=? AND user=? AND end IS NULL',(self.e.now(),g,u))
            self.db.execute('UPDATE eco_profiles SET active=0 WHERE guild=? AND user=?',(g,u))
            self.db.execute('UPDATE mission_reports SET ended=? WHERE guild=? AND owner=? AND kind=\'authorization\' AND ended IS NULL',(self.e.now(),g,u))
            self.db.execute("UPDATE mission_reports SET state='rejected',reason='Personaje fallecido',revision=revision+1 WHERE guild=? AND owner=? AND state IN ('draft','pending','correction')",(g,u))
            # Archive completed missions; new character receives separate counters.
            messages=[]
            for field,channel in [('review_message','review_channel'),('public_message','public_channel')]:
                if r.get(field) and r.get(channel):messages.append([r[channel],r[field]])
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='market_requests'").fetchone():
                for mreq in self.e.rows("SELECT id FROM market_requests WHERE guild=? AND (actor=? OR target=?) AND state IN ('offered','pending')",(g,u,u)):
                    self.db.execute('DELETE FROM market_holds WHERE request=?',(mreq['id'],))
                    self.db.execute('UPDATE market_inventory SET locked_by=NULL WHERE locked_by=?',(mreq['id'],))
                    self.db.execute("UPDATE market_requests SET state='cancelled',reason='Personaje fallecido',dirty=1 WHERE id=?",(mreq['id'],))
                self.db.execute("UPDATE market_inventory SET state='revoked' WHERE guild=? AND user=? AND state='owned'",(g,u))
                self.db.execute('DELETE FROM market_profiles WHERE guild=? AND user=?',(g,u))
            self.db.execute('INSERT INTO cleanup_jobs(registration,payload) VALUES(?,?)',(r['id'],dump(cleanup or {'messages':messages,'roles':[]})))
            self.db.execute("UPDATE registrations SET state='deleted',data='{}',roblox=NULL,revision=revision+1,reason=?,review_message=NULL,public_message=NULL WHERE id=?",(reason,r['id']))
            self.db.execute('DELETE FROM screenshots WHERE registration=?',(r['id'],))
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='assignments'").fetchone():self.db.execute('DELETE FROM assignments WHERE guild=? AND user=?',(g,u))
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='birth_v8_claims'").fetchone():self.db.execute('DELETE FROM birth_v8_claims WHERE guild=? AND user=?',(g,u))
            self.e.audit(g,a,'permadeath',{'life':life,'user':u,'legacy':legacy,'reason':reason})
            return life,legacy
    def claim(self,g,u):
        r=self.reg.find(g,u)
        if not r or r['state']!='approved':raise ValueError(t('Primero aprueba la ficha del nuevo personaje.'))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');row=self.db.execute('SELECT * FROM wx_lives WHERE guild=? AND user=? AND claimed=0 ORDER BY id DESC LIMIT 1',(g,u)).fetchone()
            if not row:raise ValueError(t('No tienes un legado pendiente.'))
            legacy=json.loads(row['legacy']);self.e._move(g,f'legacy:{row["id"]}:claim',u,t('Legado del personaje anterior'),[(f'legacy:{row["id"]}',-legacy['money']),(f'u:{u}',legacy['money'])])
            for item in legacy['items']:self.e._grant(g,u,item['item'],item['data'],1)
            self.db.execute('UPDATE wx_lives SET claimed=1 WHERE id=?',(row['id'],))
            return legacy

def install_core(eco):
    p=Policies(eco);eco.policy=p;eco._raw_move=eco._move
    def move(g,ref,a,reason,entries):
        entries=list(entries);tx=eco._raw_move(g,ref,a,reason,entries);p.collect(g,entries,ref);return tx
    eco._move=move;eco.prepare_payroll=p.payroll
    return p
