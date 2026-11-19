import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from economy_domain import Economy
from worldx_v7 import install_core
from wx_market.domain import Market, validate_item
from wx_market.ui import MarketSystem
from tools.import_catalog import import_html

ROOT=Path(__file__).resolve().parents[1]
class MarketTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=sqlite3.connect(Path(self.tmp.name)/'test.sqlite3');self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
        self.time=10000000
        reg=SimpleNamespace(db=self.db,find=lambda g,u:{'state':'approved'})
        self.e=Economy(reg,clock=lambda:self.time);install_core(self.e)
        self.e.configure(1,99,enabled=True)
        for u in (1,2,3):
            self.db.execute('INSERT INTO eco_profiles VALUES(?,?,?,?,?,?,?,?)',(1,u,1,'genin',1,'[]',1,self.time))
        self.db.commit()
        self.config=json.loads((ROOT/'config/market/settings.json').read_text())
        self.m=Market(self.e,self.config,clock=lambda:self.time)
        MarketSystem.harden(SimpleNamespace(store=self.m))
        self.it={'id':'test','name':'Test jutsu','category':'element_jutsu','type':'permission','rank':'C','price':100,'price_x3':300,'requirements':[{'label':'Rango','policy':'x3','condition':{'field':'rank_level','op':'gte','value':2}},{'label':'Agua','policy':'hard','condition':{'field':'elements','op':'any','value':['water']}}],'emoji':'🌊','enabled':True,'unique':True,'tradable':True,'teachable':True,'teaching_fee':50,'source':'test'}
        self.load()
        self.p={'race':'humano','rank_level':2,'elements':['water'],'bloodlines':[],'tags':[]}
        for u in (1,2,3):
            self.m.set_profile(1,99,u,self.p)
            self.e.adjust(1,99,f'u:{u}',1000,'Test salary',f'seed:{u}')
    def load(self):
        p=Path(self.tmp.name)/'catalog.json';p.write_text(json.dumps({'items':[self.it]}));self.m.load_catalog([p])
    def evidence(self,r,*users):
        for u in users:self.m.evidence(1,u,r,'https://example.org/proof.png','https://discord.com/channels/1/2/3')
    def buy(self,u=1):
        r=self.m.purchase(1,u,'test','buy:'+str(u));self.evidence(r,u);self.m.review(1,99,r,True,'Verificado');return self.m.owns(1,u,'test')['id']
    def test_hold_prevents_legacy_transfer(self):
        r=self.m.purchase(1,1,'test','a');self.assertEqual(self.e.balance(1,'u:1'),1000)
        self.assertEqual(self.m.available(1,1),900)
        with self.assertRaises(ValueError):self.e.transfer(1,1,2,950,'bad')
        self.assertEqual(self.e.balance(1,'u:1'),1000)
        self.m.cancel(1,1,r);self.assertEqual(self.m.available(1,1),1000)
    def test_purchase_atomic_idempotent(self):
        r=self.m.purchase(1,1,'test','a');self.evidence(r,1)
        self.m.review(1,99,r,True,'Validado')
        with self.assertRaises(ValueError):self.m.review(1,99,r,True,'Validado')
        self.assertEqual(self.e.balance(1,'u:1'),900)
        self.assertEqual(self.db.execute('select count(*) from market_inventory').fetchone()[0],1)
    def test_x3_once_and_hard_blocks(self):
        p=dict(self.p,rank_level=0);self.m.set_profile(1,99,1,p)
        self.assertEqual(self.m.quote(1,1,self.it)['price'],300)
        p['elements']=[];self.m.set_profile(1,99,1,p)
        with self.assertRaises(ValueError):self.m.purchase(1,1,'test','a')
        self.assertEqual(self.m.reserved(1,1),0)
    def test_reject_and_expire_release(self):
        r=self.m.purchase(1,1,'test','a');self.m.review(1,99,r,False,'Sin pruebas')
        self.assertEqual(self.m.available(1,1),1000)
        r=self.m.purchase(1,1,'test','b');self.time+=73*3600
        self.assertEqual(self.m.expire(),1);self.assertEqual(self.m.req(1,r)['state'],'expired')
        self.assertEqual(self.m.available(1,1),1000)
    def test_self_review_and_missing_evidence(self):
        r=self.m.purchase(1,1,'test','a')
        for staff in [1,99]:
            with self.assertRaises(ValueError):self.m.review(1,staff,r,True,'Validado')
        self.assertEqual(self.m.req(1,r)['state'],'pending')
    def test_catalog_change_refuses_old_price(self):
        r=self.m.purchase(1,1,'test','a');self.evidence(r,1)
        self.it['price']=200;self.it['price_x3']=600;self.load()
        with self.assertRaises(ValueError):self.m.review(1,99,r,True,'Validado')
        self.assertEqual(self.m.reserved(1,1),100)
    def test_failure_after_debit_rolls_back(self):
        r=self.m.purchase(1,1,'test','a');self.evidence(r,1)
        self.db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON market_inventory BEGIN SELECT RAISE(ABORT,'injected'); END");self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):self.m.review(1,99,r,True,'Validado')
        self.assertEqual(self.e.balance(1,'u:1'),1000);self.assertEqual(self.m.reserved(1,1),100)
    def test_trade_consent_moves_one_item(self):
        lot=self.buy();r=self.m.offer(1,1,2,'trade','trade',lot=lot,amount=70)
        with self.assertRaises(ValueError):self.m.accept(1,3,r)
        with self.assertRaises(ValueError):self.m.review(1,99,r,True,'Validado')
        self.m.accept(1,2,r);self.evidence(r,1,2);self.m.review(1,99,r,True,'Validado')
        self.assertFalse(self.m.owns(1,1,'test'));self.assertTrue(self.m.owns(1,2,'test'))
        self.assertEqual(self.e.balance(1,'u:1'),970);self.assertEqual(self.e.balance(1,'u:2'),930)
    def test_teaching_preserves_teacher(self):
        lot=self.buy();r=self.m.offer(1,1,2,'teaching','teach',lot=lot)
        self.m.accept(1,2,r);self.evidence(r,1,2);self.m.review(1,99,r,True,'Validado')
        self.assertTrue(self.m.owns(1,1,'test'));self.assertTrue(self.m.owns(1,2,'test'))
        self.assertEqual(self.e.balance(1,'u:2'),950)
    def test_trade_does_not_bypass_rank(self):
        lot=self.buy();self.m.set_profile(1,99,2,dict(self.p,rank_level=0))
        with self.assertRaises(ValueError):self.m.offer(1,1,2,'trade','trade',lot=lot,amount=1)
    def test_loan_partial_repayment(self):
        r=self.m.offer(1,1,2,'loan','loan',amount=200,days=7)
        self.assertEqual(self.m.available(1,1),800)
        self.m.accept(1,2,r);self.evidence(r,1);self.m.review(1,99,r,True,'Contrato válido')
        self.assertEqual(self.e.balance(1,'u:2'),1200)
        self.m.repay(1,2,r,50,'part');self.m.repay(1,2,r,150,'rest')
        self.assertEqual(self.e.balance(1,'u:1'),1000)
        with self.assertRaises(ValueError):self.m.repay(1,2,r,1,'more')
    def test_balance_tampering_detected(self):
        self.db.execute("UPDATE eco_accounts SET balance=9999 WHERE key='u:1'");self.db.commit()
        with self.assertRaises(ValueError):self.m.purchase(1,1,'test','bad')
    def test_mission_income_trace(self):
        tx=self.e.adjust(1,99,'u:1',300,'Misión validada','mission:1')
        self.db.execute("INSERT INTO eco_mission_payments VALUES(1,1,1,?,1,'[1]',0)",(tx,));self.db.commit()
        p=self.m.provenance(1,1);self.assertEqual((p['mission_reports'],p['mission_income']),(1,300))
    def test_append_only_audit(self):
        with self.assertRaises(sqlite3.IntegrityError):self.db.execute('DELETE FROM market_audit')
        self.db.rollback()
    def test_restart_reservations_persist(self):
        r=self.m.purchase(1,1,'test','a');m2=Market(self.e,self.config,clock=lambda:self.time)
        self.assertEqual(m2.reserved(1,1),100);self.assertEqual(m2.req(1,r)['state'],'pending')
    def test_disabled_and_duplicate_unique(self):
        r=self.m.purchase(1,1,'test','a');r2=self.m.purchase(1,1,'test','b');self.evidence(r,1);self.evidence(r2,1)
        self.m.review(1,99,r,True,'Verificado')
        with self.assertRaises(ValueError):self.m.review(1,99,r2,True,'Verificado')
        self.it['enabled']=False;self.load()
        with self.assertRaises(ValueError):self.m.purchase(1,2,'test','c')
    def test_expired_profile(self):
        self.time+=31*86400
        with self.assertRaises(ValueError):self.m.purchase(1,1,'test','expired')
    def test_catalog_all_valid_and_rules(self):
        items=[]
        for f in self.config['catalog_files']:items+=json.loads((ROOT/'config/market'/f).read_text())['items']
        for it in items:validate_item(it)
        self.assertEqual(len(items),629)
        self.assertEqual(len(set(x['category'] for x in items)),11)
        medical=[x for x in items if x['name'].startswith('Medical ')]
        self.assertEqual(len(medical),3)
        for it in medical:self.assertIn('medical-corps',json.dumps(it['requirements']))
        for it in items[:612]:
            if it['name']!='Taijutsu Body Dodge':self.assertEqual(it['price'],int(it['source_values']['price'].replace('.','').split()[0]))
    def test_filter_pagination(self):
        files=[ROOT/'config/market'/x for x in self.config['catalog_files']];self.m.load_catalog(files)
        rows,total,page=self.m.search({'element':'water','category':'element_jutsu'},1)
        self.assertGreater(total,0);self.assertLessEqual(len(rows),8)
        for x in rows:self.assertEqual(x['category'],'element_jutsu')
        self.assertEqual(self.m.search({'name':'does not exist'})[1],0)

if __name__=='__main__':unittest.main()
