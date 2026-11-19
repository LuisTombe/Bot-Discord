"""Pruebas de dinero y autorizaciones con SQLite real, sin conectar a Discord."""
import json
import sqlite3
import tempfile
import unittest
import io
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from missions_domain import MissionStore, formatted
from economy_domain import Economy
from worldx_v7 import install_core
from v7_missions import install
from mission_evidence_policy import EvidencePolicy
from payment_evidence import validate_image
from wx_market.domain import Market
from simple_flow_domain import SimpleFlow, day_end, NORMAL
from simple_transactions import atomic

ROOT=Path(__file__).resolve().parents[1]

class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.db=sqlite3.connect(self.root/'db.sqlite');self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
        self.now=1794546000;self.seq=0
        def reg(g,u):return {'id':u,'user':u,'revision':1,'state':'approved','data':{'roblox_name':f'Player{u}','age':'20','display_name':'Player','discord_name':'Discord','elements':['water'],'main':'paper'}}
        self.reg=SimpleNamespace(db=self.db,root=self.root,find=reg)
        self.ms=MissionStore(self.reg,json.loads((ROOT/'misiones_config.json').read_text()),lambda:self.now)
        self.e=Economy(self.reg,self.ms,lambda:self.now);install_core(self.e);self.e.configure(1,99,enabled=True)
        self.e.add_village(1,99,'Ember',111,112,1000000)
        for u in (1,2):self.db.execute('INSERT INTO eco_profiles VALUES(?,?,?,?,?,?,?,?)',(1,u,1,'chunin',2,'[]',1,self.now))
        self.db.commit()
        install(SimpleNamespace(store=self.ms,config=self.ms.config),self.e,{'BOT_OWNER':99})
        self.policy=EvidencePolicy(self.ms,json.loads((ROOT/'config/missions/evidence.json').read_text()));self.policy.install()
        self.m=Market(self.e,json.loads((ROOT/'config/market/settings.json').read_text()),lambda:self.now)
        self.flow=SimpleFlow(self.ms,self.e,self.m)
        for k,(label,n) in NORMAL.items():self.policy.config['missions'][k]={'label':label,'base_rank':'D','worlds':['rpg'],'min_images':1,'evidence':'Fecha/hora, objetivo y username.'}
        self.session=self.flow.daily_session(1,1);self.now+=120
        for u in (1,2):
            r=self.ms.start(1,u,'authorization');p=self.image()
            r=self.ms.edit(r['id'],r['revision'],u,{'simple_flow':True,'rank':'chunin','level':'250','stats':'100 100 100 100','village':'Ember','counterparty':'Individual','purpose':'Misiones','proofs':[p]},p)
            r=self.ms.submit(r['id'],r['revision'],u);self.flow.attest_all(r['id'],r['revision'],99);self.ms.decision(r['id'],r['revision'],99,'approve','Verificado')
        self.now+=600
    def image(self):
        self.seq+=1;b=io.BytesIO();Image.new('RGB',(32,32),(self.seq,0,0)).save(b,'PNG');ext,digest=validate_image(b.getvalue());p=f'capturas/reportes/{self.seq}{ext}';(self.root/p).parent.mkdir(parents=True,exist_ok=True);(self.root/p).write_bytes(b.getvalue());return {'path':p,'digest':digest}
    def mission(self,kind='proteger_carga',members=None):
        r=self.ms.start(1,1,'mission');p=self.image()
        d={'simple_flow':True,'type':kind,'world':'rpg' if kind in NORMAL else 'world_x','enemy':'none','members':members or [1],'opponents':'Objetivo','notes':'Bien','start':formatted(self.now-300)[:16],'end':formatted(self.now-60)[:16],'proofs':[p]}
        r=self.ms.edit(r['id'],r['revision'],1,d,p);return self.ms.submit(r['id'],r['revision'],1)
    def pay(self,r,n=10000):
        self.flow.attest_all(r['id'],r['revision'],99)
        return self.flow.approve_pay(1,99,r['id'],r['revision'],n)
    def test_pay_once_counter_and_ledger(self):
        r=self.mission();tx,recipients=self.pay(r)
        self.assertEqual(self.e.balance(1,'u:1'),10000);self.assertEqual(self.ms.totals(1,1)['C'],1)
        with self.assertRaises(ValueError):self.flow.approve_pay(1,99,r['id'],r['revision'],10000)
        self.assertEqual(self.e.balance(1,'u:1'),10000)
    def test_insufficient_treasury_rolls_back_approval_and_credits(self):
        self.e.adjust(1,99,'v:1',-1000000,'vaciar','empty')
        r=self.mission()
        with self.assertRaises(ValueError):self.pay(r)
        self.assertEqual(self.ms.get(r['id'])['state'],'pending');self.assertEqual(sum(self.ms.totals(1,1).values()),0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM eco_mission_payments').fetchone()[0],0)
    def test_no_separate_approval_new_report(self):
        r=self.mission();self.flow.attest_all(r['id'],r['revision'],99)
        with self.assertRaises(ValueError):self.ms.decision(r['id'],r['revision'],99,'approve','No pagar')
    def test_missing_evidence_checks_blocks_payment(self):
        r=self.mission()
        with self.assertRaises(ValueError):self.flow.approve_pay(1,99,r['id'],r['revision'],10000)
    def test_self_review_blocked(self):
        r=self.mission()
        with self.assertRaises(ValueError):self.flow.attest_all(r['id'],r['revision'],1)
        with self.assertRaises(ValueError):self.flow.approve_pay(1,1,r['id'],r['revision'],10000)
    def test_normal_fixed_payments(self):
        for k,(_,n) in NORMAL.items():
            r=self.mission(k)
            with self.assertRaises(ValueError):self.pay(r,n+1)
            self.pay(r,n)
        self.assertEqual(self.e.balance(1,'u:1'),2250)
    def test_squad_one_total_credit_each(self):
        r=self.mission(members=[1,2]);_,paid=self.pay(r)
        self.assertEqual(sum(paid.values()),10000)
        self.assertEqual(self.ms.totals(1,1)['C'],1);self.assertEqual(self.ms.totals(1,2)['C'],1)
    def test_day_rollover(self):
        self.now=day_end(self.now)+1
        with self.assertRaises(ValueError):self.flow.active(1,1)
        s=self.flow.daily_session(1,1);self.assertNotEqual(s['id'],self.session['id'])
        with self.assertRaises(ValueError):self.flow.active(1,1)
    def test_ended_authorization_blocks_activity(self):
        self.ms.end_authorization(1,1,self.session['id'])
        with self.assertRaises(ValueError):self.flow.active(1,1)
    def test_correction_invalidates_old_verification(self):
        r=self.mission();self.flow.attest_all(r['id'],r['revision'],99)
        r=self.ms.decision(r['id'],r['revision'],99,'correct','Otra captura')
        r=self.ms.submit(r['id'],r['revision'],1)
        with self.assertRaises(ValueError):self.flow.approve_pay(1,99,r['id'],r['revision'],10000)
    def test_restart_preserves_pending_and_payment(self):
        r=self.mission();self.pay(r)
        other=sqlite3.connect(self.root/'db.sqlite')
        try:self.assertEqual(other.execute('SELECT count(*) FROM eco_mission_payments').fetchone()[0],1)
        finally:other.close()
    def test_profile_no_json_race_and_registration(self):
        p=self.flow.seed_profile(1,1);self.assertEqual(p['bloodlines'],['paper']);self.assertEqual(p['chi'],100)
        with self.assertRaises(ValueError):self.flow.propose_profile(1,1,{**p,'race':'invalida'},[self.image()])
        p['race']='humano';p['bloodline_levels']={'paper':150};p['tags']=[]
        rid=self.flow.propose_profile(1,1,p,[self.image()])
        with self.assertRaises(ValueError):self.flow.review_profile(1,1,rid,True,'yo')
        self.flow.review_profile(1,99,rid,True,'verificado')
        self.assertEqual(self.m.profile(1,1)['race'],'humano')
    def test_nested_transaction_rollback(self):
        with self.assertRaises(ValueError):
            with atomic(self.db):
                with atomic(self.db):self.db.execute("INSERT INTO simple_notifications VALUES('test',1,1,1)")
                raise ValueError('Simular fallo final')
        self.assertIsNone(self.db.execute("SELECT 1 FROM simple_notifications WHERE kind='test'").fetchone())
    def test_failure_after_money_movement_rolls_everything_back(self):
        r=self.mission();self.flow.attest_all(r['id'],r['revision'],99)
        original=self.e._move
        def broken(*a,**kw):
            original(*a,**kw)
            raise RuntimeError('Fallo después de contabilizar')
        self.e._move=broken
        with self.assertRaises(RuntimeError):self.flow.approve_pay(1,99,r['id'],r['revision'],10000)
        self.assertEqual(self.e.balance(1,'u:1'),0);self.assertEqual(self.ms.get(r['id'])['state'],'pending')
        self.assertEqual(sum(self.ms.totals(1,1).values()),0)
    def test_legacy_normal_draft_cannot_bypass_authorization(self):
        data={'type':'azul_gato','members':[1],'world':'rpg','start':formatted(self.now-60)[:16],'end':formatted(self.now)[:16]}
        p=self.image();data['proofs']=[p]
        with self.db:
            c=self.db.execute("INSERT INTO mission_reports(guild,session,owner,kind,data,proof,digest,created) VALUES(1,0,1,'mission',?,?,?,?)",(json.dumps(data),p['path'],p['digest'],self.now))
        with self.assertRaises(ValueError):self.ms.submit(c.lastrowid,0,1)
    def test_normal_mission_requires_companion_authorization(self):
        self.db.execute("UPDATE mission_reports SET state='rejected' WHERE kind='authorization' AND owner=2");self.db.commit()
        with self.assertRaises(ValueError):self.mission('normal_verde',[1,2])
    def test_profile_registration_change_invalidates_verified_profile(self):
        p=self.flow.seed_profile(1,1);p.update(race='humano',bloodline_levels={},tags=[])
        rid=self.flow.propose_profile(1,1,p,[self.image()]);self.flow.review_profile(1,99,rid,True,'OK')
        original=self.reg.find
        def changed(g,u):
            r=original(g,u);r['data']['elements']=['fire'];return r
        self.reg.find=changed
        with self.assertRaises(ValueError):self.m.profile(1,1)
    def test_single_execution_capture_accepted_under_new_rule(self):
        self.policy.config['missions']['ejecutar']['min_images']=1
        r=self.mission('ejecutar');self.pay(r,25000)
        self.assertEqual(self.ms.totals(1,1)['B'],1)
    def test_session_boundary_blocks_late_mission(self):
        self.now=day_end(self.now)+120
        with self.assertRaises(ValueError):self.mission()
