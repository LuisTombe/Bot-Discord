"""Local daily snapshots. Secrets and heavy screenshot folders are excluded to save hosting disk space."""
import asyncio,sqlite3,zipfile
from datetime import datetime
from economy_domain import TZ

def backup(store,base):
    directory=base/'respaldos';directory.mkdir(exist_ok=True)
    name='automatico-'+datetime.now(TZ).strftime('%Y-%m-%d')
    final=directory/(name+'.zip')
    if final.exists():return
    temp=directory/(name+'.tmp');dbpath=directory/(name+'.sqlite3')
    dest=sqlite3.connect(dbpath)
    try:store.db.backup(dest)
    finally:dest.close()
    try:
        with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED) as z:
            z.write(dbpath,'data/worldx-v2.sqlite3')
            for filename in ('catalogo.json','rules.json','misiones_config.json','asignacion_config.json'):
                path=base/filename
                if path.exists():z.write(path,filename)
        temp.replace(final)
    finally:dbpath.unlink(missing_ok=True);temp.unlink(missing_ok=True)
    for old in sorted(directory.glob('automatico-*.zip'))[:-3]:old.unlink(missing_ok=True)
