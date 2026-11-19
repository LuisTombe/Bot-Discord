"""Operaciones atómicas. Ninguna petición HTTP ni await dentro de transacciones."""
import copy
import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from wx_market.domain import dump
from .villages import settings, migrate, canonical

TREASURY='worldx:treasury'

class Rewards:
    def __init__(self,market,assignments,guild,owner,base,cleanup=None,clock=None):
        self.m=market;self.e=market.e;self.db=market.db;self.a=assignments
        self.g=guild;self.owner=owner;self.base=Path(base);self.cleanup=cleanup or (lambda r:{})
        self.clock=clock or market.now;self.c=settings()
        try:self.zone=ZoneInfo(self.c['timezone'])
        except ZoneInfoNotFoundError:
            if self.c['timezone']!='America/Bogota':raise
            from economy_domain import TZ
            self.zone=TZ
        self.validate()
        self.db.executescript(Path(__file__).with_name('schema.sql').read_text())
        if 'retry_at' not in {r[1] for r in self.db.execute('PRAGMA table_info(reward_days)')}:
            self.db.execute('ALTER TABLE reward_days ADD COLUMN retry_at INTEGER NOT NULL DEFAULT 0');self.db.commit()
        with self.m.transaction():
            cursor=self.db.execute('SELECT COALESCE(MAX(id),0) FROM mission_audit WHERE guild=?',(guild,)).fetchone()[0]
            first=not self.one('SELECT guild FROM reward_settings WHERE guild=?',(guild,))
            self.db.execute('INSERT OR IGNORE INTO reward_settings(guild,cursor) VALUES(?,?)',(guild,cursor))
            if first:
                self.db.execute('INSERT OR IGNORE INTO reward_seen SELECT ?,c.report,c.user FROM mission_credits c JOIN mission_reports r ON r.id=c.report WHERE r.guild=?',(guild,guild))
        migrate(assignments,guild)

    def validate(self):
        p=self.c['prizes']
        if sum(x['weight'] for x in p)!=10000 or any(type(x['weight']) is not int or x['weight']<=0 or type(x['amount']) is not int or x['amount']<=0 or x['kind'] not in ('money','reroll') for x in p):raise ValueError('Pesos de ruleta inválidos; deben sumar 10.000.')
        if len({x['id'] for x in p})!=len(p):raise ValueError('Premio duplicado.')
        special=sorted((x['amount'],x['weight']) for x in p if x['kind']=='reroll')
        if special!=[(1,50),(5,10)]:raise ValueError('Rerolls: exactamente 0,5 % para uno y 0,1 % para cinco.')
        if self.c['spin_cost']!=500000:raise ValueError('El giro cuesta 500.000 Ryo.')
        if not self.c['permit_days'] or not set(self.c['permit_days'])<=set(range(2,7)):raise ValueError('Día de permiso fuera de 2–6.')
        if type(self.c['village_cap']) is not int or self.c['village_cap']<1:raise ValueError('Cupo inválido.')
        if len(self.c['daily_money'])!=7 or any(type(n) is not int or n<0 for n in self.c['daily_money']):raise ValueError('Premios diarios inválidos.')
        if set(self.c['permit_rank_weights'])!={'D','C','B','A','S'} or any(type(w) is not int or w<=0 for w in self.c['permit_rank_weights'].values()):raise ValueError('Rangos de permiso inválidos.')
        if not set(self.c['permit_categories'])<= {'subjutsu','taijutsu','sub_modes'}:raise ValueError('Solo se admiten subjutsus, taijutsu y sub-modos.')

    def now(self):return int(self.clock())
    def one(self,sql,args=()):return self.m.one(sql,args)
    def audit(self,user,action,detail):
        self.db.execute('INSERT INTO reward_audit(guild,user,action,detail,created) VALUES(?,?,?,?,?)',(self.g,user,action,dump(detail),self.now()))
    def owner_only(self,user):
        if not self.owner() or user!=self.owner():raise ValueError('Solo BOT_OWNER_ID puede ejecutar esta acción. Administrador no concede acceso.')
    def epoch(self):return self.one('SELECT epoch FROM reward_settings WHERE guild=?',(self.g,))['epoch']
    def credits(self,user):
        r=self.one('SELECT amount FROM reward_rerolls WHERE guild=? AND user=?',(self.g,user));return r['amount'] if r else 0
    def add_rerolls(self,user,amount):
        self.db.execute('INSERT INTO reward_rerolls VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET amount=amount+excluded.amount',(self.g,user,amount))
    def channel(self):return self.one('SELECT channel FROM reward_settings WHERE guild=?',(self.g,))['channel']
    def configure(self,user,channel):
        self.owner_only(user)
        with self.m.transaction():
            self.db.execute('UPDATE reward_settings SET channel=? WHERE guild=?',(channel,self.g));self.audit(user,'channel',{'channel':channel})
    def fund(self,user,amount,ref,source='system'):
        self.owner_only(user)
        if type(amount) is not int or not 1<=amount<=10**12:raise ValueError('Importe inválido.')
        if source!='system' and not source.startswith('v:'):raise ValueError('Origen de fondo inválido.')
        with self.m.transaction():
            tx=self.e._move(self.g,'reward:fund:'+str(ref),user,'Capital del fondo general World X',[(source,-amount),(TREASURY,amount)])
            self.audit(user,'fund',{'amount':amount,'source':source,'tx':tx})
            self.db.execute("UPDATE reward_days SET retry_at=0 WHERE guild=? AND state='pending'",(self.g,))
        return tx

    @staticmethod
    def weighted(items,weight='weight',draw=None):
        total=sum(x[weight] for x in items);n=(draw or secrets.randbelow)(total)
        if not 0<=n<total:raise ValueError('Sorteo fuera de rango.')
        for x in items:
            if n<x[weight]:return copy.deepcopy(x)
            n-=x[weight]
        raise RuntimeError('Distribución inválida.')

    def spin(self,user,reference,draw=None):
        with self.m.transaction():
            old=self.one('SELECT * FROM reward_spins WHERE guild=? AND reference=?',(self.g,str(reference)))
            if old:
                if old['user']!=user:raise ValueError('Giro ajeno.')
                return old
            self.e.player(self.g,user);self.m.provenance(self.g,user)
            last=self.one('SELECT created FROM reward_spins WHERE guild=? AND user=? ORDER BY id DESC LIMIT 1',(self.g,user))
            if last and self.now()-last['created']<self.c['spin_cooldown_seconds']:raise ValueError('Espera unos segundos antes del siguiente giro.')
            cost=self.c['spin_cost']
            if self.m.available(self.g,user)<cost:raise ValueError('Necesitas 500.000 Ryo disponibles; las reservas de compras no cuentan.')
            maximum=max(p['amount'] for p in self.c['prizes'] if p['kind']=='money')
            if self.e.balance(self.g,TREASURY)+cost<maximum:raise ValueError('El fondo general necesita capital para cubrir el premio máximo. No se cobró el giro.')
            prize=self.weighted(self.c['prizes'],draw=draw)
            # Cobra la tarifa completa primero: evita aprovechar el premio para pagar un giro sin saldo.
            fee=self.e._move(self.g,'reward:fee:'+str(reference),user,'Giro ruleta World X',[(f'u:{user}',-cost),(TREASURY,cost)])
            tx=None
            if prize['kind']=='money':
                tx=self.e._move(self.g,'reward:prize:'+str(reference),user,'Premio ruleta World X',[(TREASURY,-prize['amount']),(f'u:{user}',prize['amount'])])
            else:self.add_rerolls(user,prize['amount'])
            result=dict(prize,fee=cost,fee_tx=fee,prize_tx=tx)
            c=self.db.execute('INSERT INTO reward_spins(guild,user,reference,created,result) VALUES(?,?,?,?,?)',(self.g,user,str(reference),self.now(),dump(result)))
            self.audit(user,'spin',dict(result,spin=c.lastrowid))
            return self.one('SELECT * FROM reward_spins WHERE id=?',(c.lastrowid,))

    def permit(self,user):
        # Lectura perezosa. No se inventan requisitos ni se habilitan artículos prohibidos.
        ranks={k:[] for k in self.c['permit_rank_weights']}
        try:self.m.profile(self.g,user)
        except ValueError:return None
        for row in self.db.execute("SELECT data FROM market_items WHERE enabled=1 AND type='permission' AND category IN ('subjutsu','taijutsu','sub_modes') ORDER BY id"):
            it=json.loads(row[0])
            if it['category'] not in self.c['permit_categories'] or it['rank'] not in ranks:continue
            try:self.m.quote(self.g,user,it,strict=True);self.m.check_unique(self.g,user,it)
            except ValueError:continue
            ranks[it['rank']].append(it)
        available=[{'rank':r,'weight':self.c['permit_rank_weights'][r]} for r,items in ranks.items() if items]
        if not available:return None
        rank=self.weighted(available)['rank']
        return secrets.choice(ranks[rank])

    def plan_day(self,user,report,approved_at):
        """Llamada en una transacción: un reporte y una fecha no pueden premiar dos veces."""
        if self.db.execute('SELECT 1 FROM reward_seen WHERE guild=? AND user=? AND report=?',(self.g,user,report)).fetchone():return
        self.db.execute('INSERT INTO reward_seen VALUES(?,?,?)',(self.g,report,user))
        daydate=datetime.fromtimestamp(approved_at,self.zone).date().isoformat()
        if self.db.execute('SELECT 1 FROM reward_days WHERE guild=? AND user=? AND date=?',(self.g,user,daydate)).fetchone():return
        last=self.one('SELECT * FROM reward_cycles WHERE guild=? AND user=?',(self.g,user))
        if last and daydate<=last['last_date']:return
        gap=last and (date.fromisoformat(daydate)-date.fromisoformat(last['last_date'])).days>1
        new=not last or last['day']==7 or (self.c['reset_on_missed_day'] and gap)
        cycle=(last['cycle']+1 if last else 1) if new else last['cycle']
        day=1 if new else last['day']+1
        permit_day=secrets.choice(self.c['permit_days']) if new else last['permit_day']
        result={'kind':'money','amount':self.c['daily_money'][day-1]}
        if day==7:result={'kind':'reroll','amount':1}
        elif day==permit_day:
            it=self.permit(user)
            result={'kind':'permit','item':it} if it else {'kind':'money','amount':self.c['permit_fallback_money'],'fallback':'Sin permisos compatibles verificados no poseídos.'}
        self.db.execute('INSERT INTO reward_cycles VALUES(?,?,?,?,?,?) ON CONFLICT(guild,user) DO UPDATE SET cycle=excluded.cycle,day=excluded.day,permit_day=excluded.permit_day,last_date=excluded.last_date',(self.g,user,cycle,day,permit_day,daydate))
        cur=self.db.execute('INSERT INTO reward_days(guild,user,date,report,cycle,day,result,created) VALUES(?,?,?,?,?,?,?,?)',(self.g,user,daydate,report,cycle,day,dump(result),self.now()))
        self.audit(user,'daily_planned',{'id':cur.lastrowid,'report':report,'cycle':cycle,'day':day,'date':daydate,'result':result})

    def scan(self):
        """Cursor durable: procesa aprobaciones nuevas, incluso tras reiniciar el proceso."""
        with self.m.transaction():
            cursor=self.one('SELECT cursor FROM reward_settings WHERE guild=?',(self.g,))['cursor']
            rows=self.m.rows('SELECT * FROM mission_audit WHERE guild=? AND id>? ORDER BY id LIMIT 100',(self.g,cursor))
            for event in rows:
                if event['action']=='approve':
                    r=self.one('SELECT * FROM mission_reports WHERE id=? AND guild=?',(event['report'],self.g))
                    if r and r['kind']=='mission' and r['state']=='approved':
                        # Incluye participantes acreditados; autorizaciones no son misiones.
                        for c in self.m.rows('SELECT user FROM mission_credits WHERE report=?',(r['id'],)):
                            reg=self.a.reg.find(self.g,c['user'])
                            if reg and reg['state']=='approved':self.plan_day(c['user'],r['id'],event['created'])
                self.db.execute('UPDATE reward_settings SET cursor=? WHERE guild=?',(event['id'],self.g))
        for row in self.m.rows("SELECT id FROM reward_days WHERE guild=? AND state='pending' AND retry_at<=? ORDER BY id LIMIT 50",(self.g,self.now())):
            try:self.settle(row['id'])
            except ValueError as e:
                with self.m.transaction():self.db.execute('UPDATE reward_days SET error=?,retry_at=? WHERE id=?',(str(e)[:300],self.now()+300,row['id']))

    def settle(self,ident):
        with self.m.transaction():
            row=self.one('SELECT * FROM reward_days WHERE guild=? AND id=?',(self.g,ident))
            if not row or row['state']!='pending':return
            r=self.one('SELECT state FROM mission_reports WHERE id=?',(row['report'],))
            if not r or r['state']!='approved':
                self.db.execute("UPDATE reward_days SET state='cancelled',error='Misión revocada' WHERE id=?",(ident,));self.audit(row['user'],'daily_cancelled',{'id':ident});return
            user=row['user'];self.e.player(self.g,user)
            result=json.loads(row['result']);ref='reward:daily:'+str(ident)
            if result['kind']=='permit':
                it=self.m.item(result['item']['id'])
                # Si el perfil cambió durante una interrupción no se otorga un permiso inválido.
                self.m.quote(self.g,user,it,strict=True);self.m.check_unique(self.g,user,it)
                rid=self.db.execute("INSERT INTO market_requests(guild,kind,actor,target,item,price,state,snapshot,created,expires,reviewed_by,reviewed_at,reason,dirty,reference) VALUES(?,'purchase',?,?,?,0,'approved',?,?,?,?,?,'Recompensa automática de misión aprobada',0,?)",(self.g,user,user,it['id'],dump({'item':it,'reward':ident}),self.now(),self.now(),0,self.now(),ref)).lastrowid
                self.db.execute('INSERT INTO market_inventory(guild,user,item,category,snapshot,origin,request,approved_at) VALUES(?,?,?,?,?,?,?,?)',(self.g,user,it['id'],it['category'],dump(it),ref,rid,self.now()))
                self.m.audit(self.g,user,'daily_reward',{'reward':ident,'report':row['report']},rid)
            elif result['kind']=='reroll':self.add_rerolls(user,result['amount'])
            else:
                self.e._move(self.g,ref,user,'Recompensa diaria World X',[(TREASURY,-result['amount']),(f'u:{user}',result['amount'])])
            self.db.execute("UPDATE reward_days SET state='paid',error='' WHERE id=?",(ident,));self.audit(user,'daily_paid',{'id':ident,'result':result})

    def cancel_pending(self,user=None):
        clause=' AND (actor=? OR target=?)' if user is not None else '';args=[self.g]+([user,user] if user is not None else [])
        for r in self.m.rows("SELECT id FROM market_requests WHERE guild=? AND state IN ('offered','pending')"+clause,args):
            self.m.release(r['id']);self.db.execute("UPDATE market_requests SET state='cancelled',reason='Reinicio de ficha',dirty=1 WHERE id=?",(r['id'],))
        if user is None:self.db.execute('DELETE FROM market_profiles WHERE guild=?',(self.g,))
        else:self.db.execute('DELETE FROM market_profiles WHERE guild=? AND user=?',(self.g,user))

    def queue_cleanup(self,reg):
        payload=self.cleanup(reg)
        if payload.get('roles') or payload.get('messages'):
            self.db.execute('INSERT INTO cleanup_jobs(registration,payload) VALUES(?,?)',(reg['id'],dump(payload)))

    def use_reroll(self,user,revision):
        from assignment_domain import PROTECTED, fingerprint
        from identity_domain import clear_identity
        from birth_v71 import new_birth
        from birth_store_v8 import origin, import_history
        with self.m.transaction():
            reg=self.a.reg.find(self.g,user);old=self.a.get(self.g,user)
            if not reg or reg['state'] not in ('approved','draft','correction','rejected') or not old or old['revision']!=revision or old['state']['stage']!=3 or old['state']['frozen'] or not old['state'].get('birth'):raise ValueError('Necesitas un nacimiento terminado, sin bloqueo ni revisión pendiente. Vuelve a abrir /recompensas reroll.')
            if self.credits(user)<1:raise ValueError('No tienes rerolls.')
            import_history(self.a,self.g,self.a.config)
            village=canonical(old['state']['birth']['village'])
            self.cancel_pending(user);self.queue_cleanup(reg)
            for table in ('birth_v8_claims','birth_hardcore_a_origins','birth_population_groups'):
                self.db.execute('DELETE FROM '+table+' WHERE guild=? AND user=?',(self.g,user))
            state=copy.deepcopy(self.a.fresh_state())
            state['birth']=new_birth(state['rules'],village,secrets.SystemRandom().random)
            origin(self.a,self.g,user,state,None)
            state['rules_hash']=fingerprint(state['rules'])
            data={k:v for k,v in reg['data'].items() if k not in PROTECTED and k!='clan'};clear_identity(data);data['catalog_version']=5
            self.db.execute("UPDATE registrations SET state='correction',revision=revision+1,data=?,review_message=NULL,public_message=NULL,roles_done=0,reason='Reroll completo',updated=CURRENT_TIMESTAMP WHERE id=?",(dump(data),reg['id']))
            self.db.execute('DELETE FROM screenshots WHERE registration=?',(reg['id'],))
            self.db.execute('DELETE FROM identity_public_updates WHERE registration=?',(reg['id'],))
            self.db.execute('UPDATE assignments SET revision=revision+1,state=? WHERE guild=? AND user=?',(dump(state),self.g,user))
            self.db.execute('UPDATE reward_rerolls SET amount=amount-1 WHERE guild=? AND user=?',(self.g,user))
            self.db.execute('UPDATE eco_profiles SET active=0 WHERE guild=? AND user=?',(self.g,user))
            self.db.execute('UPDATE eco_service SET end=? WHERE guild=? AND user=? AND end IS NULL',(self.now(),self.g,user))
            self.audit(user,'reroll_used',{'previous_revision':revision,'village':village})
        return self.a.get(self.g,user)

    def counts(self):
        return {t:self.db.execute('SELECT COUNT(*) FROM '+t+' WHERE guild=?',(self.g,)).fetchone()[0] for t in ('registrations','assignments','birth_history_unique','wx_village_slots')}
    def fingerprint(self):
        # Incluye revisiones: una ficha cambiada invalida la confirmación anterior.
        a=[tuple(x) for x in self.db.execute('SELECT id,revision,state FROM registrations WHERE guild=? ORDER BY id',(self.g,))]
        b=[tuple(x) for x in self.db.execute('SELECT user,revision FROM assignments WHERE guild=? ORDER BY user',(self.g,))]
        return hashlib.sha256(dump([a,b,self.epoch()]).encode()).hexdigest()
    def preview_reset(self,user):
        self.owner_only(user);token=secrets.token_hex(8)
        with self.m.transaction():
            self.db.execute('DELETE FROM reward_reset_tokens WHERE expires<?',(self.now(),))
            self.db.execute('INSERT INTO reward_reset_tokens VALUES(?,?,?,?,?,0)',(token,self.g,user,self.now()+300,self.fingerprint()))
        return token,self.counts()
    def reset(self,user,token,confirmation):
        self.owner_only(user)
        if confirmation!=f'BORRAR {self.g}':raise ValueError('Confirmación incorrecta.')
        entry=self.one('SELECT * FROM reward_reset_tokens WHERE token=?',(token,))
        if not entry or entry['guild']!=self.g or entry['owner']!=user or entry['used'] or entry['expires']<self.now() or entry['fingerprint']!=self.fingerprint():raise ValueError('Confirmación vencida o fichas modificadas. Solicita una vista previa nueva.')
        # backup() fuera de la transacción: una conexión SQLite no puede respaldarse a sí misma bloqueada.
        directory=self.base/'respaldos';directory.mkdir(exist_ok=True)
        backup=directory/f'antes-reset-{self.g}-{self.now()}-{token}.sqlite3'
        target=sqlite3.connect(backup)
        try:self.db.backup(target)
        finally:target.close()
        try:backup.chmod(0o600)
        except OSError:pass
        with self.m.transaction():
            if entry['fingerprint']!=self.fingerprint():raise ValueError('Las fichas cambiaron. Repite el reinicio.')
            counts=self.counts();rows=self.m.rows('SELECT id FROM registrations WHERE guild=?',(self.g,))
            maxid=self.db.execute('SELECT COALESCE(MAX(id),0) FROM registrations').fetchone()[0]
            self.db.execute("INSERT INTO reward_counters VALUES('registration_id',?) ON CONFLICT(name) DO UPDATE SET value=MAX(value,excluded.value)",(maxid,))
            for r in rows:
                reg=self.a.reg.get(r['id']);self.queue_cleanup(reg)
                self.db.execute('DELETE FROM screenshots WHERE registration=?',(r['id'],))
                self.db.execute('DELETE FROM identity_public_updates WHERE registration=?',(r['id'],))
                self.db.execute('DELETE FROM audit WHERE registration=?',(r['id'],))
            self.cancel_pending()
            # Marca auditorías históricas ya importadas para que no recreen cupos únicos.
            self.db.execute("INSERT OR IGNORE INTO birth_history_imported SELECT id FROM assignment_audit WHERE guild=? AND action='reset'",(self.g,))
            for table in ('registrations','assignments','births','birth_history_unique','birth_population_groups','birth_hardcore_a_origins','birth_v8_tickets','birth_v8_blocks','birth_v8_claims','wx_village_slots'):
                self.db.execute('DELETE FROM '+table+' WHERE guild=?',(self.g,))
            self.db.execute('DELETE FROM assignment_audit WHERE guild=?',(self.g,))
            self.db.execute('UPDATE eco_profiles SET active=0 WHERE guild=?',(self.g,))
            self.db.execute('UPDATE eco_service SET end=? WHERE guild=? AND end IS NULL',(self.now(),self.g))
            self.db.execute("UPDATE mission_reports SET state='frozen',revision=revision+1,reason='Reinicio global de fichas' WHERE guild=? AND state IN ('pending','draft','correction')",(self.g,))
            self.db.execute('UPDATE world_sessions SET closed=? WHERE guild=? AND closed IS NULL',(self.now(),self.g))
            self.db.execute("UPDATE reward_days SET state='cancelled',error='Reinicio global' WHERE guild=? AND state='pending'",(self.g,))
            cursor=self.db.execute('SELECT COALESCE(MAX(id),0) FROM mission_audit WHERE guild=?',(self.g,)).fetchone()[0]
            self.db.execute('UPDATE reward_settings SET epoch=epoch+1,cursor=? WHERE guild=?',(cursor,self.g))
            self.db.execute('UPDATE reward_reset_tokens SET used=1 WHERE token=?',(token,))
            self.audit(user,'global_registration_reset',{'counts':counts,'backup':backup.name})
        return counts,backup
