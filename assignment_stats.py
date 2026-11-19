"""Read-only live census and isolated, persistent simulation aggregates."""
from i18n import format_fragment, t
import csv
import io
import json
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from assignment_domain import Engine, final_data, fingerprint
from simulate_balance import sample

SECTIONS = ('elements', 'main', 'secondary')
TITLES = dict(elements='Elementos', main='Bloodlines principales', secondary='Bloodlines secundarios')
POLICIES = dict(first='Un giro por fase', practical='Hasta 3: aceptar principal y secundario', hunt='Hasta 3: buscar principales raros')

def census(records, config):
    counts = {s: Counter({k: 0 for k in config[s]}) for s in SECTIONS}
    missing = Counter(); n = 0; disabled = 0; metrics=Counter()
    for data in records:
        n += 1
        mains=data.get('mains',[]);seconds=data.get('birth_secondary',data.get('secondary',[]))
        lee=bool(set(mains)&{'kenichi','ryuji-kenichi'})
        genetic=bool(mains) and not lee
        metrics['with_genetic_main' if genetic else 'without_genetic_main']+=1
        metrics['lee' if lee else 'both' if genetic and seconds else 'main_only' if genetic else 'secondary_only' if seconds else 'neither']+=1
        audit=data.get('secondary_audit') or {}
        if audit.get('mandatory'):metrics['mandatory_secondary']+=1
        elif audit.get('eligible'):
            metrics['eligible_optional_secondary']+=1
            metrics['received_optional_secondary']+=bool(seconds)

        disabled += not data.get('chakra_usable', True)
        for section, field in [('elements','elements'),('main','mains'),('secondary','secondary')]:
            values = set(data.get('birth_secondary',data.get(field,[])) if section=='secondary' else data.get(field, []))
            if section=='main':values-={'kenichi','ryuji-kenichi'}
            if not values: missing[section] += 1
            counts[section].update(values)
    return dict(n=n, counts={s:dict(c) for s,c in counts.items()}, missing=dict(missing), disabled=disabled,metrics=dict(metrics))

def simulate(config, n, policy):
    if not 1 <= n <= 1000 or policy not in POLICIES: raise ValueError(t('Simulación inválida.'))
    engine=Engine(config); records=[]
    for _ in range(n):
        e,m,s=sample(engine,policy)
        records.append(final_data(getattr(engine,'last_state',None) or dict(results=dict(elements=e,main=m,secondary=s),rules=config,rules_hash=fingerprint(config),birth=getattr(engine,'last_birth',None))))
    result=census(records,config)
    result['simulation']=dict(mode=config.get('birth_v8',{}).get('strategy','hardcore'),profile=config.get('birth_v8',{}).get('hardcore_profile') if config.get('birth_v8',{}).get('strategy','hardcore')=='hardcore' else None,policy=policy,order=getattr(engine,'last_state',{}).get('order'),village=config.get('_simulation_village','konoha'),slots='actuales' if '_simulation_occupied' in config else 'nuevos')
    return result

class StatisticsStore:
    def __init__(self, db):
        self.db=db
        with db:
            db.execute('''CREATE TABLE IF NOT EXISTS assignment_test_batches (
                id INTEGER PRIMARY KEY, guild INTEGER NOT NULL, actor INTEGER NOT NULL,
                rules_hash TEXT NOT NULL, policy TEXT NOT NULL, summary TEXT NOT NULL,
                created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

    def real(self, guild, config, approved=False):
        query="""SELECT a.state FROM assignments a JOIN registrations r
            ON a.guild=r.guild AND a.user=r.user WHERE a.guild=? AND r.state!='deleted'"""
        if approved: query += " AND r.state='approved'"
        records=[]; versions=Counter()
        for row in self.db.execute(query,(guild,)):
            state=json.loads(row[0])
            if state['stage'] != 3: continue
            records.append(final_data(state)); versions[state['rules_hash']] += 1
        result=census(records,config); result['versions']=dict(versions)
        return result

    def save(self,guild,actor,config,policy,summary):
        with self.db:
            cur=self.db.execute('INSERT INTO assignment_test_batches(guild,actor,rules_hash,policy,summary) VALUES(?,?,?,?,?)',
                (guild,actor,fingerprint(config),policy,json.dumps(summary)))
        return cur.lastrowid

    def tests(self,guild,config,policy):
        result=census([],config)
        for row in self.db.execute('SELECT summary FROM assignment_test_batches WHERE guild=? AND rules_hash=? AND policy=?',
                                   (guild,fingerprint(config),policy)):
            summary=json.loads(row[0]); result['n']+=summary['n'];result['disabled']+=summary['disabled']
            for k,v in summary.get('metrics',{}).items():result['metrics'][k]=result['metrics'].get(k,0)+v
            for key in SECTIONS:
                result['missing'][key]=result['missing'].get(key,0)+summary['missing'].get(key,0)
                for name,n in summary['counts'][key].items():
                    result['counts'][key][name]=result['counts'][key].get(name,0)+n
        return result

_RENDER_LOCK=threading.Lock()
def attachments(summary,config,sections,source):
    """PNG plus exact CSV; count holders once, not rerolls or repeated slots."""
    from PIL import Image, ImageDraw, ImageFont
    result=[('resumen.json',json.dumps(summary,ensure_ascii=False,indent=2).encode())]
    with _RENDER_LOCK:
        font_path=str(Path(__file__).with_name('assets')/'DejaVuSans.ttf')
        font=ImageFont.truetype(font_path,19);small=ImageFont.truetype(font_path,16);title=ImageFont.truetype(font_path,27)
        for section in sections:
            pairs=sorted(summary['counts'][section].items(),key=lambda p:(-p[1],config[section].get(p[0],{}).get('label',p[0])))
            pairs.append(('__none__',summary['missing'].get(section,0)))
            width=1320;height=220+len(pairs)*34
            img=Image.new('RGB',(width,height),'#121b26');draw=ImageDraw.Draw(img)
            draw.text((30,20),'WORLD X | '+t(TITLES[section]),font=title,fill='#f2ae53')
            draw.text((30,61),source[:140],font=small,fill='white')
            draw.text((30,88),''.join([format_fragment(t('Muestra: '), '', -1), format_fragment(summary['n'], '', -1), format_fragment(t(' nacimientos | Frecuencia observada: usuarios con cada resultado / muestra'), '', -1)]),font=small,fill='#bdcddd')
            draw.text((30,115),t('Cada usuario cuenta una vez por nombre. Los porcentajes pueden sumar más de 100%.'),font=small,fill='#bdcddd')
            largest=max([n for _,n in pairs]+[1]);out=io.StringIO(newline='');writer=csv.writer(out)
            writer.writerow([t('csv.source'),t('csv.category'),t('csv.result'),t('csv.users'),t('csv.sample'),t('csv.percentage')])
            for index,(key,count) in enumerate(pairs):
                label=t('Ninguno') if key=='__none__' else t(config[section].get(key,{}).get('label',key))
                pct=100*count/summary['n'] if summary['n'] else 0
                y=155+34*index
                draw.text((30,y),label,font=font,fill='white')
                if count:draw.rectangle((330,y+3,330+int(700*count/largest),y+24),fill='#45b8b0')
                draw.text((1050,y),f'{count}  ({pct:.2f}%)',font=font,fill='white')
                writer.writerow([source,section,label,count,summary['n'],f'{pct:.6f}'])
            stamp=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            foot=''.join([format_fragment(stamp, '', -1), format_fragment(t(' | No es la probabilidad teórica. Sin uso de chakra: '), '', -1), format_fragment(summary['disabled'], '', -1), '.'])
            draw.text((30,height-38),foot,font=small,fill='#bdcddd')
            buf=io.BytesIO();img.save(buf,format='PNG')
            result.extend([(f'{section}.png',buf.getvalue()),(f'{section}.csv',out.getvalue().encode('utf-8-sig'))])
    return result
