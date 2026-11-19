"""Synchronous short SQLite transactions. No I/O or await inside transactions."""
from contextlib import contextmanager
from pathlib import Path
from collections import defaultdict
from simple_transactions import atomic
import hashlib
import json
import time
import unicodedata

CATEGORIES = ['element_jutsu','modes','sub_modes','subjutsu','taijutsu','bloodline_jutsu','throwables','consumables','weapons','objects','race_permits']
FIELDS = {'rank_level','level','chi','tai','nin','hp','bloodline_levels','race','bloodlines','elements','tags','village','shinobi_rank'}
SET_FIELDS = {'bloodlines','elements','tags'}
NUM_FIELDS = {'rank_level','level','chi','tai','nin','hp'}
ACTIVE = ('offered','pending')
MAX_MONEY = 10**12
RANK_ALLOWED = {
    'academia': (),
    'estudiante': (),
    'genin': ('D','C'),
    'chunin': ('D','C','B'),
    'police': ('D','C','B'),
    'medical': ('D','C','B'),
    'jounin': ('D','C','B'),
    'anbu': ('D','C','B','A'),
    'sensei': ('D','C','B','A'),
    'subkage': ('D','C','B','A'),
    'sannin': ('D','C','B','A'),
    'kage': ('D','C','B','A','S'),
}
TIER_DEFAULT_RANK = {0:'academia',1:'genin',2:'chunin',3:'jounin',4:'sannin',5:'kage'}

def dump(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def normalize(s): return ''.join(c for c in unicodedata.normalize('NFKD',str(s).lower()) if not unicodedata.combining(c))
def digest(x): return hashlib.sha256(dump(x).encode()).hexdigest()
def money(n,zero=False):
    if type(n) is not int or not (0 if zero else 1)<=n<=MAX_MONEY: raise ValueError('Importe entero fuera de límites.')
    return n

def validate_condition(c):
    if set(c) & {'all','any'}:
        if len(c)!=1 or not isinstance(next(iter(c.values())),list) or not next(iter(c.values())): raise ValueError('Condición all/any inválida.')
        for child in next(iter(c.values())): validate_condition(child)
        return
    if set(c)!={'field','op','value'}: raise ValueError('Condición incompleta.')
    field,op,value=c['field'],c['op'],c['value']
    if field not in FIELDS and not field.startswith('bloodline_levels.'): raise ValueError('Campo desconocido: '+field)
    if op not in ('gte','eq','any','all'): raise ValueError('Operador desconocido.')
    if op=='gte' and (type(value) is not int or value<0): raise ValueError('Umbral numérico inválido.')
    if op in ('any','all') and (not isinstance(value,list) or not value): raise ValueError('Lista de requisitos vacía.')

def satisfies(profile,c):
    if 'all' in c:return all(satisfies(profile,x) for x in c['all'])
    if 'any' in c:return any(satisfies(profile,x) for x in c['any'])
    v=profile
    for part in c['field'].split('.'):
        if not isinstance(v,dict):return False
        v=v.get(part)
    if v is None:return False
    op=c['op'];expected=c['value']
    if op=='gte':return type(v) is int and v>=expected
    if op=='eq':return v==expected
    if op in ('any','all'):
        vals=v if isinstance(v,list) else [v]
        return (any if op=='any' else all)(x in vals for x in expected)
    return False

def validate_item(it):
    required={'id','name','category','type','rank','price','price_x3','requirements','emoji','enabled','unique','tradable','teachable','source'}
    if not required<=it.keys():raise ValueError('Faltan campos en ítem: '+str(required-it.keys()))
    if not isinstance(it['id'],str) or not 1<=len(it['id'])<=100:raise ValueError('ID inválido.')
    if it['category'] not in CATEGORIES or it['rank'] not in tuple('DCBAS'):raise ValueError('Categoría/rango inválido.')
    if it['type'] not in ('permission','physical'):raise ValueError('Tipo inválido.')
    for key in ('enabled','unique','tradable','teachable'):
        if type(it[key]) is not bool:raise ValueError('Indicador booleano inválido: '+key)
    money(it['price'],True)
    if it['price_x3']!=it['price']*3:raise ValueError('Precio ×3 incoherente: '+it['id'])
    if it.get('image_url') and not it['image_url'].startswith('https://'):raise ValueError('Solo imágenes HTTPS.')
    for req in it['requirements']:
        if req.get('policy') not in ('hard','x3') or not req.get('label'):raise ValueError('Requisito sin política/etiqueta.')
        validate_condition(req['condition'])
    for dis in it.get('discounts',[]):
        validate_condition(dis['condition']);money(dis['price'],True)
        if dis['price']>it['price']:raise ValueError('Descuento mayor que precio.')
    if it.get('teachable'):money(it.get('teaching_fee',0),True)

def stat_or_level_requirement(requirement):
    """Stats (CHI/NIN/TAI/HP), nivel de personaje y niveles numéricos de bloodline no bloquean compras."""
    c = requirement.get('condition', {})
    field = c.get('field', '')
    return field in ('level', 'chi', 'nin', 'tai', 'hp') or field.startswith('bloodline_levels.')

def purchase_manual_requirement(item, requirement):
    """Solo el desbloqueo del artículo y niveles/stats se difieren o ignoran en la compra."""
    c = requirement['condition']
    unlock = c == {'field': 'tags', 'op': 'all', 'value': ['unlock:' + item['id']]}
    elemental_level = (item['category'] == 'element_jutsu' and c.get('field') == 'level'
                       and 'Lv' in item.get('source_values', {}).get('level', ''))
    return unlock or elemental_level or stat_or_level_requirement(requirement)

def satisfies_rank_policy(profile, item, requirement, strict=False):
    """Clasificación por Rango Shinobi de Discord:
    - Genin: D - C
    - Chunin hasta Jounin (Chunin, Medical, Police, Jounin): D - B
    - Anbu hasta Subkage (Anbu, Sensei, Subkage, Sannin): D - A
    - Kage: D - S
    Si el usuario tiene un rango explícito (shinobi_rank), aplica esta tabla; si no, usa el rank_level clásico.
    """
    srank = profile.get('shinobi_rank')
    if srank and srank in RANK_ALLOWED:
        return item.get('rank', 'D') in RANK_ALLOWED[srank]
    return satisfies(profile, requirement['condition'])


class Market:
    def __init__(self,economy,config,clock=None):
        self.e=economy;self.db=economy.db;self.config=config;self.clock=clock or time.time
        self.db.executescript(Path(__file__).with_name('schema.sql').read_text())
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA cache_size=-2048')
        self.db.execute('PRAGMA wal_autocheckpoint=250')
        self.db.commit()
    def now(self):return int(self.clock())
    def rows(self,sql,args=()):return [dict(r) for r in self.db.execute(sql,args)]
    def one(self,sql,args=()):
        r=self.rows(sql,args)
        return r[0] if r else None
    @contextmanager
    def transaction(self):
        with atomic(self.db):
            yield
    def audit(self,g,u,action,detail,rid=None):
        self.db.execute('INSERT INTO market_audit(guild,actor,action,request,detail,created) VALUES(?,?,?,?,?,?)',(g,u,action,rid,dump(detail),self.now()))
    def load_catalog(self,files):
        items=[];seen=set()
        for p in files:
            for it in json.loads(Path(p).read_text(encoding='utf-8'))['items']:
                validate_item(it)
                if it['id'] in seen:raise ValueError('ID duplicado: '+it['id'])
                seen.add(it['id']);items.append(it)
        with self.transaction():
            self.db.execute('UPDATE market_items SET enabled=0')
            for it in items:
                self.db.execute('INSERT INTO market_items VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,search_name=excluded.search_name,category=excluded.category,type=excluded.type,rank=excluded.rank,price=excluded.price,price_x3=excluded.price_x3,enabled=excluded.enabled,digest=excluded.digest,data=excluded.data',
                    (it['id'],it['name'],normalize(it['name']),it['category'],it['type'],it['rank'],it['price'],it['price_x3'],int(it['enabled']),digest(it),dump(it)))
                self.db.execute('DELETE FROM market_tags WHERE item=?',(it['id'],))
                for k in ['race','bloodline','element']:
                    for value in it.get('filters',{}).get(k,[]):self.db.execute('INSERT OR IGNORE INTO market_tags VALUES(?,?,?)',(it['id'],k,value))
        return len(items)
    def search(self,filters=None,page=1,size=8):
        filters=filters or {};clauses=['enabled=1'];args=[]
        for k in ['category','type','rank']:
            if filters.get(k):clauses.append(k+'=?');args.append(filters[k])
        if filters.get('name'):clauses.append('instr(search_name,?)>0');args.append(normalize(filters['name']))
        for k in ['race','bloodline','element']:
            if filters.get(k):clauses.append('EXISTS(SELECT 1 FROM market_tags t WHERE t.item=market_items.id AND t.kind=? AND t.value=?)');args.extend([k,filters[k]])
        for k,sign in [('min_price','>='),('max_price','<=')]:
            if filters.get(k) is not None:money(filters[k],True);clauses.append('price'+sign+'?');args.append(filters[k])
        where=' AND '.join(clauses);total=self.db.execute('SELECT COUNT(*) FROM market_items WHERE '+where,args).fetchone()[0]
        page=max(1,min(int(page),max(1,(total+size-1)//size)))
        return self.rows('SELECT id,name,category,type,rank,price,price_x3,json_extract(data,"$.emoji") AS emoji FROM market_items WHERE '+where+' ORDER BY name,id LIMIT ? OFFSET ?',args+[size,(page-1)*size]),total,page
    def item(self,ident):
        r=self.one('SELECT * FROM market_items WHERE id=?',(ident,))
        if not r or not r['enabled']:raise ValueError('Artículo inexistente, desactivado o prohibido.')
        return json.loads(r['data'])
    def profile(self,g,u):
        r=self.one('SELECT * FROM market_profiles WHERE guild=? AND user=?',(g,u))
        if not r or r['verified_at']<self.now()-self.config['profile_ttl_days']*86400:raise ValueError('Perfil sin verificar o vencido. Pide al staff actualizar /mercado perfil.')
        return json.loads(r['data'])
    def set_profile(self,g,staff,u,data):
        if staff==u:raise ValueError('Otro revisor debe verificar tu ficha.')
        if not isinstance(data,dict) or set(data)-FIELDS:raise ValueError('Campos de perfil desconocidos.')
        if data.get('race') not in ('humano','shark','snake','puppet'):raise ValueError('Raza inválida.')
        for k in NUM_FIELDS:
            if type(data.get(k,0)) is not int or not 0<=data.get(k,0)<=100000:raise ValueError('Stat/nivel inválido.')
        for k in SET_FIELDS:
            if not isinstance(data.get(k,[]),list) or not all(isinstance(x,str) for x in data.get(k,[])):raise ValueError('Lista de perfil inválida.')
        if not isinstance(data.get('bloodline_levels',{}),dict) or any(type(v) is not int or v<0 for v in data.get('bloodline_levels',{}).values()):raise ValueError('Niveles BL inválidos.')
        with self.transaction():
            self.db.execute('INSERT INTO market_profiles VALUES(?,?,?,?,?) ON CONFLICT(guild,user) DO UPDATE SET data=excluded.data,verified_by=excluded.verified_by,verified_at=excluded.verified_at',(g,u,dump(data),staff,self.now()))
            self.audit(g,staff,'profile_verified',{'user':u,'profile':data})
    def quote(self,g,u,it,strict=False):
        p=self.profile(g,u);hard=[];soft=[];manual=[]
        for r in it['requirements']:
            c = r['condition']
            unlock = c == {'field': 'tags', 'op': 'all', 'value': ['unlock:' + it['id']]}
            elemental_level = (it['category'] == 'element_jutsu' and c.get('field') == 'level'
                               and 'Lv' in it.get('source_values', {}).get('level', ''))
            if not strict and (unlock or elemental_level):
                manual.append(r['label']);continue
            if not strict and stat_or_level_requirement(r):
                continue
            if r['policy']=='x3' and c.get('field')=='rank_level':
                ok = satisfies_rank_policy(p,it,r,strict)
            else:
                ok = satisfies(p,c)
            if not ok:(hard if r['policy']=='hard' or strict else soft).append(r['label'])
        if hard:raise ValueError('Requisitos obligatorios: '+'; '.join(hard))
        price=it['price']
        for dis in it.get('discounts',[]):
            if satisfies(p,dis['condition']):price=min(price,dis['price'])
        return {'price':price*(3 if soft else 1),'x3':bool(soft),'missing':soft,'manual_review':manual,'profile':p,'item_digest':digest(it)}
    def reserved(self,g,u):return self.db.execute('SELECT COALESCE(SUM(amount),0) FROM market_holds WHERE guild=? AND user=?',(g,u)).fetchone()[0]
    def available(self,g,u):return self.e.balance(g,f'u:{u}')-self.reserved(g,u)
    def provenance(self,g,u,page=1):
        key=f'u:{u}';balance=self.e.balance(g,key)
        ledger=self.db.execute('SELECT COALESCE(SUM(e.delta),0) FROM eco_entries e JOIN eco_transactions t ON t.id=e.tx WHERE t.guild=? AND e.account=?',(g,key)).fetchone()[0]
        if balance!=ledger:raise ValueError('Saldo no conciliado con el libro contable. Administración debe revisarlo.')
        mission=self.one('SELECT COUNT(DISTINCT p.report) AS reports,COALESCE(SUM(e.delta),0) AS income FROM eco_mission_payments p JOIN eco_entries e ON e.tx=p.tx WHERE p.guild=? AND p.reversed=0 AND e.account=? AND e.delta>0',(g,key))
        history=self.rows('SELECT t.id,t.ref,t.reason,t.created,e.delta FROM eco_entries e JOIN eco_transactions t ON t.id=e.tx WHERE t.guild=? AND e.account=? ORDER BY t.id DESC LIMIT 8 OFFSET ?',(g,key,(max(1,page)-1)*8))
        return {'balance':balance,'reserved':self.reserved(g,u),'mission_reports':mission['reports'],'mission_income':mission['income'],'entries':history}
    def req(self,g,rid):
        r=self.one('SELECT * FROM market_requests WHERE guild=? AND id=?',(g,rid))
        if not r:raise ValueError('Solicitud no encontrada en este servidor.')
        return r
    def limit(self,g,u):
        n=self.db.execute("SELECT COUNT(*) FROM market_requests WHERE guild=? AND actor=? AND state IN ('pending','offered')",(g,u)).fetchone()[0]
        if n>=self.config['max_pending_per_user']:raise ValueError('Límite de solicitudes pendientes alcanzado.')
    def hold(self,rid,g,u,n):
        if self.available(g,u)<n:raise ValueError('Saldo disponible insuficiente (hay reservas pendientes).')
        self.provenance(g,u)
        self.db.execute('INSERT INTO market_holds VALUES(?,?,?,?)',(rid,g,u,n))
    def owns(self,g,u,it):return self.one("SELECT id FROM market_inventory WHERE guild=? AND user=? AND item=? AND state='owned'",(g,u,it))
    def check_unique(self,g,u,it):
        if it['unique'] and self.owns(g,u,it['id']):raise ValueError('Ya tienes este permiso.')
    def _new(self,g,kind,actor,target,it,lot,price,snapshot,ref):
        self.limit(g,actor)
        state='pending' if kind=='purchase' else 'offered'
        c=self.db.execute('INSERT INTO market_requests(guild,kind,actor,target,item,lot,price,state,snapshot,created,expires,reference) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (g,kind,actor,target,it,lot,price,state,dump(snapshot),self.now(),self.now()+self.config['request_ttl_hours']*3600,str(ref)))
        rid=c.lastrowid;self.audit(g,actor,'request_created',{'kind':kind,'target':target,'price':price},rid);return rid
    def purchase(self,g,u,ident,ref):
        self.e.player(g,u)
        with self.transaction():
            it=self.item(ident);q=self.quote(g,u,it);self.check_unique(g,u,it)
            q['item']=it;q['funding']=self.provenance(g,u)
            rid=self._new(g,'purchase',u,u,ident,None,q['price'],q,ref);self.hold(rid,g,u,q['price'])
        return rid
    def lot(self,g,u,lid):
        r=self.one("SELECT * FROM market_inventory WHERE guild=? AND user=? AND id=? AND state='owned'",(g,u,lid))
        if not r or r['locked_by']:raise ValueError('Objeto no disponible o reservado.')
        return r
    def offer(self,g,u,target,kind,ref,lot=None,amount=0,days=7):
        if kind not in ('trade','teaching','loan') or u==target:raise ValueError('Oferta o destinatario inválido.')
        self.e.player(g,u);self.e.player(g,target)
        with self.transaction():
            snap={};ident=None
            if kind=='loan':
                money(amount)
                if not 1<=days<=90:raise ValueError('Plazo entre 1 y 90 días.')
                snap={'days':days,'interest':0,'funding':self.provenance(g,u)}
            else:
                inv=self.lot(g,u,lot);it=self.item(inv['item']);ident=it['id']
                if not it['tradable' if kind=='trade' else 'teachable']:raise ValueError('Este ítem no admite esa operación.')
                self.quote(g,u,it,True);q=self.quote(g,target,it,True);self.check_unique(g,target,it)
                # Trade/teaching cannot be used to bypass the buyer's range or intrinsic requirements.
                snap={'item':it,'item_digest':digest(it),'quote':q}
                if kind=='teaching':amount=min(it.get('teaching_fee',0),q['price'])
                money(amount,True)
            rid=self._new(g,kind,u,target,ident,lot,amount,snap,ref)
            if lot:self.db.execute('UPDATE market_inventory SET locked_by=? WHERE id=?',(rid,lot))
            if kind=='loan':self.hold(rid,g,u,amount)
        return rid
    def accept(self,g,u,rid):
        with self.transaction():
            r=self.req(g,rid)
            if r['target']!=u or r['state']!='offered' or r['expires']<=self.now():raise ValueError('Oferta no aceptable.')
            self.e.player(g,u)
            if r['kind']!='loan':self.hold(rid,g,u,r['price'])
            self.db.execute("UPDATE market_requests SET state='pending',dirty=1 WHERE id=?",(rid,));self.audit(g,u,'accepted',{},rid)
    def evidence(self,g,u,rid,url,message_url):
        if not url.startswith('https://') or not message_url.startswith('https://discord.com/channels/'):raise ValueError('Evidencia inválida.')
        with self.transaction():
            r=self.req(g,rid)
            if u not in (r['actor'],r['target']) or r['state'] not in ACTIVE:raise ValueError('No puedes aportar evidencia aquí.')
            ev=json.loads(r['evidence'])
            if len(ev)>=12:raise ValueError('Máximo 12 capturas por solicitud.')
            ev.append({'user':u,'url':url,'message':message_url,'at':self.now()})
            self.db.execute('UPDATE market_requests SET evidence=?,dirty=1 WHERE id=?',(dump(ev),rid));self.audit(g,u,'evidence_added',{'message':message_url},rid)
    def release(self,rid):
        self.db.execute('DELETE FROM market_holds WHERE request=?',(rid,));self.db.execute('UPDATE market_inventory SET locked_by=NULL WHERE locked_by=?',(rid,))
    def cancel(self,g,u,rid):
        with self.transaction():
            r=self.req(g,rid)
            if u not in (r['actor'],r['target']) or r['state'] not in ACTIVE:raise ValueError('No puedes cancelar esta solicitud.')
            self.release(rid);self.db.execute("UPDATE market_requests SET state='cancelled',reason='Cancelada por participante',dirty=1 WHERE id=?",(rid,));self.audit(g,u,'cancelled',{},rid)
    def review(self,g,staff,rid,approve,reason,manual_verified=False):
        # Caller must verify Discord role + review channel at execution time.
        if len(reason.strip())<5:raise ValueError('Motivo de al menos cinco caracteres.')
        with self.transaction():
            r=self.req(g,rid)
            if staff in (r['actor'],r['target']):raise ValueError('No se permite revisar tu propia operación.')
            if r['state']!='pending' or r['expires']<=self.now():raise ValueError('Solicitud resuelta, vencida o sin consentimiento.')
            snap=json.loads(r['snapshot']);tx=None
            if approve:
                if (r['reason'] or '').startswith('CORREGIR:'):raise ValueError('El jugador debe aportar la corrección antes de aprobar.')
                evidence=json.loads(r['evidence']);required={r['actor']} if r['kind'] in ('purchase','loan') else {r['actor'],r['target']}
                if not required<={e['user'] for e in evidence}:raise ValueError('Faltan capturas de los participantes requeridos.')
                self.e.player(g,r['actor']);self.e.player(g,r['target'])
                if r['item']:
                    it=self.item(r['item'])
                    if digest(it)!=snap['item_digest']:raise ValueError('Catálogo modificado: rechaza y crea otra solicitud para confirmar el nuevo precio.')
                    self.check_unique(g,r['target'],it)
                    q=self.quote(g,r['target'],it,strict=r['kind']!='purchase')
                    if r['kind']=='purchase' and q.get('manual_review') and not manual_verified:
                        raise ValueError('Comprueba el desbloqueo y los niveles en las capturas; escribe CONFIRMO en la revisión.')
                    if r['kind']=='purchase' and q.get('manual_review'):
                        self.audit(g,staff,'purchase_requirements_verified',{'requirements':q['manual_review'],'reason':reason},rid)
                    if r['kind']=='purchase' and q['price']!=r['price']:raise ValueError('Requisitos/precio cambiaron. Recrea la solicitud.')
                    if r['kind']!='purchase':
                        self.quote(g,r['actor'],it,True)
                        inv=self.one("SELECT * FROM market_inventory WHERE id=? AND guild=? AND user=? AND state='owned' AND locked_by=?",(r['lot'],g,r['actor'],rid))
                        if not inv:raise ValueError('El permiso de origen ya no está disponible.')
                payer=r['actor'] if r['kind'] in ('purchase','loan') else r['target']
                funding=self.provenance(g,payer)
                self.release(rid)
                receiver='system' if r['kind']=='purchase' else f"u:{r['target'] if r['kind']=='loan' else r['actor']}"
                if r['price']:tx=self.e._move(g,f'market:{rid}',staff,'Mercado '+r['kind'],[(f'u:{payer}',-r['price']),(receiver,r['price'])])
                if r['kind']=='loan':
                    self.db.execute('INSERT INTO market_loans VALUES(?,?,?,?,?,0,?)',(rid,g,r['actor'],r['target'],r['price'],self.now()+snap['days']*86400))
                elif r['kind']=='trade':
                    self.db.execute('UPDATE market_inventory SET user=?,origin=?,request=?,approved_at=? WHERE id=?',(r['target'],f'trade:{rid}',rid,self.now(),r['lot']))
                else:
                    self.db.execute('INSERT INTO market_inventory(guild,user,item,category,snapshot,origin,request,approved_at) VALUES(?,?,?,?,?,?,?,?)',
                        (g,r['target'],it['id'],it['category'],dump(it),r['kind']+':'+str(rid),rid,self.now()))
                    if it.get('grants_race'):
                        p=self.profile(g,r['target']);p['race']=it['grants_race'];self.db.execute('UPDATE market_profiles SET data=? WHERE guild=? AND user=?',(dump(p),g,r['target']))
                self.audit(g,staff,'funding_checked',funding,rid)
            else:self.release(rid)
            self.db.execute('UPDATE market_requests SET state=?,reviewed_by=?,reviewed_at=?,reason=?,tx=?,dirty=1 WHERE id=?',('approved' if approve else 'rejected',staff,self.now(),reason[:1000],tx,rid))
            self.audit(g,staff,'approved' if approve else 'rejected',{'reason':reason,'tx':tx},rid)
        return tx
    def repay(self,g,u,rid,n,ref):
        money(n);self.e.player(g,u)
        with self.transaction():
            loan=self.one('SELECT * FROM market_loans WHERE guild=? AND request=?',(g,rid))
            if not loan or loan['borrower']!=u or n>loan['principal']-loan['repaid']:raise ValueError('Deuda o importe inválido.')
            if self.available(g,u)<n:raise ValueError('Saldo disponible insuficiente.')
            self.provenance(g,u)
            self.e._move(g,'repay:'+str(ref),u,'Devolución préstamo '+str(rid),[(f'u:{u}',-n),(f"u:{loan['lender']}",n)])
            self.db.execute('UPDATE market_loans SET repaid=repaid+? WHERE request=?',(n,rid));self.audit(g,u,'loan_repaid',{'amount':n},rid)
    def consume(self,g,u,lid):
        with self.transaction():
            r=self.lot(g,u,lid)
            if r['category'] not in ('consumables','throwables'):raise ValueError('Este permiso no es consumible.')
            self.db.execute("UPDATE market_inventory SET state='consumed' WHERE id=?",(lid,));self.audit(g,u,'consumed',{'lot':lid})
    def expire(self):
        with self.transaction():
            rows=self.rows("SELECT id,guild FROM market_requests WHERE state IN ('offered','pending') AND expires<=? LIMIT 100",(self.now(),))
            for r in rows:
                self.release(r['id']);self.db.execute("UPDATE market_requests SET state='expired',dirty=1 WHERE id=?",(r['id'],));self.audit(r['guild'],0,'expired',{},r['id'])
        return len(rows)
