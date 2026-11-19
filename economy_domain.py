from simple_transactions import atomic
"""World X economy: integer Ryo, atomic ledger, immutable inventory lots and payroll.
All methods are synchronous; never await inside a SQLite transaction.
"""
from i18n import format_fragment, t
import calendar
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from fractions import Fraction

TZ = timezone(timedelta(hours=-5))
RANKS = {'genin': ('Genin',150000,1), 'chunin': ('Chunnin',240000,2),
         'police': ('Police Corp',300000,2), 'medical': ('Medical Corp',300000,2),
         'jounin': ('Jounin',375000,3), 'anbu': ('Anbu',450000,3),
         'sensei': ('Jounin Sensei',600000,3), 'sannin': ('Subkage / Sannin',1050000,4),
         'kage': ('Kage',1500000,5)}
TIERS = {'D':1,'C':2,'B':2,'A':3,'S':3}
MISSION_REWARDS = {'D':[5000,7000],'C':[10000,12500],'B':[25000,30000],'A':[50000,75000],'S':[100000,500000]}
PHYSICAL = {'arma','lanzable','consumible'}
KINDS = PHYSICAL | {'permiso'}

def dump(value): return json.dumps(value,ensure_ascii=False,sort_keys=True)
def amount(n, zero=False):
    if type(n) is not int or not (0 if zero else 1)<=n<=10**12: raise ValueError(t('Importe entero entre 1 y 1.000.000.000.000 Ryo (0 cuando se permita).'))
    return n

def period(ts):
    d=datetime.fromtimestamp(ts,TZ)
    start=d.replace(day=1 if d.day<=15 else 16,hour=0,minute=0,second=0,microsecond=0)
    if d.day<=15: end=start.replace(day=16)
    elif d.month==12: end=start.replace(year=d.year+1,month=1,day=1)
    else: end=start.replace(month=d.month+1,day=1)
    return int(start.timestamp()),int(end.timestamp())

class Economy:
    def __init__(self, registrations, missions=None, clock=None):
        self.reg=registrations; self.db=registrations.db; self.missions=missions; self.clock=clock or time.time
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS eco_settings(guild INTEGER PRIMARY KEY,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS eco_villages(id INTEGER PRIMARY KEY,guild INTEGER NOT NULL,name TEXT NOT NULL,role INTEGER NOT NULL,kage_role INTEGER NOT NULL,UNIQUE(guild,name),UNIQUE(guild,role));
        CREATE TABLE IF NOT EXISTS eco_ranks(guild INTEGER NOT NULL,key TEXT NOT NULL,role INTEGER NOT NULL,monthly INTEGER NOT NULL,tier INTEGER NOT NULL,PRIMARY KEY(guild,key),UNIQUE(guild,role));
        CREATE TABLE IF NOT EXISTS eco_accounts(guild INTEGER NOT NULL,key TEXT NOT NULL,balance INTEGER NOT NULL DEFAULT 0 CHECK(balance>=0),PRIMARY KEY(guild,key));
        CREATE TABLE IF NOT EXISTS eco_transactions(id INTEGER PRIMARY KEY,guild INTEGER NOT NULL,ref TEXT NOT NULL,actor INTEGER NOT NULL,reason TEXT NOT NULL,created INTEGER NOT NULL,UNIQUE(guild,ref));
        CREATE TABLE IF NOT EXISTS eco_entries(tx INTEGER NOT NULL,account TEXT NOT NULL,delta INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS eco_profiles(guild INTEGER NOT NULL,user INTEGER NOT NULL,village INTEGER,rank TEXT,tier INTEGER NOT NULL,roles TEXT NOT NULL,active INTEGER NOT NULL,observed INTEGER NOT NULL,PRIMARY KEY(guild,user));
        CREATE TABLE IF NOT EXISTS eco_service(id INTEGER PRIMARY KEY,guild INTEGER NOT NULL,user INTEGER NOT NULL,village INTEGER NOT NULL,rank TEXT NOT NULL,monthly INTEGER NOT NULL,start INTEGER NOT NULL,end INTEGER);
        CREATE UNIQUE INDEX IF NOT EXISTS eco_one_service ON eco_service(guild,user) WHERE end IS NULL;
        CREATE TABLE IF NOT EXISTS eco_payroll(guild INTEGER NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,village INTEGER NOT NULL,user INTEGER NOT NULL,amount INTEGER NOT NULL,paid INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(guild,start,village,user));
        CREATE TABLE IF NOT EXISTS eco_periods(guild INTEGER NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,PRIMARY KEY(guild,start));
        CREATE TABLE IF NOT EXISTS eco_categories(id INTEGER PRIMARY KEY,guild INTEGER NOT NULL,name TEXT NOT NULL,UNIQUE(guild,name));
        CREATE TABLE IF NOT EXISTS eco_items(id INTEGER PRIMARY KEY,guild INTEGER NOT NULL,village INTEGER NOT NULL,category INTEGER NOT NULL,data TEXT NOT NULL,stock INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS eco_inventory(id INTEGER PRIMARY KEY,guild INTEGER NOT NULL,user INTEGER NOT NULL,item INTEGER NOT NULL,data TEXT NOT NULL,quantity INTEGER NOT NULL CHECK(quantity>=0));
        CREATE TABLE IF NOT EXISTS eco_deals(id INTEGER PRIMARY KEY,guild INTEGER NOT NULL,kind TEXT NOT NULL,seller INTEGER NOT NULL,buyer INTEGER NOT NULL,lot INTEGER NOT NULL,quantity INTEGER NOT NULL,price INTEGER NOT NULL,wanted INTEGER NOT NULL DEFAULT 0,wanted_quantity INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'open',proof TEXT NOT NULL DEFAULT '',created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS eco_mission_payments(report INTEGER PRIMARY KEY,guild INTEGER NOT NULL,village INTEGER NOT NULL,tx INTEGER NOT NULL,revision INTEGER NOT NULL,recipients TEXT NOT NULL,reversed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS eco_mission_receipts(report INTEGER PRIMARY KEY,guild INTEGER NOT NULL,details TEXT NOT NULL,proof TEXT NOT NULL,digest TEXT NOT NULL,tx INTEGER NOT NULL,created INTEGER NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS eco_unique_receipt_image ON eco_mission_receipts(guild,digest);
        CREATE TABLE IF NOT EXISTS eco_audit(id INTEGER PRIMARY KEY,guild INTEGER NOT NULL,actor INTEGER NOT NULL,action TEXT NOT NULL,detail TEXT NOT NULL,created INTEGER NOT NULL);
        ''')
    def now(self): return int(self.clock())
    def rows(self,sql,args=()): return [dict(r) for r in self.db.execute(sql,args)]
    def audit(self,g,a,action,detail): self.db.execute('INSERT INTO eco_audit(guild,actor,action,detail,created) VALUES(?,?,?,?,?)',(g,a,action,dump(detail),self.now()))
    def settings(self,g):
        r=self.db.execute('SELECT data FROM eco_settings WHERE guild=?',(g,)).fetchone()
        return json.loads(r[0]) if r else {'enabled':False,'log_channel':0}
    def configure(self,g,a,**values):
        with self.db:
            d=self.settings(g);d.update(values)
            self.db.execute('INSERT INTO eco_settings VALUES(?,?) ON CONFLICT(guild) DO UPDATE SET data=excluded.data',(g,dump(d)))
            self.audit(g,a,'settings',values)
    def live(self,g):
        if not self.settings(g).get('enabled'): raise ValueError(t('La economía está pausada. Administración debe activarla después de configurar aldeas y rangos.'))
    def balance(self,g,key):
        r=self.db.execute('SELECT balance FROM eco_accounts WHERE guild=? AND key=?',(g,key)).fetchone();return r[0] if r else 0
    def _move(self,g,ref,a,reason,entries):
        old=self.db.execute('SELECT id FROM eco_transactions WHERE guild=? AND ref=?',(g,ref)).fetchone()
        if old: raise ValueError(t('Esta operación ya fue registrada; no se repetirá.'))
        totals=defaultdict(int)
        for key,delta in entries: totals[key]+=delta
        if sum(totals.values())!=0: raise ValueError(t('Movimiento contable desequilibrado.'))
        for key,delta in totals.items():
            if key!='system' and self.balance(g,key)+delta<0: raise ValueError(''.join([format_fragment(t('Fondos insuficientes en '), '', -1), format_fragment(key, '', -1), format_fragment(t('. No se ha transferido dinero.'), '', -1)]))
        cur=self.db.execute('INSERT INTO eco_transactions(guild,ref,actor,reason,created) VALUES(?,?,?,?,?)',(g,ref,a,reason[:500],self.now()));tx=cur.lastrowid
        for key,delta in totals.items():
            if key!='system':
                self.db.execute('INSERT OR IGNORE INTO eco_accounts VALUES(?,?,0)',(g,key))
                self.db.execute('UPDATE eco_accounts SET balance=balance+? WHERE guild=? AND key=?',(delta,g,key))
            self.db.execute('INSERT INTO eco_entries VALUES(?,?,?)',(tx,key,delta))
        return tx
    def villages(self,g): return self.rows('SELECT * FROM eco_villages WHERE guild=? ORDER BY id',(g,))
    def village(self,g,v):
        r=self.rows('SELECT * FROM eco_villages WHERE guild=? AND id=?',(g,v))
        if not r: raise ValueError(t('Aldea inexistente.'))
        return r[0]
    def add_village(self,g,a,name,role,kage,capital):
        amount(capital,True)
        if not name.strip() or len(name)>60 or not role or not kage: raise ValueError(t('Nombre y roles de aldea y Kage obligatorios.'))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute('SELECT 1 FROM eco_villages WHERE guild=? AND (name=? OR role=?)',(g,name.strip(),role)).fetchone():raise ValueError(t('Ya existe una aldea con ese nombre o rol.'))
            c=self.db.execute('INSERT INTO eco_villages(guild,name,role,kage_role) VALUES(?,?,?,?)',(g,name.strip(),role,kage));v=c.lastrowid
            self._move(g,f'village:{v}:initial',a,t('Capital inicial'), [('system',-capital),(f'v:{v}',capital)])
            self.audit(g,a,'village_create',{'id':v,'name':name,'capital':capital})
        return v
    def rank(self,g,a,key,role,monthly=None,tier=None):
        if key not in RANKS: raise ValueError(t('Rango desconocido.'))
        label,default,base=RANKS[key];monthly=default if monthly is None else amount(monthly,True);tier=base if tier is None else tier
        if not 1<=tier<=5: raise ValueError(t('Nivel de permisos entre 1 y 5.'))
        if monthly%2: raise ValueError(t('El mensual debe ser par para dividirlo en dos quincenas enteras.'))
        with self.db:
            if self.db.execute('SELECT 1 FROM eco_ranks WHERE guild=? AND role=? AND key!=?',(g,role,key)).fetchone():raise ValueError(t('Ese rol ya pertenece a otro rango salarial.'))
            self.db.execute('INSERT INTO eco_ranks VALUES(?,?,?,?,?) ON CONFLICT(guild,key) DO UPDATE SET role=excluded.role,monthly=excluded.monthly,tier=excluded.tier',(g,key,role,monthly,tier))
            self.audit(g,a,'rank',{'key':key,'role':role,'monthly':monthly,'tier':tier})
    def observe(self,g,u,roles,present=True):
        """Observe approved registration and roles. Changes apply from observation time."""
        role_set=set(roles);reg=self.reg.find(g,u)
        villages=[v for v in self.villages(g) if v['role'] in role_set]
        ranks=[r for r in self.rows('SELECT * FROM eco_ranks WHERE guild=?',(g,)) if r['role'] in role_set]
        r=max(ranks,key=lambda x:(x['monthly'],x['tier'],x['key'])) if ranks else None
        active=bool(self.settings(g).get('enabled') and present and reg and reg['state']=='approved' and len(villages)==1 and r)
        v=villages[0]['id'] if len(villages)==1 else None;now=self.now()
        with self.db:
            old=self.db.execute('SELECT * FROM eco_service WHERE guild=? AND user=? AND end IS NULL',(g,u)).fetchone()
            same=old and active and (old['village'],old['rank'],old['monthly'])==(v,r['key'],r['monthly'])
            if old and not same: self.db.execute('UPDATE eco_service SET end=? WHERE id=?',(now,old['id']))
            if active and not same: self.db.execute('INSERT INTO eco_service(guild,user,village,rank,monthly,start) VALUES(?,?,?,?,?,?)',(g,u,v,r['key'],r['monthly'],now))
            self.db.execute('INSERT INTO eco_profiles VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(guild,user) DO UPDATE SET village=excluded.village,rank=excluded.rank,tier=excluded.tier,roles=excluded.roles,active=excluded.active,observed=excluded.observed',(g,u,v,r['key'] if r else None,r['tier'] if r else 0,dump(sorted(role_set)),int(active),now))
        return self.profile(g,u)
    def profile(self,g,u):
        r=self.rows('SELECT * FROM eco_profiles WHERE guild=? AND user=?',(g,u));return r[0] if r else None
    def player(self,g,u):
        self.live(g);r=self.profile(g,u);reg=self.reg.find(g,u)
        if not r or not r['active'] or not reg or reg['state']!='approved': raise ValueError(t('Necesitas ficha aprobada, un rol de aldea y un rango económico configurado. Usa /economia acreditar.'))
        return r
    def adjust(self,g,a,key,delta,reason,ref):
        amount(abs(delta))
        if len(reason.strip())<5:raise ValueError(t('Indica un motivo de al menos cinco caracteres.'))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');return self._move(g,ref,a,reason,[('system',-delta),(key,delta)])
    def transfer(self,g,a,target,n,ref):
        amount(n);self.player(g,a);self.player(g,target)
        if target==a:raise ValueError(t('El destinatario debe ser otra persona.'))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');return self._move(g,ref,a,t('Transferencia personal'),[(f'u:{a}',-n),(f'u:{target}',n)])
    def treasury_transfer(self,g,a,source,target,n,reason,ref):
        amount(n);self.village(g,source);self.village(g,target)
        if source==target or len(reason.strip())<5:raise ValueError(t('Aldeas diferentes y motivo obligatorio.'))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');return self._move(g,ref,a,reason,[(f'v:{source}',-n),(f'v:{target}',n)])
    def prepare_payroll(self,g,now=None):
        now=self.now() if now is None else now
        first=self.db.execute('SELECT MIN(start) FROM eco_service WHERE guild=?',(g,)).fetchone()[0]
        if first is None:return
        start,end=period(first)
        while end<=now:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                if not self.db.execute('SELECT 1 FROM eco_periods WHERE guild=? AND start=?',(g,start)).fetchone():
                    totals=defaultdict(Fraction)
                    for s in self.rows('SELECT * FROM eco_service WHERE guild=? AND start<? AND (end IS NULL OR end>?)',(g,end,start)):
                        seconds=max(0,min(s['end'] or end,end)-max(s['start'],start))
                        totals[(s['village'],s['user'])]+=Fraction(s['monthly']*seconds,2*(end-start))
                    for (v,u),n in totals.items():
                        n=int(n)
                        if n:self.db.execute('INSERT INTO eco_payroll VALUES(?,?,?,?,?,?,0)',(g,start,end,v,u,n))
                    self.db.execute('INSERT INTO eco_periods VALUES(?,?,?)',(g,start,end))
            start,end=period(end)
    def pay_payroll(self,g):
        self.live(g);self.prepare_payroll(g);paid=0;blocked=set()
        groups=self.rows('SELECT start,village,SUM(amount) total FROM eco_payroll WHERE guild=? AND paid=0 GROUP BY start,village ORDER BY start,village',(g,))
        for group in groups:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE');v=group['village'];s=group['start']
                if v in blocked:continue
                claims=self.rows('SELECT * FROM eco_payroll WHERE guild=? AND start=? AND village=? AND paid=0',(g,s,v));total=sum(c['amount'] for c in claims)
                if not claims:continue
                if self.balance(g,f'v:{v}')<total:
                    blocked.add(v);continue
                self._move(g,f'payroll:{s}:{v}',0,t('Nómina quincenal'),[(f'v:{v}',-total)]+[(f"u:{c['user']}",c['amount']) for c in claims])
                self.db.execute('UPDATE eco_payroll SET paid=1 WHERE guild=? AND start=? AND village=?',(g,s,v));paid+=len(claims)
        return paid
    def category(self,g,a,name):
        if not name.strip() or len(name)>70:raise ValueError(t('Nombre de categoría de 1 a 70 caracteres.'))
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO eco_categories(guild,name) VALUES(?,?)',(g,name.strip()))
            return self.db.execute('SELECT id FROM eco_categories WHERE guild=? AND name=?',(g,name.strip())).fetchone()[0]
    def save_item(self,g,a,v,c,data,stock=-1,ident=None):
        self.village(g,v)
        if not self.db.execute('SELECT 1 FROM eco_categories WHERE id=? AND guild=?',(c,g)).fetchone():raise ValueError(t('Categoría inexistente.'))
        data=dict(data);amount(data.get('price'),True)
        if data.get('kind') not in KINDS or data.get('rank') not in TIERS or not 1<=len(data.get('name',''))<=80:raise ValueError(t('Nombre, tipo o rango inválido.'))
        if len(data.get('description',''))>1800:raise ValueError(t('Descripción demasiado larga.'))
        if type(stock) is not int or stock< -1:raise ValueError(t('Stock -1 ilimitado o un entero no negativo.'))
        if data.get('image') and not data['image'].startswith('https://'):raise ValueError(t('La imagen debe ser una URL HTTPS.'))
        req=data.setdefault('requires',{})
        if set(req)-{'elements','bloodlines','roles','permissions'}:raise ValueError(t('Requisito desconocido.'))
        for key,values in req.items():
            if not isinstance(values,list) or len(values)>20:raise ValueError(t('Los requisitos deben ser listas de hasta 20 valores.'))
            expected=int if key in ('roles','permissions') else str
            if any(type(x) is not expected for x in values):raise ValueError(t('Tipos inválidos en requisitos.'))
        with self.db:
            if ident:
                self.item(g,ident)
                self.db.execute('UPDATE eco_items SET village=?,category=?,data=?,stock=?,version=version+1 WHERE guild=? AND id=?',(v,c,dump(data),stock,g,ident))
            else:
                ident=self.db.execute('INSERT INTO eco_items(guild,village,category,data,stock) VALUES(?,?,?,?,?)',(g,v,c,dump(data),stock)).lastrowid
            self.audit(g,a,'item_save',{'id':ident,'data':data,'stock':stock})
        return ident
    def item(self,g,ident):
        rows=self.rows('SELECT * FROM eco_items WHERE guild=? AND id=?',(g,ident))
        if not rows:raise ValueError(t('Artículo inexistente.'))
        r=rows[0];r['data']=json.loads(r['data']);return r
    def lot(self,g,ident,user=None):
        rows=self.rows('SELECT * FROM eco_inventory WHERE guild=? AND id=?',(g,ident))
        if not rows or rows[0]['quantity']<1 or (user is not None and rows[0]['user']!=user):raise ValueError(t('Objeto inexistente o sin existencias en ese inventario.'))
        r=rows[0];r['data']=json.loads(r['data']);return r
    def eligible(self,g,u,data):
        p=self.player(g,u)
        if p['tier']<TIERS[data['rank']]:raise ValueError(t('Tu rango no permite usar este artículo.'))
        reg=self.reg.find(g,u)['data'];req=data.get('requires',{})
        if not set(req.get('roles',[]))<=set(json.loads(p['roles'])):raise ValueError(t('Faltan roles requeridos.'))
        draws=Counter(reg.get('element_draws',reg.get('elements',[])))
        if any(draws[x]<n for x,n in Counter(req.get('elements',[])).items()):raise ValueError(t('No cumples los elementos requeridos.'))
        if req.get('elements') and not reg.get('chakra_usable',True):raise ValueError(t('El personaje no puede utilizar chakra elemental.'))
        if not set(req.get('bloodlines',[]))<=set(reg.get('mains',[reg.get('main')])+reg.get('secondary',[])):raise ValueError(t('No cumples los bloodlines requeridos.'))
        for ident in req.get('permissions',[]):
            if not self.db.execute('SELECT 1 FROM eco_inventory WHERE guild=? AND user=? AND item=? AND quantity>0',(g,u,ident)).fetchone():raise ValueError(''.join([format_fragment(t('Falta el permiso previo #'), '', -1), format_fragment(ident, '', -1), '.']))
    def _grant(self,g,u,item,data,q):
        if data['kind']=='permiso':
            if self.db.execute('SELECT 1 FROM eco_inventory WHERE guild=? AND user=? AND item=? AND quantity>0',(g,u,item)).fetchone():raise ValueError(t('El usuario ya tiene ese permiso.'))
            if q!=1:raise ValueError(t('Los permisos se conceden de uno en uno.'))
        return self.db.execute('INSERT INTO eco_inventory(guild,user,item,data,quantity) VALUES(?,?,?,?,?)',(g,u,item,dump(data),q)).lastrowid
    def buy(self,g,u,ident,q,version,ref):
        amount(q)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');r=self.item(g,ident);d=r['data'];self.eligible(g,u,d)
            if not r['active'] or r['version']!=version:raise ValueError(t('El artículo cambió o se retiró. Abre de nuevo la tienda.'))
            if d.get('local_only',False) and self.profile(g,u)['village']!=r['village']:raise ValueError(t('Venta exclusiva para la aldea propietaria.'))
            if r['stock']!=-1 and q>r['stock']:raise ValueError(t('Stock insuficiente.'))
            total=amount(d['price']*q,True)
            tx=self._move(g,ref,u,''.join([format_fragment(t('Compra: '), '', -1), format_fragment(d['name'], '', -1), ' × ', format_fragment(q, '', -1)]),[(f'u:{u}',-total),(f"v:{r['village']}",total)])
            self._grant(g,u,ident,d,q)
            if r['stock']!=-1:self.db.execute('UPDATE eco_items SET stock=stock-? WHERE id=?',(q,ident))
            return tx
    def use(self,g,u,lot,q,ref):
        amount(q)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');r=self.lot(g,lot,u);self.eligible(g,u,r['data'])
            if q>r['quantity']:raise ValueError(t('Cantidad insuficiente.'))
            self._move(g,ref,u,''.join([format_fragment(t('Uso de objeto #'), '', -1), format_fragment(lot, '', -1)]),[])
            if r['data']['kind'] in {'consumible','lanzable'}:self.db.execute('UPDATE eco_inventory SET quantity=quantity-? WHERE id=?',(q,lot))
            self.audit(g,u,'use',{'lot':lot,'quantity':q})
    def propose(self,g,seller,buyer,lot,q,price,kind='market',wanted=0,wanted_q=0):
        self.player(g,seller);amount(q);amount(price,True);r=self.lot(g,lot,seller)
        if buyer==seller:raise ValueError(t('El destinatario debe ser otra persona.'))
        if buyer:self.player(g,buyer)
        if kind=='market':
            if r['data']['kind'] not in PHYSICAL:raise ValueError(t('Solo se intercambian armas, lanzables y consumibles.'))
        elif kind=='teaching':
            if not buyer or r['data']['kind']!='permiso' or not r['data'].get('teachable') or q!=1 or wanted:raise ValueError(t('La enseñanza requiere alumno y un permiso marcado como enseñable.'))
            self.eligible(g,seller,r['data']);self.eligible(g,buyer,r['data'])
        else:raise ValueError(t('Operación desconocida.'))
        if q>r['quantity']:raise ValueError(t('Cantidad insuficiente.'))
        if wanted:
            if not buyer:raise ValueError(t('Indica destinatario para un trueque.'))
            other=self.lot(g,wanted,buyer);amount(wanted_q)
            if other['data']['kind'] not in PHYSICAL or other['quantity']<wanted_q:raise ValueError(t('Contraprestación inválida.'))
        with self.db:
            ident=self.db.execute('INSERT INTO eco_deals(guild,kind,seller,buyer,lot,quantity,price,wanted,wanted_quantity,created) VALUES(?,?,?,?,?,?,?,?,?,?)',(g,kind,seller,buyer,lot,q,price,wanted,wanted_q,self.now())).lastrowid
            self.audit(g,seller,'offer',{'id':ident})
        return ident
    def deal(self,g,ident):
        rows=self.rows('SELECT * FROM eco_deals WHERE guild=? AND id=?',(g,ident))
        if not rows:raise ValueError(t('Oferta inexistente.'))
        return rows[0]
    def _swap(self,g,lot,source,target,q):
        r=self.lot(g,lot,source)
        if r['data']['kind'] not in PHYSICAL or r['quantity']<q:raise ValueError(t('Objeto no disponible para intercambio.'))
        self.db.execute('UPDATE eco_inventory SET quantity=quantity-? WHERE id=?',(q,lot));self._grant(g,target,r['item'],r['data'],q)
    def accept(self,g,u,ident):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');d=self.deal(g,ident);self.player(g,u);self.player(g,d['seller'])
            if d['state']!='open' or u==d['seller'] or d['buyer'] not in (0,u):raise ValueError(t('No puedes aceptar esta oferta.'))
            if d['kind']=='teaching':
                r=self.lot(g,d['lot'],d['seller']);self.eligible(g,u,r['data']);self.eligible(g,d['seller'],r['data'])
                self.db.execute("UPDATE eco_deals SET state='accepted',buyer=? WHERE id=?",(u,ident))
            else:
                self._move(g,f'deal:{ident}',u,t('Mercado entre jugadores'),[(f'u:{u}',-d['price']),(f"u:{d['seller']}",d['price'])])
                self._swap(g,d['lot'],d['seller'],u,d['quantity'])
                if d['wanted']:self._swap(g,d['wanted'],u,d['seller'],d['wanted_quantity'])
                self.db.execute("UPDATE eco_deals SET state='done',buyer=? WHERE id=?",(u,ident))
            self.audit(g,u,'accept',{'id':ident})
    def evidence(self,g,u,ident,proof):
        d=self.deal(g,ident)
        if d['kind']!='teaching' or d['state']!='accepted' or u not in (d['buyer'],d['seller']):raise ValueError(t('Enseñanza no aceptada o no te pertenece.'))
        if not proof.startswith('https://discord.com/channels/'):raise ValueError(t('Pega el enlace al mensaje de Discord con las capturas de la enseñanza.'))
        parts=proof.rstrip('/').split('/')
        if len(parts)<3 or parts[-3]!=str(g) or not parts[-2].isdigit() or not parts[-1].isdigit():raise ValueError(t('La evidencia debe ser un mensaje de este servidor.'))
        with self.db:
            self.db.execute('UPDATE eco_deals SET proof=? WHERE id=?',(proof,ident));self.audit(g,u,'teaching_evidence',{'id':ident,'proof':proof})
    def review_teaching(self,g,a,ident,approve,owner=False):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');d=self.deal(g,ident)
            if d['kind']!='teaching' or d['state']!='accepted':raise ValueError(t('La enseñanza no está pendiente.'))
            if a in (d['seller'],d['buyer']) and not owner:raise ValueError(t('Otro revisor debe evaluar esta enseñanza.'))
            if approve:
                if not d['proof']:raise ValueError(t('Falta la evidencia de enseñanza.'))
                r=self.lot(g,d['lot'],d['seller']);self.eligible(g,d['seller'],r['data']);self.eligible(g,d['buyer'],r['data'])
                self._move(g,f'teaching:{ident}',a,t('Enseñanza aprobada'),[(f"u:{d['buyer']}",-d['price']),(f"u:{d['seller']}",d['price'])])
                self._grant(g,d['buyer'],r['item'],r['data'],1)
            self.db.execute('UPDATE eco_deals SET state=? WHERE id=?',('done' if approve else 'rejected',ident));self.audit(g,a,'teaching_review',{'id':ident,'approved':approve})
    def cancel(self,g,u,ident,admin=False):
        d=self.deal(g,ident)
        if d['state'] not in ('open','accepted') or (u not in (d['seller'],d['buyer']) and not admin):raise ValueError(t('No puedes cancelar esta oferta.'))
        with self.db:
            self.db.execute("UPDATE eco_deals SET state='cancelled' WHERE id=?",(ident,));self.audit(g,u,'cancel',{'id':ident})
    def pay_mission(self,g,a,report,v,n,mode,revision,receipt=None):
        self.live(g);amount(n);self.village(g,v)
        if mode not in ('participante','squad'):raise ValueError(t('Modalidad inválida.'))
        with atomic(self.db):
            r=self.missions.get(report)
            if not r or r['guild']!=g or r['kind']!='mission' or r['state']!='approved' or r['revision']!=revision:raise ValueError(t('El reporte debe estar aprobado y no haber cambiado.'))
            if self.db.execute('SELECT 1 FROM eco_mission_payments WHERE report=?',(report,)).fetchone():raise ValueError(t('Este reporte ya tuvo un pago. No se permite repetirlo, ni tras reabrirlo.'))
            limits=self.settings(g).get('mission_rewards',MISSION_REWARDS).get(r['data'].get('mission_rank'))
            if hasattr(self,'simple_flow'):limits=self.simple_flow.limits(r)
            if not limits or not limits[0]<=n<=limits[1]:raise ValueError(t('La recompensa está fuera del intervalo configurado para ese rango. Administración puede editarlo con /economia recompensa.'))
            users=sorted(x['user'] for x in r['participants'])
            if not users:raise ValueError(t('No hay participantes verificados.'))
            for u in users:self.player(g,u)
            if mode=='squad' and n<len(users):raise ValueError(t('El total debe permitir al menos 1 Ryo por participante.'))
            recipients={str(u):(n if mode=='participante' else n//len(users)+(i<n%len(users))) for i,u in enumerate(users)}
            if hasattr(self,'policy'):
                from worldx_v7 import split_reward
                mode='squad'
                recipients=split_reward(n,r['data'].get('roster') or [{'user':u,'rank':self.profile(g,u)['rank']} for u in users])
            # The receipt and ledger commit together. The client cannot substitute recipients.
            from payment_evidence import identity,same_registration,file_blob,validate_image
            from evidence import safe_path
            roster={x['user']:x for x in r['data'].get('roster',[])}
            if receipt is None:
                snapshots=[identity(self.reg.find(g,u),roster.get(u,{}).get('rank',self.profile(g,u)['rank'])) for u in users]
                receipt={'identities':snapshots,'proof':r.get('proof',''),'source':'report'}
            blob=file_blob(self.reg.root,receipt['proof']);_,digest=validate_image(blob)
            snapshots=receipt['identities']
            if sorted(x['user'] for x in snapshots)!=users:raise ValueError(t('Los datos de la ficha no coinciden con todos los participantes del reporte.'))
            for snap in snapshots:
                if not same_registration(snap,self.reg.find(g,snap['user'])):raise ValueError(t('Una ficha cambió. Abre de nuevo la ficha de pago para actualizar sus datos.'))
                expected=roster.get(snap['user'],{}).get('rank',self.profile(g,snap['user'])['rank'])
                if snap['rank']!=expected:raise ValueError(t('El rango no coincide con el autorizado para esta misión.'))
            duplicate=self.db.execute('SELECT report FROM eco_mission_receipts WHERE guild=? AND digest=?',(g,digest)).fetchone()
            if duplicate:raise ValueError(''.join([format_fragment(t('Esta captura ya está vinculada al pago del reporte #'), '', -1), format_fragment(duplicate[0], '', -1), format_fragment(t('. No puede utilizarse para cobrar otra misión.'), '', -1)]))
            duplicate_report=self.db.execute("SELECT id FROM mission_reports WHERE guild=? AND id!=? AND kind='mission' AND digest=? AND state NOT IN ('deleted','rejected')",(g,report,digest)).fetchone()
            if duplicate_report:raise ValueError(t('La captura pertenece a otro reporte de misión. Usa la evidencia de esta misión.'))
            selected=receipt.get('selected',r.get('owner',users[0]))
            if selected not in users:raise ValueError(t('El shinobi seleccionado no participa en este reporte.'))
            details={'identities':snapshots,'source':receipt.get('source','upload'),'mode':mode,'amount':n,'selected':selected,'mission_rank':r['data'].get('mission_rank'), 'completed_at':r['data'].get('end',''),'report_revision':revision,'evidence':r['data'].get('proofs',[])}
            total=sum(recipients.values())
            tx=self._move(g,f'mission:{report}',a,''.join([format_fragment(t('Misión #'), '', -1), format_fragment(report, '', -1), ' / ', format_fragment(mode, '', -1)]),[(f'v:{v}',-total)]+[(f'u:{u}',m) for u,m in recipients.items()])
            self.db.execute('INSERT INTO eco_mission_payments VALUES(?,?,?,?,?,?,0)',(report,g,v,tx,revision,dump(recipients)))
            self.db.execute('INSERT INTO eco_mission_receipts VALUES(?,?,?,?,?,?,?)',(report,g,dump(details),receipt['proof'],digest,tx,self.now()))
            return tx,recipients
    def reverse_mission(self,g,a,report,reason):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');rows=self.rows('SELECT * FROM eco_mission_payments WHERE guild=? AND report=? AND reversed=0',(g,report))
            if not rows or len(reason.strip())<5:raise ValueError(t('Pago no reversible o motivo incompleto.'))
            p=rows[0];recipients=json.loads(p['recipients']);total=sum(recipients.values())
            self._move(g,f'mission-reverse:{report}',a,reason,[(f"v:{p['village']}",total)]+[(f'u:{u}',-n) for u,n in recipients.items()])
            self.db.execute('UPDATE eco_mission_payments SET reversed=1 WHERE report=?',(report,))
    def issues(self,g):
        return self.rows("SELECT p.report,p.village,r.state FROM eco_mission_payments p JOIN mission_reports r ON r.id=p.report WHERE p.guild=? AND p.reversed=0 AND (r.state!='approved' OR r.revision!=p.revision)",(g,))

    def grant(self,g,a,u,ident,q,reason,ref=None):
        amount(q);self.player(g,u);r=self.item(g,ident)
        if len(reason.strip())<5:raise ValueError(t('Indica un motivo.'))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if ref:self._move(g,ref,a,reason,[])
            lot=self._grant(g,u,ident,r['data'],q)
            self.audit(g,a,'grant',{'user':u,'lot':lot,'reason':reason})
        return lot
    def revoke(self,g,a,lot,q,reason,ref=None):
        amount(q)
        if len(reason.strip())<5:raise ValueError(t('Indica un motivo.'))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');r=self.lot(g,lot)
            if ref:self._move(g,ref,a,reason,[])
            if q>r['quantity']:raise ValueError(t('Cantidad insuficiente.'))
            self.db.execute('UPDATE eco_inventory SET quantity=quantity-? WHERE id=?',(q,lot))
            self.audit(g,a,'revoke',{'user':r['user'],'lot':lot,'quantity':q,'reason':reason})

    def mission_receipt(self,g,report):
        rows=self.rows('SELECT r.*,p.village,p.reversed,p.recipients FROM eco_mission_receipts r JOIN eco_mission_payments p ON p.report=r.report WHERE r.guild=? AND r.report=?',(g,report))
        if not rows:return None
        r=rows[0];r['details']=json.loads(r['details']);r['recipients']=json.loads(r['recipients']);return r
