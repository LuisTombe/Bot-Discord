import test_market
import json

class PurchaseManualTests(test_market.MarketTests):
    def setUp(self):
        super().setUp()
        self.it['requirements'].append({'label':'Condiciones y desbloqueo del HTML verificados por staff','policy':'hard','condition':{'field':'tags','op':'all','value':['unlock:test']}})
        self.it['source_values']={'level':'Wind Lv 27'}
        self.it['requirements'].append({'label':'Wind Lv 27','policy':'hard','condition':{'field':'level','op':'gte','value':27}})
        self.load()
    def test_request_without_internal_tag_and_review(self):
        q=self.m.quote(1,1,self.it)
        self.assertEqual(len(q['manual_review']),2)
        r=self.m.purchase(1,1,'test','manual')
        self.assertEqual(self.m.reserved(1,1),100)
        self.evidence(r,1)
        with self.assertRaises(ValueError):self.m.review(1,99,r,True,'Revisado')
        self.assertEqual(self.m.req(1,r)['state'],'pending')
        self.assertEqual(self.e.balance(1,'u:1'),1000)
        self.m.review(1,99,r,True,'CONFIRMO',manual_verified=True)
        self.assertEqual(self.e.balance(1,'u:1'),900)
        self.assertIsNotNone(self.m.owns(1,1,'test'))
        with self.assertRaises(ValueError):self.m.review(1,99,r,True,'CONFIRMO',manual_verified=True)
    def test_no_proof_no_approval(self):
        r=self.m.purchase(1,1,'test','no-proof')
        with self.assertRaises(ValueError):self.m.review(1,99,r,True,'CONFIRMO',manual_verified=True)
    def test_intrinsic_requirement_still_blocks(self):
        self.m.set_profile(1,99,1,dict(self.p,elements=[]))
        with self.assertRaises(ValueError):self.m.purchase(1,1,'test','wrong-element')
    def test_strict_trade_still_blocks(self):
        with self.assertRaises(ValueError):self.m.quote(1,1,self.it,strict=True)
    def test_rejection_returns_hold(self):
        r=self.m.purchase(1,1,'test','reject')
        self.m.review(1,99,r,False,'Prueba incorrecta')
        self.assertEqual(self.m.reserved(1,1),0)
        self.assertEqual(self.e.balance(1,'u:1'),1000)

# Heredar solo la preparación; la suite original se ejecuta por separado.
for name in list(test_market.MarketTests.__dict__):
    if name.startswith('test_') and name not in PurchaseManualTests.__dict__:
        setattr(PurchaseManualTests,name,None)
