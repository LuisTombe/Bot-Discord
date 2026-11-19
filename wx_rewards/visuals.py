"""Paneles pequeños y deterministas con Pillow; no alteran el sorteo monetario."""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import io
import math

ASSETS=Path(__file__).resolve().parents[1]/'assets'
COLORS=['#236c93','#358777','#9a7433','#97522f','#7540a3','#f2bc4c','#fff0ad']
BG='#101321';GOLD='#eac587';TEXT='#fff4de'

def font(size):return ImageFont.truetype(str(ASSETS/'DejaVuSans.ttf'),size)
def center(d,xy,text,size=20,fill=TEXT):d.text(xy,text,font=font(size),fill=fill,anchor='mm')
def coin(im,xy,size):
    with Image.open(ASSETS/'wiki-Ryo.png') as original:
        icon=original.convert('RGBA');icon.thumbnail((size,size))
        im.paste(icon,xy,icon)

def background(width,height,title,subtitle):
    im=Image.new('RGB',(width,height),BG);d=ImageDraw.Draw(im)
    for n in range(-height,width,100):d.line((n,0,n+height,height),fill='#1b2034',width=1)
    d.rounded_rectangle((12,12,width-13,height-13),radius=20,outline=GOLD,width=2)
    d.text((32,27),'WORLD X  |  HARDCORE',font=font(16),fill=GOLD)
    with Image.open(ASSETS/'wiki-Site-logo.png') as source:
        logo=source.convert('RGBA');logo.thumbnail((142,68));im.paste(logo,(width-174,25),logo)
    d.text((32,63),title,font=font(34),fill=TEXT)
    d.text((34,109),subtitle,font=font(15),fill='#c1bbd6')
    return im

def wheel_frame(config,angle=0,winner=None,small=False):
    im=background(960,680,'RULETA DE LA FORTUNA','500.000 Ryo por giro · Premios y probabilidades reales')
    d=ImageDraw.Draw(im);box=(42,169,500,627);cx,cy=271,398
    start=angle-90
    for j,p in enumerate(config['prizes']):
        end=start+p['weight']*360/10000
        d.pieslice(box,start,end,fill=COLORS[j],outline=BG,width=1)
        if p['weight']>=500:
            mid=math.radians((start+end)/2);label=p['label'].replace('.000 Ryo','k').replace('1.000k','1M').replace('2.000k','2M')
            center(d,(cx+156*math.cos(mid),cy+156*math.sin(mid)),label,18)
        start=end
    d.ellipse(box,outline=GOLD,width=5);d.ellipse((197,324,345,472),fill=BG,outline=GOLD,width=3)
    coin(im,(219,339),105)
    d.polygon([(253,156),(289,156),(271,187)],fill=TEXT)
    for j,p in enumerate(config['prizes']):
        y=180+j*49
        d.rounded_rectangle((533,y,922,y+41),radius=9,fill='#24293d',outline=GOLD if winner==p['id'] else '#3a4056',width=2)
        d.ellipse((546,y+12,562,y+28),fill=COLORS[j])
        d.text((575,y+9),p['label'],font=font(18),fill=TEXT)
        d.text((905,y+10),f"{p['weight']/100:g}%",font=font(18),fill=GOLD,anchor='ra')
    d.text((541,548),'1 reroll = 3 giros por categoría',font=font(16),fill=TEXT)
    d.text((541,575),'Elementos · Principal · Secundarios',font=font(14),fill='#bebad0')
    d.text((541,605),'La tarifa siempre se cobra completa;',font=font(13),fill='#bebad0')
    d.text((541,625),'el premio se abona por separado.',font=font(13),fill='#bebad0')
    if small:im.thumbnail((720,510))
    return im

def wheel(config,prize_id=None):
    if prize_id is None:
        b=io.BytesIO();wheel_frame(config).save(b,format='PNG',optimize=True);b.seek(0);return b
    cumulative=0
    for p in config['prizes']:
        if p['id']==prize_id:
            angle=-(cumulative+p['weight']/2)*360/10000;break
        cumulative+=p['weight']
    else:raise ValueError('Premio gráfico desconocido.')
    frames=[]
    for n in range(15):
        t=n/14;rotation=angle+720*(1-t)**3
        frame=wheel_frame(config,rotation,prize_id if n==14 else None,True)
        frames.append(frame.convert('P',palette=Image.Palette.ADAPTIVE,colors=96))
    b=io.BytesIO();frames[0].save(b,format='GIF',save_all=True,append_images=frames[1:],duration=[110]*14+[4000],optimize=False,disposal=2)
    b.seek(0);return b

def daily(config,day=0,permit_day=None,cycle=1,rerolls=0):
    im=background(1120,610,'TU CAMINO SHINOBI','Primera misión aprobada del día · Hora de Colombia · Ciclo de 7 días activos')
    d=ImageDraw.Draw(im)
    for n in range(1,8):
        x=28+(n-1)*154;y=175;done=n<=day;special=n==7 or n==permit_day
        d.rounded_rectangle((x,y,x+142,y+287),radius=15,fill='#243c38' if done else '#22273d',outline=GOLD if special else '#606487',width=3)
        center(d,(x+71,y+26),f'DÍA {n}',22,GOLD)
        if n==7:
            d.arc((x+34,y+70,x+108,y+144),20,325,fill=GOLD,width=6)
            d.polygon([(x+100,y+68),(x+114,y+84),(x+94,y+88)],fill=GOLD)
            label='1 REROLL';sub='3 giros / categoría'
        elif n==permit_day:
            center(d,(x+71,y+111),'?',64,GOLD);label='PERMISO';sub='Subjutsu elegible'
        else:
            coin(im,(x+26,y+65),90);label=f"{config['daily_money'][n-1]:,}".replace(',','.');sub='Ryo'
        center(d,(x+71,y+183),label,20);center(d,(x+71,y+215),sub,12)
        center(d,(x+71,y+259),'REGISTRADO' if done else 'PENDIENTE',12,'#a9e5c3' if done else '#bab8d0')
    center(d,(560,503),f'CICLO {cycle}  ·  PROGRESO {day}/7  ·  REROLLS DISPONIBLES {rerolls}',20,GOLD)
    center(d,(560,540),'Sin días perdidos: el progreso se conserva. Un permiso sorpresa entre los días 2 y 6.',16)
    center(d,(560,569),'Premios con saldo pendiente se abonan al reponer el fondo; consulta /recompensas historial.',14,'#bfb8d6')
    b=io.BytesIO();im.save(b,format='PNG',optimize=True);b.seek(0);return b
