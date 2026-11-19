"""Políticas de evidencia. La revisión visual es humana, nunca un OCR supuesto."""
import hashlib
import io
from PIL import Image
import json
import re
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from evidence import MAX_BYTES, safe_path
from missions_domain import timestamp
from payment_evidence import validate_image

COMMON = ['Pantalla completa, sin recortes ni datos superpuestos',
          'Fecha y hora reales del dispositivo visibles y legibles',
          'Panel con el nombre/objetivo de misión visible',
          'Jugador identificable y coherente con la ficha aprobada']

def visible_timestamp(text, offset):
    """Convierte la hora que el moderador lee en la imagen, no la hora del archivo."""
    if not re.fullmatch(r'[+-](?:0\d|1[0-4]):[0-5]\d',offset):
        raise ValueError('Zona horaria como -05:00 (Colombia), +01:00, etc.')
    hours,minutes=map(int,offset[1:].split(':'))
    if hours==14 and minutes:raise ValueError('Desfase máximo UTC±14:00.')
    delta=(hours*60+minutes)*(1 if offset[0]=='+' else -1)
    try:return int(datetime.strptime(text.strip(),'%d/%m/%Y %H:%M').replace(tzinfo=timezone(timedelta(minutes=delta))).timestamp())
    except ValueError as e:raise ValueError('Fecha visible: DD/MM/AAAA HH:MM, formato de 24 horas.') from e

def has_shinobi(role_ids,required):
    if not required:raise ValueError('Falta configurar SHINOBI_ROLE_ID con el ID del rol Shinobi.')
    if required not in role_ids:raise ValueError('Solo los usuarios con el rol Shinobi pueden reportar misiones.')

def validate_screenshot(blob):
    ext,digest=validate_image(blob)
    with Image.open(io.BytesIO(blob)) as image:
        if getattr(image,'is_animated',False):raise ValueError('Adjunta una captura estática, no una animación.')
    return ext,digest

def validate_config(config):
    if config.get('version')!=1:raise ValueError('Versión de políticas de evidencia no soportada.')
    for key in ('max_evidence_disk_mb','min_free_disk_mb','clock_tolerance_minutes'):
        if type(config.get(key)) is not int or config[key]<0:raise ValueError('Límite de evidencia inválido: '+key)
    for rule in config['missions'].values():
        if rule['base_rank'] not in tuple('DCBAS') or not 1<=rule['min_images']<=4 or not rule['evidence']:
            raise ValueError('Regla de misión inválida.')
        if not rule['worlds'] or set(rule['worlds'])-{'rpg','open_world','world_x'}:raise ValueError('Mundo inválido.')

class EvidencePolicy:
    def __init__(self,store,config):
        validate_config(config)
        self.s=store;self.db=store.db;self.config=config
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS mission_evidence_checks(
          report INTEGER NOT NULL,revision INTEGER NOT NULL,actor INTEGER NOT NULL,
          image_index INTEGER NOT NULL,digest TEXT NOT NULL,observed INTEGER NOT NULL,
          written_time TEXT NOT NULL,utc_offset TEXT NOT NULL,checklist TEXT NOT NULL,
          notes TEXT NOT NULL,created INTEGER NOT NULL,
          PRIMARY KEY(report,revision,actor,image_index));
        CREATE TABLE IF NOT EXISTS mission_proof_index(report INTEGER NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(report,digest));
        CREATE INDEX IF NOT EXISTS mission_proof_digest ON mission_proof_index(digest,report);
        CREATE INDEX IF NOT EXISTS mission_reports_delivery ON mission_reports(guild,state,sent_revision,revision);
        CREATE INDEX IF NOT EXISTS mission_reports_digest ON mission_reports(guild,digest);
        ''')
        # Índice derivado: reconstrucción por cursor, sin cargar todas las capturas/reportes.
        with self.db:
            for row in self.db.execute('SELECT id,data,proof,digest FROM mission_reports'):
                r=dict(row);r['data']=json.loads(r['data']);self.index(r)
    def proofs(self,row):
        return row['data'].get('proofs') or ([{'path':row['proof'],'digest':row['digest']}] if row['proof'] else [])
    def index(self,row):
        self.db.execute('DELETE FROM mission_proof_index WHERE report=?',(row['id'],))
        self.db.executemany('INSERT OR IGNORE INTO mission_proof_index VALUES(?,?)',[(row['id'],p['digest']) for p in self.proofs(row)])
    def conflict(self,row):
        for p in self.proofs(row):
            r=self.db.execute("SELECT r.id FROM mission_proof_index p JOIN mission_reports r ON r.id=p.report WHERE r.guild=? AND r.kind='mission' AND r.id!=? AND r.state NOT IN ('deleted','rejected') AND p.digest=? LIMIT 1",(row['guild'],row['id'],p['digest'])).fetchone()
            if r:return r[0]
        return None
    def rule(self,row):
        if row['kind']=='authorization':return {'evidence':'Menú M con nivel y CHI/NIN/TAI/HP legibles; TAB con aldea e identidad.','min_images':1}
        rule=self.config['missions'].get(row['data'].get('type'))
        if rule:return rule
        return {'evidence':'Objetivo y resultado del encargo personalizado visibles. Verificar sus condiciones con el líder.','min_images':1,'worlds':['open_world','world_x']}
    def checklist(self,row):
        # La autorización se pide ANTES de la misión: no exige un panel de objetivo activo.
        common=COMMON if row['kind']=='mission' else [COMMON[0],COMMON[1],COMMON[3]]
        checks=common+[self.rule(row)['evidence']]
        if row['kind']=='mission':checks+=['El enemigo y su peligrosidad/rango coinciden con la clasificación del reporte']
        if len(row['data'].get('members',[]))>1:checks+=['Squad y participantes reconocibles y coincidentes con el reporte']
        return checks
    def validate_files(self,row):
        proofs=self.proofs(row)
        minimum=self.rule(row)['min_images']
        maximum=10 if row['data'].get('batch_flow') else 4
        if not minimum<=len(proofs)<=maximum:raise ValueError(f'Esta misión requiere entre {minimum} y {maximum} capturas diferentes.')
        seen=set()
        for p in proofs:
            path=safe_path(self.s.root,p['path'])
            if not path.is_file() or not 0<path.stat().st_size<=MAX_BYTES:raise ValueError('Falta una imagen válida del reporte.')
            actual=hashlib.sha256(path.read_bytes()).hexdigest()
            if actual!=p['digest']:raise ValueError('Una captura cambió después de adjuntarse. Vuelve a subirla.')
            if actual in seen:raise ValueError('No repitas la misma captura.')
            seen.add(actual)
        if row['kind']=='mission':
            world=row['data'].get('world','rpg' if row['session']==0 else 'open_world')
            if world not in self.rule(row).get('worlds',['open_world','world_x']):raise ValueError('El tipo de misión no corresponde al mundo elegido.')
            if not row['data'].get('simple_flow') and (row['session']==0)!=(world=='rpg'):raise ValueError('El modo RPG y la sesión de Open World no coinciden.')
        return proofs
    def check_upload_budget(self,incoming):
        root=self.s.root/'capturas'/'reportes';root.mkdir(parents=True,exist_ok=True)
        max_mb=self.config.get('max_evidence_disk_mb',2048)
        if max_mb<=160:
            used=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
            if used+incoming>max_mb*1024**2:raise ValueError('Archivo de evidencias lleno. Administración debe respaldar y ampliar el límite antes de continuar.')
        elif incoming>max_mb*1024**2:
            raise ValueError('Archivo de evidencias lleno. Administración debe respaldar y ampliar el límite antes de continuar.')
        min_free=min(self.config.get('min_free_disk_mb',4),4)
        try:
            if shutil.disk_usage(root).free-incoming<min_free*1024**2:raise ValueError('Espacio insuficiente para guardar pruebas de forma segura.')
        except OSError:
            pass
    def attest(self,ident,revision,actor,image_index,observed_text,offset,notes,confirmation):
        if confirmation.strip().upper()!='CONFIRMO':raise ValueError('Escribe CONFIRMO solo si verificaste todos los puntos de esta imagen.')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.s.get(ident)
            if not row or row['state']!='pending' or row['revision']!=revision:raise ValueError('El reporte cambió. Abre su revisión actual.')
            if actor in {row['owner'],*row['data'].get('members',[])}:raise ValueError('Otro moderador debe revisar tu reporte.')
            proofs=self.validate_files(row)
            if not 0<=image_index<len(proofs):raise ValueError('Captura inexistente.')
            observed=visible_timestamp(observed_text,offset)
            tolerance=self.config['clock_tolerance_minutes']*60
            if observed>self.s.now()+tolerance:raise ValueError('La fecha visible está en el futuro; revisa el desfase horario.')
            if row['kind']=='mission':
                lo=timestamp(row['data'].get('start',''));hi=timestamp(row['data'].get('end',''))
                if not lo-tolerance<=observed<=hi+60+tolerance:raise ValueError('La hora de la imagen no coincide con el intervalo reportado (convertido a Colombia).')
            if not notes.strip():raise ValueError('Describe lo que observaste en el objetivo y el resultado.')
            values=(ident,revision,actor,image_index,proofs[image_index]['digest'],observed,observed_text,offset,json.dumps(self.checklist(row),ensure_ascii=False),notes[:500],self.s.now())
            self.db.execute('INSERT OR REPLACE INTO mission_evidence_checks VALUES(?,?,?,?,?,?,?,?,?,?,?)',values)
            self.s.log(row['guild'],ident,actor,'evidence_checked',{'revision':revision,'image':image_index+1,'digest':proofs[image_index]['digest'],'visible_time':observed_text,'offset':offset,'observed':observed,'checklist':self.checklist(row),'notes':notes[:500]})
            self.db.execute('UPDATE mission_reports SET sent_revision=-1 WHERE id=?',(ident,))
    def require_checks(self,row,actor):
        proofs=self.validate_files(row)
        if actor in {row['owner'],*row['data'].get('members',[])}:raise ValueError('No puedes aprobar una misión en la que participaste, tampoco en modo pruebas.')
        rows=self.db.execute('SELECT image_index,digest,checklist FROM mission_evidence_checks WHERE report=? AND revision=? AND actor=?',(row['id'],row['revision'],actor))
        verified={r['image_index']:r for r in rows}
        expected=self.checklist(row)
        if any(n not in verified or verified[n]['digest']!=p['digest'] or json.loads(verified[n]['checklist'])!=expected for n,p in enumerate(proofs)):
            raise ValueError('Antes de aprobar, verifica cada captura con el botón Verificar pruebas o /revision_mision, desde el canal privado.')
    def install(self):
        original_submit=self.s.submit;original_decision=self.s.decision;original_edit=self.s.edit
        self.s.evidence_policy=self;self.s.proof_conflict=self.conflict
        def edit(*args,**kwargs):
            row=original_edit(*args,**kwargs)
            with self.db:self.index(row)
            return row
        def submit(ident,revision,user):
            row=self.s.get(ident)
            if not row:raise ValueError('Reporte inexistente.')
            self.validate_files(row)
            with self.db:self.index(row)
            return original_submit(ident,revision,user)
        def decision(ident,revision,actor,action,reason,demo=False):
            row=self.s.get(ident)
            if not row:raise ValueError('Reporte inexistente.')
            if action=='approve':self.require_checks(row,actor)
            return original_decision(ident,revision,actor,action,reason,False)
        self.s.edit=edit;self.s.submit=submit;self.s.decision=decision
