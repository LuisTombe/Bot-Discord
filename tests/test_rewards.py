"""Pruebas de integración SQLite: doble gasto, reinicio, cupos y entregas."""
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from domain import Store
from assignment_domain import AssignmentStore, fingerprint
from missions_domain import MissionStore
from economy_domain import Economy
from worldx_v7 import install_core
from wx_market.domain import Market
from wx_market.ui import MarketSystem
from wx_rewards.domain import Rewards,TREASURY
from wx_rewards.villages import normalize,reserve,migrate
from birth_v71 import new_birth
from hardcore_a import upgrade
from wx_rewards.visuals import wheel,daily
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]

class RewardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.reg=Store(self.root/'data/db.sqlite3');self.db=self.reg.db;self.addCleanup(self.db.close)
        self.time=1794780000
        self.config=normalize(upgrade(json.loads((ROOT/'balance_v8.json').read_text())))
        self.a=AssignmentStore(self.reg,self.config)
        self.ms=MissionStore(self.reg,json.loads((ROOT/'misiones_config.json').read_text()),clock=lambda:self.time)
        self.e=Economy(self.reg,self.ms,clock=lambda:self.time);install_core(self.e);self.e.configure(1,99,enabled=True)
        self.m=Market(self.e,json.loads((ROOT/'config/market/settings.json').read_text()),clock=lambda:self.time)
        MarketSystem.harden(SimpleNamespace(store=self.m))
        self.db.execute('CREATE TABLE identity_public_updates(registration INTEGER PRIMARY KEY,revision INTEGER)');self.db.commit()
        self.s=Rewards(self.m,self.a,1,lambda:99,self.root,cleanup=lambda r:{'user':r['user'],'roles':[77]},clock=lambda:self.time)
        for u in (1,2):self.player(u)
        self.s.fund(99,10000000,'capital')
    def player(self,u,g=1,approved=True):
        row=self.reg.start(g,u,{'version':5})
        with self.db:
            self.db.execute('UPDATE registrations SET state=? WHERE id=?',('approved' if approved else 'draft',row['id']))
            self.db.execute('INSERT OR REPLACE INTO eco_profiles VALUES(?,?,?,?,?,?,?,?)',(g,u,1,'genin',1,'[]',1,self.time))
        self.e.adjust(g,99,'u:'+str(u),2000000,'Fixture credit',f'fixture:{u}')
        return self.reg.get(row['id'])
    def report(self,u=1,state='approved',kind='mission',when=None):
        with self.db:
            ident=self.db.execute('INSERT INTO mission_reports(guild,session,owner,kind,state,created) VALUES(1,0,?,?,?,?)',(u,kind,state,self.time)).lastrowid
            if state=='approved':
                self.db.execute('INSERT INTO mission_credits VALUES(?,?,?)',(ident,u,'D'))
                self.db.execute("INSERT INTO mission_audit(guild,report,actor,action,detail,created) VALUES(1,?,99,'approve','{}',?)",(ident,when or self.time))
        return ident
    def finished(self,u=1):
        state=copy.deepcopy(self.a.fresh_state());state.update(stage=3,used={'main':3,'secondary':3,'elements':3},results={'main':[],'secondary':[],'elements':[]})
        state['birth']=new_birth(state['rules'],'konoha',lambda:0.99)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO assignments VALUES(?,?,0,?)',(1,u,json.dumps(state)))
            self.db.execute('INSERT OR REPLACE INTO wx_village_slots VALUES(?,?,?)',(1,u,'konoha'))
        return state
    def test_exact_distribution_boundaries(self):
        counts={x['id']:0 for x in self.s.c['prizes']}
        for n in range(10000):counts[self.s.weighted(self.s.c['prizes'],draw=lambda total,n=n:n)['id']]+=1
        self.assertEqual(counts,{x['id']:x['weight'] for x in self.s.c['prizes']})
    def test_money_conservation_and_full_fee(self):
        old=self.e.balance(1,TREASURY);r=self.s.spin(1,'spin',lambda n:0)
        self.assertEqual(self.e.balance(1,'u:1'),1600000)
        self.assertEqual(self.e.balance(1,TREASURY),old+400000)
        p=json.loads(r['result']);self.assertIsNotNone(p['fee_tx']);self.assertIsNotNone(p['prize_tx'])
        self.assertEqual(self.db.execute('SELECT SUM(delta) FROM eco_entries').fetchone()[0],0)
    def test_idempotent_same_interaction(self):
        a=self.s.spin(1,'spin',lambda n:9999);b=self.s.spin(1,'spin',lambda n:0)
        self.assertEqual(a,b);self.assertEqual(self.s.credits(1),5);self.assertEqual(self.e.balance(1,'u:1'),1500000)
    def test_single_reroll(self):
        self.s.spin(1,'spin',lambda n:9940);self.assertEqual(self.s.credits(1),1)
    def test_full_tariff_needed_even_if_winning(self):
        self.e.adjust(1,99,'u:1',-1600000,'Fixture debit','low')
        with self.assertRaises(ValueError):self.s.spin(1,'x',lambda n:9800)
        self.assertEqual(self.e.balance(1,'u:1'),400000)
    def test_underfunded_does_not_draw_or_charge(self):
        self.e.adjust(1,99,TREASURY,-10000000,'Fixture debit','empty')
        with self.assertRaises(ValueError):self.s.spin(1,'x',lambda n:self.fail('No sortear sin fondos'))
        self.assertEqual(self.e.balance(1,'u:1'),2000000)
    def test_market_holds_respected(self):
        with self.db:
            req=self.m._new(1,'purchase',1,1,None,None,1800000,{},'hold')
            self.db.execute('INSERT INTO market_holds VALUES(?,?,?,?)',(req,1,1,1800000))
        with self.assertRaises(ValueError):self.s.spin(1,'x')
    def test_cooldown(self):
        self.s.spin(1,'a',lambda n:0)
        with self.assertRaises(ValueError):self.s.spin(1,'b')
        self.time+=8;self.s.spin(1,'b',lambda n:0)
    def test_transaction_rolls_back_if_ledger_prize_fails(self):
        original=self.e._move
        def broken(g,ref,*args):
            if ref.startswith('reward:prize'):raise ValueError('test')
            return original(g,ref,*args)
        self.e._move=broken
        with self.assertRaises(ValueError):self.s.spin(1,'bad',lambda n:0)
        self.assertEqual(self.e.balance(1,'u:1'),2000000)
        self.assertFalse(self.db.execute('SELECT 1 FROM reward_spins').fetchone())
    def test_no_reward_on_pending_rejected_or_authorization(self):
        for state in ('pending','rejected'):self.report(state=state)
        self.report(kind='authorization');self.s.scan()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reward_days').fetchone()[0],0)
    def test_first_approved_per_day_only(self):
        self.report();self.report();self.s.scan();self.s.scan()
        self.assertEqual(self.e.balance(1,'u:1'),2025000)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reward_days').fetchone()[0],1)
    def test_same_report_reapproval_next_day_no_reward(self):
        report=self.report();self.s.scan();self.time+=86400
        with self.db:self.db.execute("INSERT INTO mission_audit(guild,report,actor,action,detail,created) VALUES(1,?,99,'approve','{}',?)",(report,self.time))
        self.s.scan();self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reward_days').fetchone()[0],1)
    def test_bogota_day_boundary(self):
        # 04:59 UTC y 05:01 UTC pertenecen a fechas distintas en Bogotá.
        from datetime import datetime,timezone
        self.time=int(datetime(2026,11,15,4,59,tzinfo=timezone.utc).timestamp());self.report();self.s.scan()
        self.time+=120;self.report();self.s.scan()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reward_days').fetchone()[0],2)
    def test_seven_days_and_skip_preserves_progress(self):
        for n in range(7):
            self.report();self.s.scan();self.time+=86400*(3 if n==2 else 1)
        c=self.s.one('SELECT * FROM reward_cycles');self.assertEqual(c['day'],7);self.assertTrue(2<=c['permit_day']<=6)
        self.assertEqual(self.s.credits(1),1)
        self.report();self.s.scan();self.assertEqual(self.s.one('SELECT * FROM reward_cycles')['day'],1)
    def test_underfunded_daily_remains_pending_then_retries_once(self):
        self.e.adjust(1,99,TREASURY,-10000000,'Empty fixture','empty');self.report();self.s.scan()
        r=self.s.one('SELECT * FROM reward_days');self.assertEqual(r['state'],'pending')
        result=r['result'];self.s.fund(99,100000,'refill');self.s.scan();self.s.scan()
        r=self.s.one('SELECT * FROM reward_days');self.assertEqual(r['state'],'paid');self.assertEqual(r['result'],result)
        self.assertEqual(self.e.balance(1,'u:1'),2025000)
    def test_restart_does_not_repay(self):
        self.report();self.s.scan();before=self.e.balance(1,'u:1')
        s=Rewards(self.m,self.a,1,lambda:99,self.root,clock=lambda:self.time);s.scan()
        self.assertEqual(before,self.e.balance(1,'u:1'))
    def test_permission_inventory_delivery(self):
        it={'id':'test-permit','name':'Prueba','category':'taijutsu','type':'permission','rank':'D','price':100,'price_x3':300,'requirements':[],'emoji':'x','enabled':True,'unique':True,'tradable':True,'teachable':False,'source':'test'}
        path=self.root/'cat.json';path.write_text(json.dumps({'items':[it]}));self.m.load_catalog([path]);self.m.set_profile(1,99,1,{'race':'humano'})
        self.report();self.s.scan()
        with self.db:self.db.execute('UPDATE reward_cycles SET permit_day=2')
        self.time+=86400;self.report();self.s.scan();self.s.scan()
        inv=self.s.one('SELECT * FROM market_inventory');self.assertEqual(inv['item'],'test-permit');self.assertTrue(inv['origin'].startswith('reward:daily:'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM market_inventory').fetchone()[0],1)
    def test_permission_excludes_bloodline_and_missing_requirements(self):
        # El catálogo real no ofrece permisos cuando no hay perfil verificado.
        self.m.load_catalog([ROOT/'config/market/catalog_html.json']);self.assertIsNone(self.s.permit(1))
        self.m.set_profile(1,99,1,{'race':'humano'});self.assertIsNone(self.s.permit(1))
    def test_owner_only_even_admin_id(self):
        for user in (1,2,98):
            with self.assertRaises(ValueError):self.s.preview_reset(user)
            with self.assertRaises(ValueError):self.s.fund(user,100,'no')
            with self.assertRaises(ValueError):self.s.configure(user,123)
    def test_reset_requires_phrase(self):
        token,_=self.s.preview_reset(99)
        with self.assertRaises(ValueError):self.s.reset(99,token,'BORRAR')
        self.assertIsNotNone(self.reg.find(1,1))
    def test_reset_stale_confirmation(self):
        token,_=self.s.preview_reset(99)
        with self.db:self.db.execute('UPDATE registrations SET revision=revision+1 WHERE user=1')
        with self.assertRaises(ValueError):self.s.reset(99,token,'BORRAR 1')
    def test_old_assignment_buttons_cannot_mutate_after_reset(self):
        with self.db:self.db.execute("UPDATE registrations SET state='draft' WHERE user=1")
        before=self.a.start(1,1)
        token,_=self.s.preview_reset(99);self.s.reset(99,token,'BORRAR 1')
        self.reg.start(1,1,{'version':5});after=self.a.start(1,1)
        self.assertGreater(after['revision'],before['revision'])
        with self.assertRaises(ValueError):self.a.choose_village(1,1,before['revision'],'konoha')
    def test_reset_expired(self):
        token,_=self.s.preview_reset(99);self.time+=301
        with self.assertRaises(ValueError):self.s.reset(99,token,'BORRAR 1')
    def test_reset_backup_scope_unique_history_and_stale_ids(self):
        self.finished();self.player(5,g=2)
        with self.db:self.db.execute("INSERT INTO birth_history_unique VALUES(1,'unique:test',1)")
        oldid=self.reg.find(1,1)['id'];token,counts=self.s.preview_reset(99);done,path=self.s.reset(99,token,'BORRAR 1')
        self.assertTrue(path.is_file());self.assertFalse(self.reg.find(1,1));self.assertTrue(self.reg.find(2,5))
        self.assertEqual(self.s.counts(),dict(registrations=0,assignments=0,birth_history_unique=0,wx_village_slots=0))
        self.assertEqual(self.e.balance(1,'u:1'),2000000)
        con=sqlite3.connect(path);self.assertEqual(con.execute('SELECT COUNT(*) FROM registrations WHERE guild=1').fetchone()[0],2);con.close()
        new=self.reg.start(1,1,{'version':5});self.assertGreater(new['id'],oldid)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM cleanup_jobs').fetchone()[0],2)
        with self.assertRaises(ValueError):self.s.reset(99,token,'BORRAR 1')
    def test_reroll_consumption_and_three_spins_each(self):
        self.finished()
        with self.db:
            self.s.add_rerolls(1,1);self.db.execute("INSERT INTO birth_history_unique VALUES(1,'unique:test',1)")
        row=self.s.use_reroll(1,0)
        self.assertEqual(row['state']['used'],dict(elements=0,main=0,secondary=0));self.assertEqual(row['state']['stage'],0)
        self.assertEqual(row['state']['birth']['village'],'konoha');self.assertEqual(self.s.credits(1),0)
        self.assertEqual(self.reg.find(1,1)['state'],'correction')
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM birth_history_unique WHERE kind='unique:test'").fetchone())
        with self.assertRaises(ValueError):self.s.use_reroll(1,0)
    def test_village_cap_50(self):
        with self.m.transaction():
            for u in range(1,51):reserve(self.db,1,u,'konoha')
            reserve(self.db,1,1,'konoha')
            with self.assertRaises(ValueError):reserve(self.db,1,51,'konoha')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM wx_village_slots').fetchone()[0],50)
    def test_vinland_alias_same_cap(self):
        with self.m.transaction():
            for u in range(1,51):reserve(self.db,1,u,'vinland')
            with self.assertRaises(ValueError):reserve(self.db,1,51,'iron')
        self.assertNotIn('vinland',self.config['birth_policy']['villages'])
    def test_cap_concurrent_two_last_place(self):
        with self.m.transaction():
            for u in range(10,59):reserve(self.db,1,u,'konoha')
        def claim(user):
            db=sqlite3.connect(self.root/'data/db.sqlite3',timeout=5)
            try:
                with db:db.execute('BEGIN IMMEDIATE');reserve(db,1,user,'konoha')
                return True
            except ValueError:return False
            finally:db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(claim,[1,2]))
        self.assertEqual(sorted(results),[False,True])
    def test_filled_village_does_not_consume_origin(self):
        with self.m.transaction():
            for u in range(10,60):reserve(self.db,1,u,'konoha')
            self.db.execute("UPDATE registrations SET state='draft' WHERE user=1")
        row=self.a.start(1,1)
        with self.assertRaises(ValueError):self.a.choose_village(1,1,row['revision'],'konoha')
        self.assertNotIn('birth',self.a.get(1,1)['state'])
        row=self.a.choose_village(1,1,row['revision'],'dunes');self.assertEqual(row['state']['birth']['village'],'dunes')
    def test_old_approved_reports_not_eligible_on_install(self):
        report=self.report()
        with self.db:self.db.execute('DELETE FROM reward_settings')
        s=Rewards(self.m,self.a,1,lambda:99,self.root,clock=lambda:self.time)
        self.time+=86400
        with self.db:self.db.execute("INSERT INTO mission_audit(guild,report,actor,action,detail,created) VALUES(1,?,99,'approve','{}',?)",(report,self.time))
        s.scan();self.assertFalse(self.db.execute('SELECT 1 FROM reward_days').fetchone())
    def test_deleted_registration_releases_village(self):
        self.finished()
        with self.db:self.db.execute("UPDATE registrations SET state='deleted' WHERE guild=1 AND user=1")
        self.assertFalse(self.db.execute('SELECT 1 FROM wx_village_slots WHERE user=1').fetchone())
        migrate(self.a,1)
        self.assertFalse(self.db.execute('SELECT 1 FROM wx_village_slots WHERE user=1').fetchone())
    def test_cleanup_follows_user_after_new_registration_id(self):
        old=self.reg.find(1,1);token,_=self.s.preview_reset(99);self.s.reset(99,token,'BORRAR 1')
        new=self.reg.start(1,1,{'version':5})
        self.assertNotEqual(new['id'],old['id']);self.assertTrue(self.reg.cleanup(new['id']))
    def test_retry_does_not_starve_new_rewards(self):
        # Una cuenta inactiva aplaza su premio; no bloquea la entrega a otras cuentas.
        self.report(1)
        with self.db:self.db.execute('UPDATE eco_profiles SET active=0 WHERE user=1')
        self.s.scan();pending=self.s.one('SELECT * FROM reward_days')
        self.assertGreater(pending['retry_at'],self.time)
        self.report(2);self.s.scan()
        self.assertEqual(self.e.balance(1,'u:2'),2025000)
    def test_alias_migration_keeps_single_reservation(self):
        state=self.finished();state['birth']['village']='vinland'
        with self.db:self.db.execute('UPDATE assignments SET state=? WHERE user=1',(json.dumps(state),))
        migrate(self.a,1);migrate(self.a,1)
        self.assertEqual(self.a.get(1,1)['state']['birth']['village'],'iron')
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM wx_village_slots WHERE village='iron'").fetchone()[0],1)
    def test_visuals_valid_small_and_gif_finite(self):
        gif=wheel(self.s.c,'reroll5');self.assertLess(len(gif.getvalue()),4*1024*1024)
        im=Image.open(gif);self.assertGreater(im.n_frames,1);self.assertNotIn('loop',im.info)
        png=daily(self.s.c,3,4,1,2);im=Image.open(png);self.assertEqual(im.size,(1120,610))

if __name__=='__main__':unittest.main()
