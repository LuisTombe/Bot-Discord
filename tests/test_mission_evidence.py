"""Pruebas del motor real: evidencia visual humana obligatoria, sin conexión Discord."""
import copy
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from missions_domain import MissionStore, formatted, classify
from economy_domain import Economy
from worldx_v7 import install_core
from v7_missions import install
from mission_evidence_policy import EvidencePolicy, has_shinobi, visible_timestamp
from payment_evidence import validate_image
ROOT=Path(__file__).resolve().parents[1]

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.db=sqlite3.connect(self.root/'test.sqlite3');self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
        self.now=1794546000;self.counter=0
        reg=SimpleNamespace(db=self.db,root=self.root,find=lambda g,u:{'id':u,'user':u,'state':'approved','data':{'roblox_name':f'Player{u}'}})
        self.config=json.loads((ROOT/'misiones_config.json').read_text());self.ms=MissionStore(reg,self.config,clock=lambda:self.now)
        self.eco=Economy(reg,clock=lambda:self.now);install_core(self.eco);self.eco.configure(1,99,enabled=True)
        self.db.execute("INSERT INTO eco_profiles VALUES(1,1,1,'chunin',2,'[]',1,?)",(self.now,));self.db.commit()
        install(SimpleNamespace(store=self.ms,config=self.config),self.eco,{'BOT_OWNER':1})
        self.rules=json.loads((ROOT/'config/missions/evidence.json').read_text());self.policy=EvidencePolicy(self.ms,self.rules);self.policy.install()
        self.session=self.ms.open(1,99,'Prueba');self.now+=60
        self.db.execute("UPDATE world_sessions SET opened=? WHERE id=?",(self.now-3600,self.session['id']))
        auth={'rank':'chunin','level':250,'stats':'100 100 100 100','village':'Ember','counterparty':'Aliado','purpose':'Misión'}
        self.db.execute("INSERT INTO mission_reports(guild,session,owner,kind,state,data,created,granted) VALUES(1,?,1,'authorization','approved',?,?,?)",(self.session['id'],json.dumps(auth),self.now-1800,self.now-1800));self.db.commit()
    def image(self):
        self.counter+=1;blob=io.BytesIO();Image.new('RGB',(32,32),(self.counter,40,90)).save(blob,'PNG')
        ext,digest=validate_image(blob.getvalue());path=f'capturas/reportes/p{self.counter}{ext}';p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(blob.getvalue());return {'path':path,'digest':digest}
    def draft(self,kind='proteger_carga',count=1,world='open_world'):
        row=self.ms.start(1,1,'mission');proofs=[self.image() for _ in range(count)]
        data={'type':kind,'world':world,'enemy':'none','members':[1],'opponents':'Sin enemigo','notes':'Resultado declarado','start':formatted(self.now-600)[:16],'end':formatted(self.now-60)[:16],'proofs':proofs}
        return self.ms.edit(row['id'],row['revision'],1,data,proofs[0])
    def pending(self,kind='proteger_carga',count=1):
        r=self.draft(kind,count);return self.ms.submit(r['id'],r['revision'],1)
    def check(self,r,index=0,actor=99):self.policy.attest(r['id'],r['revision'],actor,index,formatted(self.now-120)[:16],'-05:00','Objetivo y panel legibles; fecha/hora presentes.','CONFIRMO')
    def test_shinobi_exact_id_and_no_admin_bypass(self):
        has_shinobi({777},777)
        for roles,key in [({1,2},777),({777},0)]:
            with self.assertRaises(ValueError):has_shinobi(roles,key)
    def test_no_approval_without_visual_attestation(self):
        r=self.pending()
        with self.assertRaises(ValueError):self.ms.decision(r['id'],r['revision'],99,'approve','Bien')
        self.assertEqual(self.ms.totals(1,1)['C'],0)
    def test_approval_and_double_click(self):
        r=self.pending();self.check(r);done=self.ms.decision(r['id'],r['revision'],99,'approve','Verificada')
        self.assertEqual(done['state'],'approved');self.assertEqual(self.ms.totals(1,1)['C'],1)
        with self.assertRaises(ValueError):self.ms.decision(r['id'],r['revision'],99,'approve','Otra vez')
        self.assertEqual(self.ms.totals(1,1)['C'],1)
    def test_missing_date_rejects_without_credits(self):
        r=self.pending();self.ms.decision(r['id'],r['revision'],99,'reject','No hay fecha/hora visible')
        self.assertEqual(sum(self.ms.totals(1,1).values()),0)
        self.assertEqual(self.ms.get(r['id'])['state'],'rejected')
    def test_invalid_date_and_mismatched_interval(self):
        r=self.pending()
        for stamp in ['sin fecha','01/01/2000 00:00']:
            with self.assertRaises(ValueError):self.policy.attest(r['id'],r['revision'],99,0,stamp,'-05:00','Observación','CONFIRMO')
    def test_device_timezone_conversion(self):
        self.assertEqual(visible_timestamp('13/11/2026 01:38','-05:00'),visible_timestamp('13/11/2026 07:38','+01:00'))
        with self.assertRaises(ValueError):visible_timestamp('13/11/2026 01:38','+14:30')
    def test_owner_cannot_self_attest_or_approve(self):
        r=self.pending()
        with self.assertRaises(ValueError):self.check(r,actor=1)
        with self.assertRaises(ValueError):self.ms.decision(r['id'],r['revision'],1,'approve','Soy dueño',True)
    def test_each_capture_required_for_execution(self):
        r=self.pending('ejecutar',2);self.check(r,0)
        with self.assertRaises(ValueError):self.ms.decision(r['id'],r['revision'],99,'approve','Incompleta')
        self.check(r,1);self.ms.decision(r['id'],r['revision'],99,'approve','Dos capturas verificadas')
        self.assertEqual(self.ms.totals(1,1)['B'],1)
    def test_execution_one_image_blocked(self):
        r=self.draft('ejecutar',1)
        with self.assertRaises(ValueError):self.ms.submit(r['id'],r['revision'],1)
    def test_other_reviewer_must_verify_independently(self):
        r=self.pending();self.check(r)
        with self.assertRaises(ValueError):self.ms.decision(r['id'],r['revision'],100,'approve','No la revisé')
    def test_correction_invalidates_checks(self):
        r=self.pending();self.check(r);r=self.ms.decision(r['id'],r['revision'],99,'correct','Corrige hora')
        r=self.ms.edit(r['id'],r['revision'],1,{**r['data'],'notes':'Texto corregido'});r=self.ms.submit(r['id'],r['revision'],1)
        with self.assertRaises(ValueError):self.ms.decision(r['id'],r['revision'],99,'approve','Vieja revisión')
    def test_duplicate_image_on_another_mission(self):
        r=self.pending();original=r['data']['proofs'][0];other=self.draft()
        d={**other['data'],'proofs':[original]};other=self.ms.edit(other['id'],other['revision'],1,d,original)
        with self.assertRaises(ValueError):self.ms.submit(other['id'],other['revision'],1)
    def test_modified_file_detected(self):
        r=self.pending();p=self.root/r['proof'];p.write_bytes(p.read_bytes()+b'x')
        with self.assertRaises(ValueError):self.check(r)
    def test_fake_png_and_quota(self):
        with self.assertRaises(ValueError):validate_image(b'\x89PNG\r\n\x1a\nnot an image')
        self.rules['max_evidence_disk_mb']=0
        with self.assertRaises(ValueError):self.policy.check_upload_budget(1)
    def test_restart_preserves_attestation(self):
        r=self.pending();self.check(r);restored=EvidencePolicy(self.ms,self.rules);restored.require_checks(r,99)
    def test_rank_scales_with_danger(self):
        for enemy,expected in [('none','C'),('chunin','B'),('jounin','A'),('kage','S')]:self.assertEqual(classify(self.config,'proteger_carga',enemy),expected)
    def test_world_type_incompatible(self):
        r=self.draft('proteger_carga',1,'rpg')
        with self.assertRaises(ValueError):self.ms.submit(r['id'],r['revision'],1)
    def test_authorization_does_not_require_active_mission_panel(self):
        r=self.ms.start(1,2,'authorization');p=self.image()
        data={'rank':'chunin','level':250,'stats':'100 100 100 100','village':'Ember','counterparty':'Aliado','purpose':'Misión','proofs':[p]}
        r=self.ms.edit(r['id'],r['revision'],2,data,p);r=self.ms.submit(r['id'],r['revision'],2)
        self.assertNotIn('Panel con el nombre/objetivo de misión visible',self.policy.checklist(r))
        self.check(r);done=self.ms.decision(r['id'],r['revision'],99,'approve','Autorización revisada')
        self.assertEqual(done['state'],'approved');self.assertIsNotNone(done['granted'])
        self.assertEqual(sum(self.ms.totals(1,2).values()),0)
    def test_blue_graffiti_is_d(self):
        p=self.image();d={'type':'azul_grafiti','world':'rpg','enemy':'none','members':[1],'start':formatted(self.now-600)[:16],'end':formatted(self.now-60)[:16],'proofs':[p]}
        self.db.execute("INSERT INTO mission_reports(guild,session,owner,kind,data,proof,digest,created) VALUES(1,0,1,'mission',?,?,?,?)",(json.dumps(d),p['path'],p['digest'],self.now));self.db.commit()
        r=self.ms.get(self.db.execute('SELECT MAX(id) FROM mission_reports').fetchone()[0]);r=self.ms.submit(r['id'],r['revision'],1);self.check(r);self.ms.decision(r['id'],r['revision'],99,'approve','Correcta')
        self.assertEqual(self.ms.totals(1,1)['D'],1)

if __name__=='__main__':unittest.main()
