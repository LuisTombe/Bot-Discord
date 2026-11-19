"""Versioned, transactional balance administration. SQLite owns edits after first save."""
from i18n import format_fragment, t
import copy
import json
import math
from pathlib import Path
from assignment_domain import validate_config, fingerprint

NATURES=('earth','water','fire','wind','lightning')

def number(text, maximum=10000):
    try: value=float(str(text).strip().replace(',','.'))
    except ValueError: raise ValueError(t('Escribe un número, por ejemplo 0,05 o 2.5.'))
    if not math.isfinite(value) or not 0<=value<=maximum:
        raise ValueError(''.join([format_fragment(t('El valor debe estar entre 0 y '), '', -1), format_fragment(maximum, '', -1), '.']))
    return value

def validate_editor(c):
    validate_config(c)
    from birth_v8 import is_v8
    if is_v8(c):return
    b=c['balanced']
    for group in ('main','secondary'):
        for item in c[group].values():
            if sum(item.get('requires',{}).values())>4:raise ValueError(t('La combinación requiere más de cuatro elementos.'))
    for item in c['secondary'].values():
        parent=item.get('main_required')
        if parent:
            if parent in b['taijutsu_mains']:raise ValueError(t('Lee/Gai no pueden tener secundarios. Elige otro principal requerido.'))
            combined=sum(max(item.get('requires',{}).get(n,0),c['main'][parent].get('requires',{}).get(n,0)) for n in NATURES)
            if combined>4:raise ValueError(t('El secundario y su principal obligatorio requieren juntos más de cuatro elementos.'))
    for key,item in c['elements'].items():
        if sum(item.get('requires',{}).values())>3:raise ValueError(t('Un elemento raro solo puede requerir hasta tres elementos previos.'))
    # Conservative bound ensures no slot distribution exceeds its configured budget,
    # including arbitrary families and mutually compatible candidates.
    fixed=sum(b['secondary_fixed'].values())
    if fixed>min(b['secondary_percent_with_main']+b['secondary_percent_without_main'])+1e-9:
        raise ValueError(t('La suma de porcentajes fijos secundarios supera el presupuesto de algún slot. Reduce los fijos, usa pesos o aumenta ese presupuesto en Ajustes generales.'))
    blood=c['secondary'].get('blood',{})
    if 'blood' in b['secondary_fixed']:
        if any(v>b['secondary_fixed']['blood'] for v in blood.get('percent_with_main',{}).values()):
            raise ValueError(t('Blood con Dokei no puede superar el porcentaje fijo general de Blood.'))


def item_value(c,group,key):
    if group=='elements':return 'weight',c[group][key]['weight']
    b=c['balanced']
    if key in b[group+'_fixed']:return 'fixed',b[group+'_fixed'][key]
    return 'weight',b[group+'_weights'][key]


def set_value(c,group,key,mode,value,dokei=None):
    result=copy.deepcopy(c)
    if group=='elements':
        result[group][key]['weight']=number(value,100)
    else:
        value=number(value,100 if mode=='fixed' else 10000)
        b=result['balanced'];b[group+'_fixed'].pop(key,None);b[group+'_weights'].pop(key,None)
        b[group+('_fixed' if mode=='fixed' else '_weights')][key]=value
        if group=='secondary' and key=='blood' and dokei is not None:
            result[group][key]['percent_with_main']['dokei']=number(dokei,100)
    return result


def set_requirements(c,group,key,values):
    result=copy.deepcopy(c);req={}
    for nature,text in zip(NATURES,values):
        amount=number(text,4)
        if int(amount)!=amount:raise ValueError(t('La cantidad de elementos debe ser un número entero de 0 a 4.'))
        if amount:req[nature]=int(amount)
    result[group][key]['requires']=req
    return result


class BalanceControl:
    def __init__(self,system):
        self.system=system;self.db=system.store.db;self.guild=system.ctx['GUILD']
        with self.db:
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS balance_active(guild INTEGER PRIMARY KEY, config TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS balance_changes(
              id INTEGER PRIMARY KEY, guild INTEGER NOT NULL, actor INTEGER NOT NULL,
              previous TEXT NOT NULL, current TEXT NOT NULL, reason TEXT NOT NULL,
              created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS balance_migrations(
              guild INTEGER NOT NULL, release TEXT NOT NULL, rules_hash TEXT NOT NULL,
              created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(guild,release));
            ''')
        row=self.db.execute('SELECT config FROM balance_active WHERE guild=?',(self.guild,)).fetchone()
        if row:
            current=json.loads(row[0]);validate_config(current);self.publish(current)
        base=system.ctx.get('BASE')
        if base is not None:
            release_file=Path(base)/'balance_G_v59.json'
            if release_file.is_file():self.apply_release(release_file)
            release_file=Path(base)/'balance_v71.json'
            if release_file.is_file():self.apply_release(release_file,'worldx-v7.1-origins')
            if (Path(base)/'activar_v72.json').is_file():self.apply_free_spins()
            release_file=Path(base)/'balance_v8.json'
            if release_file.is_file():self.apply_release(release_file,'worldx-v8-births')
            if (Path(base)/'activar_hardcore_a.json').is_file():self.apply_hardcore_a()

    def apply_hardcore_a(self):
        from hardcore_a import upgrade
        release='worldx-v8.2-hardcore-A'
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute('SELECT 1 FROM balance_migrations WHERE guild=? AND release=?',(self.guild,release)).fetchone():return False
            row=self.db.execute('SELECT config FROM balance_active WHERE guild=?',(self.guild,)).fetchone()
            previous=json.loads(row[0]) if row else self.system.config
            candidate=upgrade(previous);validate_editor(candidate);payload=json.dumps(candidate,ensure_ascii=False)
            self.db.execute('INSERT INTO balance_changes(guild,actor,previous,current,reason) VALUES(?,?,?,?,?)',(self.guild,0,json.dumps(previous),payload,'Activar propuesta A: 80/20 y 60% compatible'))
            self.db.execute('INSERT INTO balance_active VALUES(?,?) ON CONFLICT(guild) DO UPDATE SET config=excluded.config',(self.guild,payload))
            self.db.execute('INSERT INTO balance_migrations(guild,release,rules_hash) VALUES(?,?,?)',(self.guild,release,fingerprint(candidate)))
        self.publish(candidate)
        return True

    def apply_release(self,path,release='worldx-v5.9-candidate-G'):
        if self.db.execute('SELECT 1 FROM balance_migrations WHERE guild=? AND release=?',(self.guild,release)).fetchone():
            return False
        candidate=json.loads(Path(path).read_text(encoding='utf-8'));validate_editor(candidate)
        payload=json.dumps(candidate,ensure_ascii=False)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            # Recheck under the write lock; startup must never overwrite later edits.
            if self.db.execute('SELECT 1 FROM balance_migrations WHERE guild=? AND release=?',(self.guild,release)).fetchone():
                return False
            row=self.db.execute('SELECT config FROM balance_active WHERE guild=?',(self.guild,)).fetchone()
            previous=json.loads(row[0]) if row else self.system.config
            self.db.execute('INSERT INTO balance_changes(guild,actor,previous,current,reason) VALUES(?,?,?,?,?)',
                (self.guild,0,json.dumps(previous,ensure_ascii=False),payload,
                 t('Sistema: aplicar ')+release+t(' una sola vez')))
            self.db.execute('INSERT INTO balance_active(guild,config) VALUES(?,?) ON CONFLICT(guild) DO UPDATE SET config=excluded.config',
                (self.guild,payload))
            self.db.execute('INSERT INTO balance_migrations(guild,release,rules_hash) VALUES(?,?,?)',
                (self.guild,release,fingerprint(candidate)))
        self.publish(candidate)
        return True

    def apply_free_spins(self):
        from birth_v72 import upgrade_config
        release='worldx-v7.2-free-spins'
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute('SELECT 1 FROM balance_migrations WHERE guild=? AND release=?',(self.guild,release)).fetchone():return False
            row=self.db.execute('SELECT config FROM balance_active WHERE guild=?',(self.guild,)).fetchone()
            previous=json.loads(row[0]) if row else self.system.config
            if previous.get('birth_policy',{}).get('version')!=1:return False
            candidate=upgrade_config(previous);validate_editor(candidate)
            payload=json.dumps(candidate,ensure_ascii=False)
            self.db.execute('INSERT INTO balance_changes(guild,actor,previous,current,reason) VALUES(?,?,?,?,?)',(self.guild,0,json.dumps(previous),payload,t('v7.2: tres giros por sección sin tipo de nacimiento fijo')))
            self.db.execute('INSERT INTO balance_active(guild,config) VALUES(?,?) ON CONFLICT(guild) DO UPDATE SET config=excluded.config',(self.guild,payload))
            self.db.execute('INSERT INTO balance_migrations(guild,release,rules_hash) VALUES(?,?,?)',(self.guild,release,fingerprint(candidate)))
        self.publish(candidate)
        return True

    def publish(self,c):
        from wx_rewards.villages import normalize
        normalize(c)
        with self.db:
            self.db.execute('INSERT INTO balance_active VALUES(?,?) ON CONFLICT(guild) DO UPDATE SET config=excluded.config',(self.guild,json.dumps(c,ensure_ascii=False)))
        self.system.config=c;self.system.store.config=c

    def commit(self,expected_hash,config,actor,reason):
        candidate=copy.deepcopy(config)
        current=self.system.config
        if fingerprint(current)!=expected_hash:raise ValueError(t('El balance cambió mientras editabas. Abre el panel otra vez y revisa el valor nuevo.'))
        validate_editor(candidate)
        if candidate==current:raise ValueError(t('No hay cambios que guardar.'))
        candidate['balance_revision']=('8.0' if candidate.get('birth_policy',{}).get('version')==3 else '7.2' if candidate.get('birth_policy',{}).get('version')==2 else '7.1' if candidate.get('birth_policy') else '5.9')+t(' · personalizado')
        payload=json.dumps(candidate,ensure_ascii=False)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            persisted=self.db.execute('SELECT config FROM balance_active WHERE guild=?',(self.guild,)).fetchone()
            if persisted and fingerprint(json.loads(persisted[0]))!=expected_hash:
                raise ValueError(t('Hay otro cambio guardado. Reinicia esta instancia para cargarlo.'))
            self.db.execute('INSERT INTO balance_changes(guild,actor,previous,current,reason) VALUES(?,?,?,?,?)',
                (self.guild,actor,json.dumps(current,ensure_ascii=False),payload,reason))
            self.db.execute('INSERT INTO balance_active(guild,config) VALUES(?,?) ON CONFLICT(guild) DO UPDATE SET config=excluded.config',
                (self.guild,payload))
        self.publish(candidate)
        return candidate

    def history(self):
        return [dict(r) for r in self.db.execute('SELECT id,actor,reason,created FROM balance_changes WHERE guild=? ORDER BY id DESC LIMIT 10',(self.guild,))]

    def previous(self):
        row=self.db.execute('SELECT previous FROM balance_changes WHERE guild=? ORDER BY id DESC LIMIT 1',(self.guild,)).fetchone()
        if not row:raise ValueError(t('Todavía no hay cambios guardados desde el panel.'))
        return json.loads(row[0])
