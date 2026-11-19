"""Reproducible HTML V3 import, no runtime parser dependency."""
from html.parser import HTMLParser
from pathlib import Path
import json,re,hashlib,unicodedata,argparse
class Parser(HTMLParser):
    def __init__(self):super().__init__();self.rows=[];self.row=None;self.cell=None
    def handle_starttag(self,t,a):
        a=dict(a)
        if t=='tr' and 'data-cat' in a:self.row={'attrs':a,'cells':[],'wiki':None}
        if t=='td' and self.row is not None:self.cell=[]
        if t=='a' and self.row is not None:self.row['wiki']=a.get('href')
    def handle_data(self,s):
        if self.cell is not None:self.cell.append(s)
    def handle_endtag(self,t):
        if t=='td' and self.cell is not None:self.row['cells'].append(' '.join(self.cell).strip());self.cell=None
        if t=='tr' and self.row is not None:self.rows.append(self.row);self.row=None

def slug(s):return re.sub('[^a-z0-9]+','-',unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower()).strip('-')
def cond(field,op,value):return {'field':field,'op':op,'value':value}
def req(label,field,op,value,policy='hard'):return {'label':label,'policy':policy,'condition':cond(field,op,value)}
def number(s):
    m=re.search(r'\d[\d.]*',s)
    return int(m[0].replace('.','')) if m else None
MAP={'Armas':'weapons','Lanzables':'throwables','Consumibles':'consumables','Jutsus de bloodline':'bloodline_jutsu','Ninjutsu elemental':'element_jutsu','Modes de bloodline':'modes','Modes de subjutsu':'sub_modes','Subjutsus':'subjutsu','Taijutsus':'taijutsu','Kenjutsu':'objects','Estilos de combate':'objects','Invocaciones':'subjutsu'}
EMOJI={'weapons':'⚔️','throwables':'🎯','consumables':'🍙','bloodline_jutsu':'🧬','element_jutsu':'🔥','modes':'✨','sub_modes':'🌀','subjutsu':'📜','taijutsu':'🥋','objects':'🎒','race_permits':'🎭'}
TIERS={'Academia':0,'Genin':1,'Chunin':2,'Jounin':3,'Jounin Sensei':4,'Subkage':4,'Sannin':4,'Kage':5}
BL_ALIASES={'ryuji-kenichi':'ryuji-kenichi','ryugi-kenichi':'ryuji-kenichi','hoshigaki':'wanziame'}
ELEMENTS={'earth':'earth','water':'water','fire':'fire','wind':'wind','lightning':'lightning','shock':'lightning','shiver':'shiver','acid':'acid','cement':'cement','gale':'gale','combustion':'combustion'}

def base(ident,name,category,rank,price,source):
    return dict(id=ident,name=name,category=category,type='permission',rank=rank,price=price,price_x3=price*3,requirements=[],emoji=EMOJI[category],image_url=None,enabled=True,unique=True,tradable=False,teachable=False,filters={},source=source)

def import_html(path):
    raw=Path(path).read_bytes();p=Parser();p.feed(raw.decode('utf-8'));items=[]
    for idx,r in enumerate(p.rows):
        c=r['cells'];cat=r['attrs']['data-cat'];category=MAP[cat];ident='v3-'+slug(c[1])[:65]+'-'+hashlib.sha256(json.dumps(c).encode()).hexdigest()[:8];price=number(c[7])
        if price is None:raise ValueError('Precio no numérico: '+c[1])
        it=base(ident,c[1],category,c[2],price,'HTML V3 / fila '+str(idx+1));it['source_sha256']=hashlib.sha256(raw).hexdigest();it['source_values']=dict(zip(['group','name','rank','base_rank','level','stats','native_cost','price','price_x3','acquisition','notes','wiki'],c));it['wiki_url']=r['wiki'];it['notes']=c[10]
        # Carry all prose conditions forward; verify them explicitly, never silently treat as satisfied.
        it['requirements'].append(req('Condiciones y desbloqueo del HTML verificados por staff','tags','all',['unlock:'+ident]))
        candidates=[TIERS[k.strip()] for k in c[3].split('/') if k.strip() in TIERS];tier=min(candidates or [0])
        it['requirements'].append(req('Rango base: '+c[3],'rank_level','gte',tier,'x3'))
        lev=number(c[4]);stat=number(c[5]);prefix=c[1].split(' · ')[0]
        if 'bloodline' in cat:
            bl=slug(prefix);it['filters']['bloodline']=[bl]
            it['requirements'].append(req('Bloodline registrado: '+prefix,'bloodlines','any',[bl]))
            if lev:it['requirements'].append(req(c[4]+' (mínimo de rol)','bloodline_levels.'+bl,'gte',lev))
        elif lev:it['requirements'].append(req(c[4]+' (mínimo)','level','gte',lev))
        if stat:
            kinds=re.findall(r'\b(CHI|TAI|NIN|HP)\b',c[5])
            for statname in kinds:it['requirements'].append(req(c[5],statname.lower(),'gte',stat))
        if cat=='Ninjutsu elemental':
            first=slug(c[0].replace('Ninjutsu elemental','').strip()).split('-')[0];el=ELEMENTS.get(first,first);it['filters']['element']=[el];it['requirements'].append(req('Afinidad elemental: '+el,'elements','any',[el]))
        if category in ('throwables','consumables','weapons','objects'):
            it['type']='physical' if category!='objects' else 'permission';it['unique']=category not in ('throwables','consumables');it['tradable']=True
        if category in ('bloodline_jutsu','element_jutsu','taijutsu','subjutsu'):it['teachable']=True;it['tradable']=True;it['teaching_fee']=price
        apply_rules(it)
        if it['teachable']:it['teaching_fee']=it['price']
        items.append(it)
    if len({x['id'] for x in items})!=len(items):raise ValueError('Duplicate HTML names; resolve IDs before import.')
    return items

def apply_rules(it):
    n=slug(it['name']);notes=[]
    blocked=['shiki-hurricane','shiki-huricane','kagoku-rabbit','jinshiki-god','korashi-multi','tyn-tailed','reality-style-warp','rab-style','raion-style-rengoku-inferno-spirit-blade','shiver-style-dimension-blade','bankai','light-style-sun-flare','forged-style-fireball-warp','maki-style-ashura-buckshot']
    # Only Shindai subjutsus are prohibited; do not ban Shindai bloodline skills.
    if any(x in n for x in blocked) or ('shindai' in n and it['category'] in ('subjutsu','sub_modes')) or 'gen-2' in n or 'gen-3' in n or '10-colas' in n:
        it['enabled']=False;notes.append('Bloqueado por reglas del servidor.')
    def restrict(field,values,label):it['requirements'].append(req(label,field,'any',values))
    for key,field,values in [
        ('ryuji-axis','bloodlines',['kenichi','ryuji-kenichi']),('narumaki-barrage','tags',['clan:uzumaki']),('bruce-dragon-combo','bloodlines',['ryuji-kenichi']),('dynamic-stomp','bloodlines',['kenichi','ryuji-kenichi']),('thousand-air-palms','bloodlines',['arahaki-jokei']),('crow-salvation','tags',['clan:uchiha']),('exploding-vanishing','bloodlines',['explosion','clay']),('water-vanishing','elements',['water']),('wood-vanishing','bloodlines',['wood','senju']),('shadow-snowman','bloodlines',['ice']),('vanishing-clone-spirit-bomb','tags',['clan:uzumaki']),('gezo-mado','tags',['rinnegan']),('planetary-demolition','tags',['rinnegan']),('senko-spirit-bomb','bloodlines',['senko','dio-senko']),('senko-storm','bloodlines',['senko','dio-senko']),('spirit-bomb-shuriken-rush','elements',['wind']),('spirit-bomb-shuriken-toss','elements',['wind']),('rasenshuriken-rush','elements',['wind']),('rasenshuriken-toss','elements',['wind']),('magma-spirit-bomb','bloodlines',['lava','magma']),('atomic-style-dismant','bloodlines',['dust','atomic']),('blood-style-blood-wave','bloodlines',['dokei']),('inferno-armour-control','bloodlines',['raion-akuma']),('sand-shuriken-bomb','bloodlines',['sand'])]:
        if key in n:restrict(field,values,'Exclusividad: '+', '.join(values))
    if ('medical' in n or 'medico' in slug(it.get('source_values',{}).get('group',''))):restrict('tags',['medical-corps'],'Pertenecer a Medical Corps')
    if any(x in n for x in ['shock-style','lightning-style','shiver-style','odin-shock-wolf']):
        it['requirements'].append({'label':'Rayo o afinidad equivalente','policy':'hard','condition':{'any':[cond('elements','any',['lightning','shiver']),cond('bloodlines','any',['storm','lightning','odin-saberu'])]}});it['filters']['element']=['lightning']
    if it['category']=='subjutsu':
        for key,el in [('lightning-shurikens','lightning'),('shockslam','lightning'),('fire-shurikens','fire'),('water-jet','water'),('earth-style','earth'),('stone-style','earth'),('air-style','wind'),('acidic-bullets','acid')]:
            if key in n:restrict('elements',[el],'Afinidad elemental: '+el);it['filters']['element']=[el]
    if 'great-spiraling-spirit' in n:it['requirements'].append({'label':'Uzumaki o Menza','policy':'hard','condition':{'any':[cond('tags','any',['clan:uzumaki']),cond('bloodlines','any',['menza'])]}})
    if 'menza-azarashi' in n:restrict('tags',['clan:uzumaki'],'Exclusivo Uzumaki')
    discounts=[]
    if any(x in n for x in ['scarecrow-seal','firework-subjutsu','poison-style-acidic-cloud','hydra-style-liquefy']):
        condition={'any':[cond('bloodlines','any',['minakami','shirogane']),cond('tags','any',['puppeteer'])]}
        if any(x in n for x in ['acidic-cloud','liquefy']):condition['any'].append(cond('elements','any',['acid']))
        discounts.append({'condition':condition,'price':(it['price']+2)//3})
    for key,field,vals in [('menza-azarashi','tags',['clan:uzumaki']),('atomic-style-dismant','bloodlines',['dust','atomic']),('blood-style-blood-wave','bloodlines',['dokei'])]:
        if key in n:discounts.append({'condition':cond(field,'any',vals),'price':(it['price']+2)//3})
    if 'ashen-raigo' in n:discounts.append({'condition':{'any':[cond('elements','any',['lightning','shiver']),cond('bloodlines','any',['storm','lightning'])]},'price':(it['price']+2)//3})
    if 'body-dodge' in n:
        it['price']=5000000;it['price_x3']=15000000;discounts=[{'condition':cond('bloodlines','any',['kenichi','ryuji-kenichi']),'price':500000}];notes.append('Regla explícita: Body Dodge 5M; Lee/Kenichi 500k.')
    if discounts:it['discounts']=discounts
    if 'gen-1' in n or 'gen-1' in slug(it.get('source_values',{}).get('group','')):
        restrict('tags',['jinchuuriki:designado'],'Designación por aldea (Gen 1)');restrict('tags',['jinchuuriki:'+n.split('-')[0]],'Designación del bijuu exacto');it['tradable']=False;it['teachable']=False
    if notes:it['server_overrides']=notes

def supplements():
    out=[]
    for name,rank,price,bllevel,chi,native in [('Paper Birds','C',25000,50,650,60),('Shuriken Seeker','B',200000,150,1500,370),('Paper Vortex','A',650000,300,2300,480),('Modo Z','A',550000,300,2300,550)]:
        ident='paper-'+slug(name);it=base(ident,'Paper · '+name,'modes' if name=='Modo Z' else 'bloodline_jutsu',rank,price,'Lámina Paper World X aprobada: propuesta de rol');it['filters']={'bloodline':['paper']}
        it['requirements']=[req('Paper registrado','bloodlines','any',['paper']),req('Rango base','rank_level','gte',{'C':1,'B':2,'A':3}[rank],'x3'),req('Nivel BL de rol','bloodline_levels.paper','gte',bllevel),req('CHI de rol','chi','gte',chi),req('Desbloqueo nativo Paper nivel '+str(native),'tags','all',['unlock:'+ident])]
        if name=='Modo Z':it['requirements'].append(req('TAI de rol','tai','gte',2300))
        it['native']={'level':native,'chi':{'Paper Birds':500,'Shuriken Seeker':1600,'Paper Vortex':2300}.get(name),'ryo':{'Paper Birds':25000,'Shuriken Seeker':55000,'Paper Vortex':120000}.get(name)}
        it['teachable']=name!='Modo Z';it['teaching_fee']=price;out.append(it)
    for race,price,bl,level,chi in [('humano',0,[],0,0),('shark',1500000,['wanziame','hoshigaki'],650,3000),('snake',1500000,['cobra'],650,3000),('puppet',1500000,['minakami'],650,3000)]:
        it=base('race-'+race,'Permiso de raza · '+race,'race_permits','D' if race=='humano' else 'S',price,'PROPUESTA: permiso racial; no hay precio confirmado en HTML');it['enabled']=False;it['grants_race']=race;it['filters']={'race':[race],'bloodline':bl}
        it['requirements']=[req('Raza aprobada por staff','tags','all',['race-approved:'+race])]
        if bl:it['requirements'] += [req('Linaje compatible','bloodlines','any',bl),req('Nivel de progresión','level','gte',level),req('CHI mínimo','chi','gte',chi),req('Boss racial derrotado y misión validada','tags','all',['boss:race:'+race]),req('Rango base Jounin Sensei','rank_level','gte',4,'x3')]
        out.append(it)
    # Nine permitted Gen-1 beasts. No Gen-2, Gen-3 or Ten-Tails are created.
    for beast,bl in [('su','sand'),('mao','scorch'),('isu','coral'),('sun','lava'),('ku','boil'),('sei','bubble'),('chu','insect'),('gai','ink'),('kor','chakra')]:
        it=base('jinch-bloodline-'+beast,'Jinchuuriki Gen 1 · afinidad '+bl,'bloodline_jutsu','S',1300000,'PROPUESTA DE ROL: afinidad del jinchuuriki; no afirma equivalencia oficial de bloodline');it['enabled']=False;it['filters']={'bloodline':[bl]}
        it['requirements']=[req('Designación exacta del bijuu','tags','all',['jinchuuriki:'+beast,'jinchuuriki:designado']),req('Linaje/afinidad asignado en ficha','bloodlines','any',[bl]),req('Autorización del líder y del boss','tags','all',['boss:jinch:'+beast,'leader:jinch:'+beast]),req('Nivel mínimo','level','gte',600),req('CHI mínimo','chi','gte',3000),req('Rango base','rank_level','gte',4,'x3')];out.append(it)
    return out

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('html');a.add_argument('--out',default='config/market');v=a.parse_args();out=Path(v.out);out.mkdir(parents=True,exist_ok=True)
    rows=import_html(v.html)
    for filename,items in [('catalog_html.json',rows),('catalog_additions.json',supplements())]:
        (out/filename).write_text(json.dumps({'version':1,'items':items},ensure_ascii=False,indent=2),encoding='utf-8')
    print('HTML:',len(rows),'Suplementos:',len(supplements()))
