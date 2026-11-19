from simple_transactions import atomic
"""Mission permissions, reports and reversible per-player credits in the existing SQLite DB."""
from i18n import format_fragment, t
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from evidence import safe_path

RANKS='DCBAS'
EDITABLE=('draft','correction','rejected')
STATES={'draft':'Borrador','pending':'Pendiente','approved':'Aprobado','correction':'Corregir','rejected':'Rechazado','frozen':'Congelado','deleted':'Eliminado'}
BOGOTA=timezone(timedelta(hours=-5))


def load_config(path):
    c=json.loads(Path(path).read_text(encoding='utf-8'))
    if c.get('version')!=1 or not 1<=c['max_squad']<=10: raise ValueError(t('Configuración de misiones inválida.'))
    if not 1<=len(c['ranks'])<=24 or not 1<=len(c['types'])<=25: raise ValueError(t('Demasiadas opciones para el panel.'))
    for r in c['ranks'].values():
        if not 0<=r['min_level']<=r['max_level']<=1000 or not 0<r['max_stats']<=18000 or r['mission_rank'] not in RANKS: raise ValueError(t('Límites de rango inválidos.'))
    for mission_type in c['types'].values():
        if mission_type['base_rank'] not in RANKS: raise ValueError(t('Rango de misión inválido.'))
    return c


def timestamp(value):
    try: return int(datetime.strptime(value.strip(),'%d/%m/%Y %H:%M').replace(tzinfo=BOGOTA).timestamp())
    except ValueError: raise ValueError(t('Usa fecha y hora de Colombia: DD/MM/AAAA HH:MM (24 horas).'))


def formatted(value):
    return datetime.fromtimestamp(value,BOGOTA).strftime('%d/%m/%Y %H:%M:%S') if value is not None else t('En curso')


def classify(config,kind,enemy):
    if kind not in config['types'] or enemy not in {'none',*config['ranks']}: raise ValueError(t('Elige el tipo de misión y rango enemigo.'))
    base=config['types'][kind]['base_rank']
    other='D' if enemy=='none' else config['ranks'][enemy]['mission_rank']
    return max((base,other),key=RANKS.index)


def stats_values(value):
    parts=re.split(r'[,;\s]+',value.strip())
    if len(parts)!=4 or any(not p.isdigit() for p in parts): raise ValueError(t('Escribe cuatro números: CHI NIN TAI HP, sin separadores de miles.'))
    numbers=[int(x) for x in parts]
    if any(x>18000 for x in numbers): raise ValueError(t('Estadísticas fuera de rango.'))
    return numbers


class MissionStore:
    def __init__(self,registrations,config,clock=None):
        self.reg=registrations;self.db=registrations.db;self.root=registrations.root;self.config=config;self.clock=clock or time.time
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS authorization_gaps(authorization INTEGER NOT NULL,started INTEGER NOT NULL,ended INTEGER);
        CREATE TABLE IF NOT EXISTS mission_settings(guild INTEGER PRIMARY KEY,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS world_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT,guild INTEGER NOT NULL,label TEXT NOT NULL,leader INTEGER NOT NULL,opened INTEGER NOT NULL,closed INTEGER);
        CREATE UNIQUE INDEX IF NOT EXISTS one_world_session ON world_sessions(guild) WHERE closed IS NULL;
        CREATE TABLE IF NOT EXISTS mission_reports(id INTEGER PRIMARY KEY AUTOINCREMENT,guild INTEGER NOT NULL,session INTEGER NOT NULL,owner INTEGER NOT NULL,kind TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'draft',revision INTEGER NOT NULL DEFAULT 0,data TEXT NOT NULL DEFAULT '{}',proof TEXT NOT NULL DEFAULT '',digest TEXT NOT NULL DEFAULT '',
          created INTEGER NOT NULL,submitted INTEGER,granted INTEGER,ended INTEGER,reason TEXT NOT NULL DEFAULT '',reviewer INTEGER,
          review_channel INTEGER,review_message INTEGER,sent_revision INTEGER NOT NULL DEFAULT -1,error TEXT NOT NULL DEFAULT '');
        CREATE UNIQUE INDEX IF NOT EXISTS one_authorization ON mission_reports(session,owner) WHERE kind='authorization' AND state!='deleted';
        CREATE TABLE IF NOT EXISTS mission_participants(report INTEGER NOT NULL,user INTEGER NOT NULL,authorization INTEGER NOT NULL,PRIMARY KEY(report,user));
        CREATE TABLE IF NOT EXISTS mission_credits(report INTEGER NOT NULL,user INTEGER NOT NULL,rank TEXT NOT NULL,PRIMARY KEY(report,user));
        CREATE TABLE IF NOT EXISTS mission_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,guild INTEGER NOT NULL,report INTEGER,actor INTEGER NOT NULL,action TEXT NOT NULL,detail TEXT NOT NULL,created INTEGER NOT NULL);
        ''')

    def now(self): return int(self.clock())
    def log(self,guild,report,actor,action,detail):
        self.db.execute('INSERT INTO mission_audit(guild,report,actor,action,detail,created) VALUES(?,?,?,?,?,?)',(guild,report,actor,action,json.dumps(detail,ensure_ascii=False),self.now()))
    def settings(self,guild):
        row=self.db.execute('SELECT data FROM mission_settings WHERE guild=?',(guild,)).fetchone()
        return json.loads(row[0]) if row else {}
    def configure(self,guild,actor,**values):
        with self.db:
            d=self.settings(guild);d.update(values)
            self.db.execute('INSERT INTO mission_settings VALUES(?,?) ON CONFLICT(guild) DO UPDATE SET data=excluded.data',(guild,json.dumps(d)))
            self.log(guild,None,actor,'settings',values)
    def session(self,ident):
        r=self.db.execute('SELECT * FROM world_sessions WHERE id=?',(ident,)).fetchone();return dict(r) if r else None
    def current(self,guild):
        r=self.db.execute('SELECT * FROM world_sessions WHERE guild=? AND closed IS NULL',(guild,)).fetchone();return dict(r) if r else None
    def open(self,guild,actor,label):
        if not label.strip() or len(label)>80: raise ValueError(t('Nombre de sesión: 1 a 80 caracteres. No escribas el código privado.'))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.current(guild): raise ValueError(t('Ya existe una sesión abierta.'))
            c=self.db.execute('INSERT INTO world_sessions(guild,label,leader,opened) VALUES(?,?,?,?)',(guild,label,actor,self.now()))
            self.log(guild,None,actor,'open',{'session':c.lastrowid,'label':label})
        return self.session(c.lastrowid)
    def close(self,guild,actor):
        with self.db:
            s=self.current(guild)
            if not s: raise ValueError(t('No hay una sesión abierta.'))
            self.db.execute('UPDATE world_sessions SET closed=? WHERE id=?',(self.now(),s['id']))
            self.db.execute("UPDATE mission_reports SET revision=revision+1 WHERE session=? AND kind='authorization' AND state!='deleted'",(s['id'],))
            self.log(guild,None,actor,'close',{'session':s['id']})
        return self.session(s['id'])
    def registered(self,guild,user):
        row=self.reg.find(guild,user)
        if not row or row['state']!='approved': raise ValueError(''.join([format_fragment(t('El usuario '), '', -1), format_fragment(user, '', -1), format_fragment(t(' necesita una ficha de registro aprobada.'), '', -1)]))
        return row
    def record(self,row):
        if not row:return None
        row=dict(row);row['data']=json.loads(row['data'])
        row['participants']=[dict(x) for x in self.db.execute('SELECT user,authorization FROM mission_participants WHERE report=?',(row['id'],))]
        return row
    def get(self,ident):return self.record(self.db.execute('SELECT * FROM mission_reports WHERE id=?',(ident,)).fetchone())
    def all(self,guild):return [self.record(r) for r in self.db.execute('SELECT * FROM mission_reports WHERE guild=? ORDER BY id DESC',(guild,))]
    def authorization(self,session,user):
        return self.record(self.db.execute("SELECT * FROM mission_reports WHERE session=? AND owner=? AND kind='authorization' AND state!='deleted'",(session,user)).fetchone())
    def start(self,guild,user,kind,session_id=None):
        self.registered(guild,user)
        if kind not in ('authorization','mission'):raise ValueError(t('Tipo de reporte inválido.'))
        s=self.session(session_id) if session_id else self.current(guild)
        if not s or s['guild']!=guild:raise ValueError(t('No hay sesión disponible. El líder debe abrir Open World en el bot.'))
        if kind=='authorization' and s['closed'] is not None:raise ValueError(t('La sesión ya terminó.'))
        with self.db:
            if kind=='authorization': existing=self.authorization(s['id'],user)
            else:
                a=self.authorization(s['id'],user)
                if not a or a['state']!='approved':raise ValueError(t('Primero necesitas autorización aprobada para esta sesión.'))
                existing=self.record(self.db.execute("SELECT * FROM mission_reports WHERE session=? AND owner=? AND kind='mission' AND state IN ('draft','correction','rejected') ORDER BY id DESC LIMIT 1",(s['id'],user)).fetchone())
            if existing:return existing
            data={'members':[user]} if kind=='mission' else {}
            c=self.db.execute('INSERT INTO mission_reports(guild,session,owner,kind,data,created) VALUES(?,?,?,?,?,?)',(guild,s['id'],user,kind,json.dumps(data),self.now()))
            self.log(guild,c.lastrowid,user,'draft',{})
        return self.get(c.lastrowid)
    def edit(self,ident,revision,user,data=None,proof=None):
        with self.db:
            row=self.get(ident)
            if not row or row['owner']!=user or row['revision']!=revision or row['state'] not in EDITABLE:raise ValueError(t('Reporte bloqueado o desactualizado. Usa /misiones continuar.'))
            self.registered(row['guild'],user)
            if proof:
                path=safe_path(self.root,proof['path'])
                if not path.is_file():raise ValueError(t('No se encuentra la captura.'))
            self.log(row['guild'],ident,user,'edit',{'previous':row})
            self.db.execute('UPDATE mission_reports SET data=?,proof=?,digest=?,revision=revision+1 WHERE id=?',
                (json.dumps(data if data is not None else row['data']),proof['path'] if proof else row['proof'],proof['digest'] if proof else row['digest'],ident))
        return self.get(ident)
    def validate_auth(self,row):
        d=row['data'];r=self.config['ranks'].get(d.get('rank'))
        if not r:raise ValueError(t('Elige el rango de tu shinobi.'))
        try:level=int(d.get('level',''))
        except (ValueError,TypeError):raise ValueError(t('Escribe un nivel válido.'))
        if not r['min_level']<=level<=r['max_level']:raise ValueError(''.join([format_fragment(t('Nivel permitido: '), '', -1), format_fragment(r['min_level'], '', -1), '–', format_fragment(r['max_level'], '', -1), '.']))
        stats=stats_values(d.get('stats',''))
        if sum(stats)>r['max_stats']:raise ValueError(''.join([format_fragment(t('Superas el máximo de '), '', -1), format_fragment(r['max_stats'], '', -1), format_fragment(t(' puntos asignados entre CHI, NIN, TAI y HP.'), '', -1)]))
        for key in ('village','counterparty','purpose'):
            if not str(d.get(key,'')).strip():raise ValueError(t('Completa aldea, aliado/enemigo y actividad solicitada.'))
        s=self.session(row['session'])
        if s['closed'] is not None and row['granted'] is None:raise ValueError(t('La sesión terminó: no se pueden conceder nuevas autorizaciones.'))
        return stats
    def validate_mission(self,row):
        d=row['data'];rank=classify(self.config,d.get('type'),d.get('enemy'))
        start=timestamp(d.get('start',''));end=timestamp(d.get('end',''))
        if start>end or end>self.now():raise ValueError(t('La misión debe haber terminado; revisa inicio y fin (hora Colombia).'))
        s=self.session(row['session'])
        if start<s['opened']//60*60 or (s['closed'] is not None and end>s['closed']):raise ValueError(t('El horario está fuera de la sesión de Open World.'))
        members=d.get('members',[])
        if not isinstance(members,list) or not all(type(u) is int for u in members) or row['owner'] not in members or len(members)!=len(set(members)) or not 1<=len(members)<=self.config['max_squad']:raise ValueError(t('Participantes inválidos; debes incluirte y respetar el límite de squad.'))
        if not d.get('opponents','').strip():raise ValueError(t('Indica los usernames enemigos, o Sin enemigo si aplica.'))
        refs=[]
        for user in members:
            reg=self.registered(row['guild'],user)
            a=self.authorization(row['session'],user)
            if not a or a['state']!='approved' or a['granted'] is None:raise ValueError(''.join([format_fragment(t('Falta autorización aprobada para '), '', -1), format_fragment(user, '', -1), '.']))
            # Inputs are minute-precision; floor grant to the same minute only.
            if start<a['granted']//60*60 or (a['ended'] is not None and end>a['ended']):raise ValueError(''.join([format_fragment(t('El horario está fuera de la autorización de '), '', -1), format_fragment(user, '', -1), '.']))
            gaps=self.db.execute('SELECT started,ended FROM authorization_gaps WHERE authorization=?',(a['id'],))
            if any(start < (gap['ended'] if gap['ended'] is not None else self.now()+1) and end >= gap['started'] for gap in gaps):
                raise ValueError(''.join([format_fragment(t('La misión coincide con una suspensión de autorización de '), '', -1), format_fragment(user, '', -1), '.']))
            refs.append({'user':user,'authorization':a['id'],'username':reg['data'].get('roblox_name',''),'rank':a['data']['rank'],'stats':a['data']['stats'],'level':a['data']['level']})
        return rank,refs
    def submit(self,ident,revision,user):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.get(ident)
            if not row or row['owner']!=user or row['revision']!=revision or row['state'] not in EDITABLE:raise ValueError(t('Reporte desactualizado o ya enviado.'))
            self.registered(row['guild'],user)
            if not row['proof'] or not safe_path(self.root,row['proof']).is_file():raise ValueError(t('Adjunta la captura obligatoria.'))
            d=row['data']
            if row['kind']=='authorization':self.validate_auth(row)
            else:
                rank,refs=self.validate_mission(row);d['mission_rank']=rank;d['roster']=refs
                dup=self.db.execute("SELECT id FROM mission_reports WHERE guild=? AND kind='mission' AND id!=? AND digest=? AND state NOT IN ('deleted','rejected')",(row['guild'],ident,row['digest'])).fetchone()
                if dup:raise ValueError(''.join([format_fragment(t('Esa misma captura ya pertenece al reporte #'), '', -1), format_fragment(dup[0], '', -1), format_fragment(t('. No repitas la misión.'), '', -1)]))
                self.db.execute('DELETE FROM mission_participants WHERE report=?',(ident,))
                self.db.executemany('INSERT INTO mission_participants VALUES(?,?,?)',[(ident,x['user'],x['authorization']) for x in refs])
            self.db.execute("UPDATE mission_reports SET state='pending',revision=revision+1,data=?,submitted=?,reason='',reviewer=NULL WHERE id=?",(json.dumps(d),self.now(),ident))
            self.log(row['guild'],ident,user,'submit',d)
        return self.get(ident)
    def decision(self,ident,revision,actor,action,reason,demo=False):
        targets={'approve':'approved','reject':'rejected','correct':'correction','freeze':'frozen','reopen':'correction','delete':'deleted'}
        if action not in targets or not reason.strip() or len(reason)>300:raise ValueError(t('Indica una acción y un motivo de 1 a 300 caracteres.'))
        with atomic(self.db):
            row=self.get(ident)
            if not row or row['revision']!=revision or row['state']=='deleted':raise ValueError(t('El reporte cambió o fue eliminado. Abre su versión actual.'))
            if action in ('approve','reject','correct') and row['state']!='pending':raise ValueError(t('Solo se resuelven reportes pendientes. Reabre y vuelve a enviar si necesitas corregirlo.'))
            if action=='approve':
                involved={row['owner']}|{x['user'] for x in row['participants']}
                if actor in involved and not demo:raise ValueError(t('Otro revisor debe aprobar un reporte en el que participas.'))
                self.registered(row['guild'],row['owner'])
                if not row['proof'] or not safe_path(self.root,row['proof']).is_file():raise ValueError(t('La captura no está disponible.'))
                if row['kind']=='authorization':self.validate_auth(row)
                else:
                    rank,refs=self.validate_mission(row)
                    if refs!=row['data'].get('roster'):raise ValueError(t('Cambiaron los datos autorizados de un participante. Solicita corrección y reenvío.'))
                    if rank!=row['data']['mission_rank']:raise ValueError(t('La clasificación cambió; devuelve el reporte para actualizarlo.'))
                    self.db.executemany('INSERT OR IGNORE INTO mission_credits VALUES(?,?,?)',[(ident,x['user'],rank) for x in refs])
            else:
                self.db.execute('DELETE FROM mission_credits WHERE report=? OR (report<0 AND (-report)/100=?)',(ident,ident))
            granted=row['granted']
            if row['kind']=='authorization':
                if action=='approve':
                    if granted is None:granted=self.now()
                    self.db.execute('UPDATE authorization_gaps SET ended=? WHERE authorization=? AND ended IS NULL',(self.now(),ident))
                elif row['state']=='approved':
                    self.db.execute('INSERT INTO authorization_gaps VALUES(?,?,NULL)',(ident,self.now()))
            self.db.execute('UPDATE mission_reports SET state=?,revision=revision+1,reason=?,reviewer=?,granted=? WHERE id=?',(targets[action],reason,actor,granted,ident))
            self.log(row['guild'],ident,actor,action,{'reason':reason,'previous':row})
            if row['kind']=='authorization' and action!='approve':
                related=self.db.execute("SELECT DISTINCT r.id FROM mission_reports r JOIN mission_participants p ON p.report=r.id WHERE p.authorization=? AND r.state NOT IN ('deleted','rejected','frozen')",(ident,)).fetchall()
                for item in related:
                    mid=item[0]
                    self.db.execute("UPDATE mission_reports SET state='frozen',revision=revision+1,reason=? WHERE id=?",(''.join([format_fragment(t('Autorización #'), '', -1), format_fragment(ident, '', -1), format_fragment(t(' invalidada: '), '', -1), format_fragment(reason, '', -1)]),mid))
                    self.db.execute('DELETE FROM mission_credits WHERE report=?',(mid,))
                    self.log(row['guild'],mid,actor,'freeze_dependency',{'authorization':ident,'reason':reason})
        return self.get(ident)
    def end_authorization(self,guild,user,session_id):
        with self.db:
            row=self.authorization(session_id,user)
            if not row or row['guild']!=guild or row['state']!='approved':raise ValueError(t('No tienes autorización aprobada en esa sesión.'))
            if row['ended'] is not None:raise ValueError(t('Ya marcaste tu salida.'))
            self.db.execute('UPDATE mission_reports SET ended=?,revision=revision+1 WHERE id=?',(self.now(),row['id']))
            self.log(guild,row['id'],user,'end',{})
        return self.get(row['id'])
    def totals(self,guild,user,session=None):
        query="SELECT c.rank,count(*) n FROM mission_credits c JOIN mission_reports r ON r.id=(CASE WHEN c.report<0 THEN (-c.report)/100 ELSE c.report END) WHERE r.guild=? AND r.state='approved' AND c.user=?"
        args=[guild,user]
        if session is not None:query+=' AND r.session=?';args.append(session)
        rows=self.db.execute(query+' GROUP BY c.rank',args)
        result=dict.fromkeys(RANKS,0);result.update({r['rank']:r['n'] for r in rows});return result
    def activity(self,guild,user):
        records=[]
        for row in self.all(guild):
            if row['owner']!=user or row['kind']!='authorization' or row['state']!='approved' or row['granted'] is None:continue
            session=self.session(row['session'])
            end=min(x for x in (row['ended'],session['closed'],self.now()) if x is not None)
            start=row['granted'];seconds=max(0,end-start)
            for gap in self.db.execute('SELECT started,ended FROM authorization_gaps WHERE authorization=?',(row['id'],)):
                seconds-=max(0,min(end,gap['ended'] if gap['ended'] is not None else end)-max(start,gap['started']))
            records.append({'session':row['session'],'authorization':row['id'],'start':start,'end':end,'ongoing':row['ended'] is None and session['closed'] is None,'seconds':max(0,seconds),'totals':self.totals(guild,user,row['session'])})
        return records
    def history(self,guild,ident):return [dict(r) for r in self.db.execute('SELECT actor,action,created FROM mission_audit WHERE guild=? AND report=? ORDER BY id DESC LIMIT 15',(guild,ident))]
    def delivered(self,ident,channel,message,revision):
        with self.db:self.db.execute("UPDATE mission_reports SET review_channel=?,review_message=?,sent_revision=?,error='' WHERE id=?",(channel,message,revision,ident))
    def error(self,ident,error):
        with self.db:self.db.execute('UPDATE mission_reports SET error=? WHERE id=?',(error[:180],ident))
