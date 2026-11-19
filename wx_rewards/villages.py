"""Cupos compartidos por ficha y nacimiento, dentro del bloqueo SQLite existente."""
import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / 'config/rewards.json'

def settings():
    return json.loads(CONFIG.read_text(encoding='utf-8'))

def canonical(key):
    return 'iron' if key == 'vinland' else key

def normalize(config):
    if not config.get('birth_policy'):
        return config
    config['birth_policy']['villages'] = settings()['villages'].copy()
    for group in ('main','secondary','elements'):
        for item in config.get(group,{}).values():
            if 'villages' in item:
                item['villages'] = list(dict.fromkeys(canonical(v) for v in item['villages']))
            weights = item.get('village_weights',{})
            if 'vinland' in weights:
                weights.setdefault('iron',weights.pop('vinland'))
    return config

def setup(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS wx_village_slots(
      guild INTEGER NOT NULL,user INTEGER NOT NULL,village TEXT NOT NULL,
      PRIMARY KEY(guild,user));
    CREATE INDEX IF NOT EXISTS wx_village_counts ON wx_village_slots(guild,village);
    CREATE TRIGGER IF NOT EXISTS wx_village_release_deleted AFTER UPDATE OF state ON registrations
      WHEN NEW.state='deleted' BEGIN
      DELETE FROM wx_village_slots WHERE guild=NEW.guild AND user=NEW.user; END;
    ''')

def reserve(db,guild,user,village):
    village=canonical(village);cfg=settings()
    if village not in cfg['villages']:
        raise ValueError('Aldea desconocida. Abre otra vez /asignacion iniciar.')
    old=db.execute('SELECT village FROM wx_village_slots WHERE guild=? AND user=?',(guild,user)).fetchone()
    if old and old[0]==village:return
    count=db.execute('SELECT COUNT(*) FROM wx_village_slots WHERE guild=? AND village=?',(guild,village)).fetchone()[0]
    if count>=cfg['village_cap']:
        raise ValueError(f"{cfg['villages'][village]} está llena ({count}/{cfg['village_cap']}). Elige otra aldea.")
    db.execute('INSERT INTO wx_village_slots VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET village=excluded.village',(guild,user,village))

def migrate(store,guild):
    """Idempotente: conserva ocupantes anteriores aunque ya superen el nuevo cupo."""
    from assignment_domain import fingerprint
    setup(store.db)
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        for row in store.db.execute("SELECT a.user,a.state FROM assignments a JOIN registrations r ON r.guild=a.guild AND r.user=a.user WHERE a.guild=? AND r.state!='deleted'",(guild,)).fetchall():
            state=json.loads(row['state']);normalize(state['rules'])
            village=canonical((state.get('birth') or {}).get('village'))
            if village:
                state['birth']['village']=village
                store.db.execute('INSERT INTO wx_village_slots VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET village=excluded.village',(guild,row['user'],village))
            state['rules_hash']=fingerprint(state['rules'])
            store.db.execute('UPDATE assignments SET state=? WHERE guild=? AND user=?',(json.dumps(state),guild,row['user']))
        for row in store.db.execute("SELECT id,user,data FROM registrations WHERE guild=? AND state!='deleted'",(guild,)).fetchall():
            data=json.loads(row['data']);v=canonical(data.get('birth_village'))
            if v:
                data['birth_village']=v
                store.db.execute('UPDATE registrations SET data=? WHERE id=?',(json.dumps(data),row['id']))
                store.db.execute('INSERT OR IGNORE INTO wx_village_slots VALUES(?,?,?)',(guild,row['user'],v))
