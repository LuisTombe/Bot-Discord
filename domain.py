"""World X rules. Birth outcomes are persisted separately from editable data."""
from i18n import format_fragment, t
import json
import re
import sqlite3
from pathlib import Path
from evidence import KINDS, MAX_BYTES, safe_path

SPECIALTIES = ['Ninjutsu','Taijutsu','Genjutsu','Kenjutsu','Subjutsu','Summonjutsu']
EDITABLE = ('draft','correction','rejected')


def load_rules(path):
    r=json.loads(Path(path).read_text(encoding='utf-8'))
    additions=Path(path).with_name('catalogo_v8_adiciones.json')
    if r.get('version')==5 and additions.is_file():
        extra=json.loads(additions.read_text(encoding='utf-8'))
        for group in ('main','secondary'):
            for key,item in extra[group].items():r[group].setdefault(key,item)
    if r.get('version') not in (3,5): raise ValueError(t('Se requiere catalogo.json versión 3 o 5.'))
    if not 1<=r['max_elements']<=4 or not 0<=r['max_secondary']<=4:
        raise ValueError(t('Límites de slots inválidos.'))
    for group in ('clans','main','secondary'):
        for key,item in r[group].items():
            if not 1<=len(item['label'])<=80 or len(key)>100: raise ValueError(t('Catálogo inválido.'))
    return r


def capacities(rules,data,birth=None):
    return rules['max_elements'],1+rules['max_secondary']


def available_clans(rules,birth=None): return rules['clans']


def eligible(rules,data,kind): return rules[kind]


def change(rules,data,field,value,birth=None):
    result=dict(data)
    result[field]=value
    result['catalog_version']=3
    return result


def merge_page_selection(selected, page_keys, values, maximum):
    # Editing page 2 must preserve selections made on page 1.
    result=[x for x in selected if x not in page_keys]+list(values)
    if len(result)!=len(set(result)) or len(result)>maximum:
        raise ValueError(''.join([format_fragment(t('Puedes elegir como máximo '), '', -1), format_fragment(maximum, '', -1), format_fragment(t('. Desmarca primero las opciones que vas a cambiar.'), '', -1)]))
    return result


def validate(rules,data,birth=None):
    for field,limit in [('discord_name',80),('roblox_name',20),('display_name',80),('age',3),('gender',40)]:
        value=data.get(field,'')
        if not isinstance(value,str) or not value.strip() or len(value)>limit:
            raise ValueError(t('Completa correctamente todos los datos escritos.'))
    if not re.fullmatch(r'[A-Za-z0-9_]{3,20}',data['roblox_name']):
        raise ValueError(t('Username Roblox inválido: usa 3–20 letras, números o guion bajo.'))
    if not re.fullmatch(r'[0-9]{1,3}',data['age']) or int(data['age'])<1:
        raise ValueError(t('La edad del personaje debe estar entre 1 y 999.'))
    if rules['version']==3 and data.get('clan') not in rules['clans']: raise ValueError(t('Selecciona un clan o Sin clan / No aplica.'))
    if rules['version']==5:
        if data.get('catalog_version')==5:
            if not data.get('assignment_complete'): raise ValueError(t('Completa Elementos → Principal → Secundarios con /asignacion iniciar.'))
            mains=data.get('mains',[])
            if len(mains)+len(data.get('secondary',[]))>4: raise ValueError(t('Máximo cuatro bloodlines en total.'))
            if not (0 if data.get('assignment_version')==80 else 1)<=len(data.get('elements',[]))<=4: raise ValueError(t('Elementos inválidos.'))
        # Existing v3/v4 cards remain historical; never silently reroll them.
    else:
        if data.get('main') not in rules['main']: raise ValueError(t('Selecciona una bloodline principal del nuevo catálogo.'))
        for field,group,minimum,maximum in [('elements','elements',1,rules['max_elements']),('secondary','secondary',0,rules['max_secondary'])]:
            values=data.get(field,[])
            if not isinstance(values,list) or not all(isinstance(x,str) for x in values): raise ValueError(t('Selección inválida.'))
            if len(values)!=len(set(values)) or not minimum<=len(values)<=maximum or not set(values)<=set(rules[group]):
                raise ValueError(''.join([format_fragment(t('Revisa '), '', -1), format_fragment(field, '', -1), format_fragment(t(': elige de '), '', -1), format_fragment(minimum, '', -1), format_fragment(t(' a '), '', -1), format_fragment(maximum, '', -1), format_fragment(t(' opciones distintas del catálogo.'), '', -1)]))
    if data.get('specialty') not in SPECIALTIES: raise ValueError(t('Selecciona una especialidad.'))


class Store:
    def __init__(self, path):
        self.root=Path(path).parent
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS settings (guild INTEGER PRIMARY KEY, mode TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reward_counters(name TEXT PRIMARY KEY,value INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS cleanup_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, registration INTEGER NOT NULL,
          payload TEXT NOT NULL, error TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS screenshots (
          registration INTEGER NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
          sha256 TEXT NOT NULL, size INTEGER NOT NULL, PRIMARY KEY(registration,kind));
        CREATE TABLE IF NOT EXISTS births (guild INTEGER, user INTEGER, result TEXT NOT NULL, PRIMARY KEY(guild,user));
        CREATE TABLE IF NOT EXISTS registrations (
          id INTEGER PRIMARY KEY, guild INTEGER NOT NULL, user INTEGER NOT NULL,
          revision INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'draft',
          data TEXT NOT NULL DEFAULT '{}', roblox TEXT,
          reason TEXT NOT NULL DEFAULT '', reviewer INTEGER,
          review_message INTEGER, public_message INTEGER, roles_done INTEGER DEFAULT 0,
          delivery_error TEXT NOT NULL DEFAULT '', updated TEXT DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(guild,user));
        CREATE UNIQUE INDEX IF NOT EXISTS unique_roblox ON registrations(guild,roblox)
          WHERE state IN ('pending','approved');
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY, registration INTEGER, actor INTEGER,
          action TEXT, detail TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
        ''')

    def record(self, row):
        if not row:
            return None
        row = dict(row)
        row['data'] = json.loads(row['data'])
        row['screenshots']=[dict(x) for x in self.db.execute('SELECT kind,path,sha256,size FROM screenshots WHERE registration=? ORDER BY kind',(row['id'],))]
        b=self.db.execute('SELECT result FROM births WHERE guild=? AND user=?',(row['guild'],row['user'])).fetchone()
        row['birth']=json.loads(b[0]) if b else None
        return row

    def get(self, ident):
        return self.record(self.db.execute('SELECT * FROM registrations WHERE id=?', (ident,)).fetchone())

    def find(self,guild,user):
        return self.record(self.db.execute('SELECT * FROM registrations WHERE guild=? AND user=?',(guild,user)).fetchone())

    def start(self, guild, user, rules, draw=None):
        # Birth assignments are generated only by the separate assignment workflow.
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO registrations(id,guild,user,data) VALUES ((SELECT MAX(COALESCE((SELECT MAX(id) FROM registrations),0),COALESCE((SELECT value FROM reward_counters WHERE name='registration_id'),0))+1),?,?,?)",
                            (guild,user,json.dumps({'catalog_version':rules['version']})))
            self.db.execute("UPDATE registrations SET state='draft',revision=revision+1,reason='',updated=CURRENT_TIMESTAMP WHERE guild=? AND user=? AND state='deleted'",(guild,user))
        row=self.find(guild,user)
        if rules['version']==5 and not row['data']:
            data={'catalog_version':5}
            table=self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assignments'").fetchone()
            assignment=self.db.execute('SELECT state FROM assignments WHERE guild=? AND user=?',(guild,user)).fetchone() if table else None
            if assignment:
                from assignment_domain import final_data
                state=json.loads(assignment[0])
                if state['stage']==3:data.update(final_data(state))
            with self.db:self.db.execute('UPDATE registrations SET data=? WHERE id=?',(json.dumps(data),row['id']))
        return self.find(guild,user)

    def edit(self, ident, revision, user, data):
        current=self.get(ident)
        from identity_domain import FIELDS
        if current and any(data.get(k)!=current['data'].get(k) for k in FIELDS):
            raise ValueError(t('El nombre y apellido solo se modifican con /identidad elegir o /identidad corregir.'))
        if current and current['data'].get('catalog_version')==5:
            from assignment_domain import PROTECTED
            if any(data.get(k)!=current['data'].get(k) for k in (*PROTECTED,'catalog_version')):
                raise ValueError(t('Los resultados solo los puede escribir el sistema de asignación.'))
        with self.db:
            cursor = self.db.execute("""UPDATE registrations SET data=?, revision=revision+1,
                updated=CURRENT_TIMESTAMP WHERE id=? AND revision=? AND user=?
                AND state IN ('draft','correction','rejected')""", (json.dumps(data), ident, revision, user))
            if cursor.rowcount != 1:
                raise ValueError(t('Ficha modificada o bloqueada. Abre /mi_ficha para actualizarla.'))
        return self.get(ident)

    def submit(self, ident, revision, user, rules):
        row = self.get(ident)
        if not row or row['user'] != user:
            raise ValueError(t('Ficha no encontrada o ajena.'))
        if hasattr(self,'identity_service'):self.identity_service.require(row)
        validate(rules, row['data'],row['birth'])
        if row['data'].get('catalog_version')==5:
            from assignment_domain import final_data, PROTECTED
            a=self.db.execute('SELECT state FROM assignments WHERE guild=? AND user=?',(row['guild'],row['user'])).fetchone()
            state=json.loads(a[0]) if a else None
            if not state or state['stage']!=3 or state['frozen']:
                raise ValueError(t('Completa primero tu asignación.'))
            result=final_data(state)
            if any(row['data'].get(k)!=result.get(k) for k in PROTECTED):
                raise ValueError(t('Los resultados no coinciden con la asignación guardada. Avisa al dueño.'))
        self.require_screenshots(row)
        try:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                if row['data'].get('birth_village'):
                    from wx_rewards.villages import reserve
                    reserve(self.db,row['guild'],user,row['data']['birth_village'])
                cursor = self.db.execute("""UPDATE registrations SET state='pending', roblox=?,
                    revision=revision+1, review_message=NULL, reason='', reviewer=NULL, roles_done=0,
                    updated=CURRENT_TIMESTAMP WHERE id=? AND revision=? AND user=?
                    AND state IN ('draft','correction','rejected')""",
                    (row['data']['roblox_name'].lower(), ident, revision, user))
                if cursor.rowcount != 1:
                    raise ValueError(t('Ficha ya enviada o modificada. Abre /mi_ficha.'))
                self.db.execute('INSERT INTO audit(registration,actor,action,detail) VALUES (?,?,?,?)',
                                (ident,user,'submit',json.dumps(row['data'])))
        except sqlite3.IntegrityError as exc:
            raise ValueError(t('Ese username Roblox ya tiene una ficha pendiente o aprobada.')) from exc
        return self.get(ident)

    def decide(self, ident, revision, actor, state, reason):
        if state not in ('approved','correction','rejected'):
            raise ValueError(t('Decisión desconocida.'))
        if state=='approved':
            if hasattr(self,'identity_service'):self.identity_service.require(self.get(ident))
            self.require_screenshots(self.get(ident))
        with self.db:
            cursor = self.db.execute("""UPDATE registrations SET state=?, reason=?, reviewer=?,
                revision=revision+1, updated=CURRENT_TIMESTAMP WHERE id=? AND revision=? AND state='pending'""",
                (state,reason,actor,ident,revision))
            if cursor.rowcount != 1:
                raise ValueError(t('Esta revisión ya fue resuelta o reemplazada.'))
            self.db.execute('INSERT INTO audit(registration,actor,action,detail) VALUES (?,?,?,?)',
                            (ident,actor,state,reason))
        return self.get(ident)

    def require_screenshots(self,row):
        entries=row.get('screenshots',[]) if row else []
        if len(entries)!=3 or {e['kind'] for e in entries}!=set(KINDS):
            raise ValueError(t('Faltan las 3 capturas obligatorias. Abre Capturas e instrucciones antes de enviar. Si eres revisor, solicita una corrección.'))
        if any(not safe_path(self.root,e['path']).is_file() for e in entries):
            raise ValueError(t('Falta un archivo de captura guardado. Vuelve a subir las tres imágenes.'))

    def save_screenshots(self,ident,revision,user,entries):
        if len(entries)!=3 or {e['kind'] for e in entries}!=set(KINDS) or len({e['sha256'] for e in entries})!=3:
            raise ValueError(t('Se requieren tres imágenes diferentes.'))
        for e in entries:
            path=safe_path(self.root,e['path'])
            if not path.is_file() or not 0<e['size']<=MAX_BYTES or path.stat().st_size!=e['size']:
                raise ValueError(t('No se pudo guardar una de las capturas.'))
        with self.db:
            cursor=self.db.execute("""UPDATE registrations SET revision=revision+1, updated=CURRENT_TIMESTAMP
              WHERE id=? AND user=? AND revision=? AND state IN ('draft','correction','rejected')""",(ident,user,revision))
            if cursor.rowcount!=1:
                raise ValueError(t('La ficha cambió o ya está enviada. Abre /mi_ficha para continuar.'))
            self.db.execute('DELETE FROM screenshots WHERE registration=?',(ident,))
            for e in entries:
                self.db.execute('INSERT INTO screenshots VALUES (?,?,?,?,?)',
                                (ident,e['kind'],e['path'],e['sha256'],e['size']))
            self.db.execute('INSERT INTO audit(registration,actor,action,detail) VALUES (?,?,?,?)',
                            (ident,user,'screenshots',json.dumps(entries)))
        return self.get(ident)

    def mark(self, ident, **values):
        assert set(values) <= {'review_message','public_message','roles_done','delivery_error'}
        with self.db:
            self.db.execute('UPDATE registrations SET '+','.join(k+'=?' for k in values)+' WHERE id=?',
                            (*values.values(),ident))

    def all(self):
        return [self.record(row) for row in self.db.execute('SELECT * FROM registrations')]

    def mode(self,guild,default='pruebas'):
        row=self.db.execute('SELECT mode FROM settings WHERE guild=?',(guild,)).fetchone()
        return row[0] if row else default

    def set_mode(self,guild,actor,mode):
        if mode not in ('abierto','cerrado','pruebas'): raise ValueError(t('Modo inválido.'))
        with self.db:
            self.db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(guild) DO UPDATE SET mode=excluded.mode',(guild,mode))
            self.db.execute('INSERT INTO audit(registration,actor,action,detail) VALUES(NULL,?,?,?)',(actor,'mode',json.dumps({'guild':guild,'mode':mode})))

    def admin_action(self,ident,revision,actor,action,reason,payload):
        if action not in ('reopen','close','delete'): raise ValueError(t('Acción inválida.'))
        if not reason.strip() or len(reason)>180: raise ValueError(t('Indica un motivo de 1 a 180 caracteres.'))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.get(ident)
            if not row or row['revision']!=revision or row['state']=='deleted':
                raise ValueError(t('La ficha cambió. Vuelve a ejecutar el comando.'))
            target={'reopen':'correction','close':'closed','delete':'deleted'}[action]
            self.db.execute('INSERT INTO audit(registration,actor,action,detail) VALUES(?,?,?,?)',
                (ident,actor,action,json.dumps({'reason':reason,'previous':row},ensure_ascii=False)))
            self.db.execute("UPDATE registrations SET state=?,revision=revision+1,reason=?,reviewer=?,review_message=NULL,public_message=NULL,roles_done=0,delivery_error='',roblox=NULL,updated=CURRENT_TIMESTAMP WHERE id=?",
                            (target,reason,actor,ident))
            if action=='delete':
                self.db.execute("UPDATE registrations SET data='{}' WHERE id=?",(ident,))
                self.db.execute('DELETE FROM screenshots WHERE registration=?',(ident,))
                if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='wx_village_slots'").fetchone():
                    self.db.execute('DELETE FROM wx_village_slots WHERE guild=? AND user=?',(row['guild'],row['user']))
            if payload.get('messages') or payload.get('roles'):
                self.db.execute('INSERT INTO cleanup_jobs(registration,payload) VALUES (?,?)',(ident,json.dumps(payload)))
        return self.get(ident)

    def cleanup(self,ident=None):
        query='SELECT * FROM cleanup_jobs';args=()
        if ident is not None:
            query+=" WHERE registration=? OR json_extract(payload,'$.user')=(SELECT user FROM registrations WHERE id=?)";args=(ident,ident)
        return [dict(x) for x in self.db.execute(query,args)]

    def cleanup_done(self,job):
        with self.db: self.db.execute('DELETE FROM cleanup_jobs WHERE id=?',(job,))

    def cleanup_error(self,job,error):
        with self.db: self.db.execute('UPDATE cleanup_jobs SET error=? WHERE id=?',(error[:180],job))

    def history(self,ident):
        return [dict(x) for x in self.db.execute('SELECT actor,action,detail,created FROM audit WHERE registration=? ORDER BY id DESC LIMIT 15',(ident,))]
