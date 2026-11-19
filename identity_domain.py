"""Persistent cosmetic identities. Never touches birth rules or spin counters."""
from i18n import format_fragment, t
import hashlib
import json
import secrets
import unicodedata
from pathlib import Path

FIELDS=('given_name','surname','character_name','identity_source','identity_signature','identity_confirmed')
ALIASES={'shado':'shadow','rengoku':'rinnegan','apollo-sand':'iron-sand','typhon':'typhoon','aizens-mud':'aidens-son-mud'}

def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD',value).casefold() if not unicodedata.combining(c))

def lineage(data):
    mains=data.get('mains') or ([data['main']] if data.get('main') not in (None,'none','') else [])
    if data.get('assignment_version')==80:return mains,list(data.get('secondary',[]))
    return [ALIASES.get(k,k) for k in mains], [ALIASES.get(k,k) for k in data.get('secondary',[])]

def signature(data):
    return hashlib.sha256(json.dumps(lineage(data),ensure_ascii=True).encode()).hexdigest()

def clear_identity(data):
    for key in FIELDS:data.pop(key,None)

class IdentityService:
    def __init__(self,store,path):
        self.store=store;self.db=store.db
        self.defaults=json.loads(Path(path).read_text(encoding='utf-8'))
        additions=Path(path).with_name('identidad_v8_adiciones.json')
        if additions.is_file():
            extra=json.loads(additions.read_text(encoding='utf-8'))
            for group in ('main','secondary'):
                for key,value in extra[group].items():self.defaults[group].setdefault(key,value)
        self.db.execute('CREATE TABLE IF NOT EXISTS identity_catalog (guild INTEGER PRIMARY KEY, config TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS identity_public_updates (registration INTEGER PRIMARY KEY, revision INTEGER NOT NULL)')
        store.identity_service=self

    def config(self,guild):
        row=self.db.execute('SELECT config FROM identity_catalog WHERE guild=?',(guild,)).fetchone()
        config=json.loads(row[0]) if row else json.loads(json.dumps(self.defaults))
        for group in ('main','secondary'):
            for key,value in self.defaults[group].items():config[group].setdefault(key,value)
        return config

    def catalog_edit(self,guild,actor,action,mutate):
        with self.db:
            config=self.config(guild);mutate(config)
            self.db.execute('INSERT INTO identity_catalog VALUES (?,?) ON CONFLICT(guild) DO UPDATE SET config=excluded.config',(guild,json.dumps(config)))
            self.db.execute('INSERT INTO audit(registration,actor,action,detail) VALUES (NULL,?,?,?)',(actor,action,json.dumps({'guild':guild,'config':config})))

    def check(self,row):
        if not row or row['state'] not in ('draft','correction','rejected','approved'):
            raise ValueError(t('La ficha está bloqueada o en revisión. Reábrela para completar la identidad.'))
        d=row['data']
        if d.get('catalog_version')==5 and not d.get('assignment_complete'):
            raise ValueError(t('Confirma primero Elementos → Principal → Secundarios.'))

    def valid(self,data):
        return bool(data.get('identity_confirmed') and data.get('character_name') and data.get('identity_signature')==signature(data))

    def require(self,row):
        if not self.valid(row['data']):raise ValueError(t('Elige y confirma tu nombre con /identidad elegir antes de enviar la ficha.'))

    def prepare(self,guild,user):
        row=self.store.find(guild,user);self.check(row);d=dict(row['data'])
        sig=signature(d)
        if d.get('identity_signature')==sig and d.get('surname'):return row
        c=self.config(guild);mains,secondary=lineage(d)
        if mains:kind,key='main',mains[0]
        elif secondary:kind,key='secondary',secondary[0]
        else:kind,key='civil',''
        surname=secrets.choice(c['civil']) if kind=='civil' else c[kind].get(key)
        if not surname:raise ValueError(''.join([format_fragment(t('Falta configurar apellido para '), '', -1), format_fragment(kind, '', -1), ': ', format_fragment(key, '', -1), format_fragment(t('. Avisa al administrador.'), '', -1)]))
        clear_identity(d)
        d.update(surname=surname,identity_source=f'{kind}:{key}' if key else 'civil',identity_signature=sig,identity_confirmed=False)
        with self.db:
            cur=self.db.execute('UPDATE registrations SET data=?,revision=revision+1 WHERE id=? AND revision=?',(json.dumps(d),row['id'],row['revision']))
            if cur.rowcount!=1:raise ValueError(t('Ficha modificada. Abre /identidad elegir.'))
        return self.store.get(row['id'])

    def choose(self,guild,user,revision,name_id,actor=None,reason=None):
        row=self.store.find(guild,user);self.check(row)
        if row['revision']!=revision:raise ValueError(t('Panel desactualizado. Abre /identidad elegir.'))
        if actor is not None and not (reason and 3<=len(reason.strip())<=180):raise ValueError(t('Indica un motivo de 3–180 caracteres.'))
        d=dict(row['data'])
        if self.valid(d) and actor is None:raise ValueError(t('Identidad ya confirmada. Solicita una corrección al staff.'))
        if d.get('identity_signature')!=signature(d) or not d.get('surname'):raise ValueError(t('Prepara primero tu identidad con /identidad elegir.'))
        entry=self.config(guild)['names'].get(normalized(name_id))
        if not entry or not entry['active']:raise ValueError(t('Nombre inexistente o desactivado.'))
        d.update(given_name=entry['name'],character_name=entry['name']+' '+d['surname'],identity_confirmed=True)
        with self.db:
            cur=self.db.execute('UPDATE registrations SET data=?,revision=revision+1,updated=CURRENT_TIMESTAMP WHERE id=? AND revision=?',(json.dumps(d),row['id'],revision))
            if cur.rowcount!=1:raise ValueError(t('La ficha cambió. Repite el comando.'))
            self.db.execute('INSERT INTO identity_public_updates VALUES (?,?) ON CONFLICT(registration) DO UPDATE SET revision=excluded.revision',(row['id'],revision+1))
            self.db.execute('INSERT INTO audit(registration,actor,action,detail) VALUES (?,?,?,?)',(row['id'],actor or user,'identity_correct' if actor else 'identity_confirm',json.dumps({'before':row['data'].get('character_name'),'after':d['character_name'],'reason':reason})))
        return self.store.get(row['id'])

    def names(self,guild,category='',initial='',query=''):
        return sorted([(k,v) for k,v in self.config(guild)['names'].items() if v['active'] and (not category or category in v['categories']) and (not initial or k.startswith(normalized(initial))) and normalized(query) in k],key=lambda p:p[0])
