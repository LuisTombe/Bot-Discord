"""Consistent SQLite backup. Never raw-copy a live WAL database."""
import argparse
from datetime import datetime,timezone
from pathlib import Path
import sqlite3
p=argparse.ArgumentParser();p.add_argument('--database',default='data/worldx-v2.sqlite3');p.add_argument('--out',default='backups');p.add_argument('--keep',type=int,default=3);a=p.parse_args()
source=Path(a.database).resolve()
if not source.is_file():raise SystemExit('Database does not exist; nothing was changed.')
folder=Path(a.out);folder.mkdir(parents=True,exist_ok=True)
target=folder/('worldx-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')+'.sqlite3')
with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(target) as dst:
    src.backup(dst)
    if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SystemExit('Backup failed integrity check.')
for old in sorted(folder.glob('worldx-*.sqlite3'),reverse=True)[max(1,a.keep):]:old.unlink()
print(target)
