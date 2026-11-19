"""Comandos y botones persistentes. Solo el dueño configura o borra fichas."""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
import discord
from discord import app_commands
from .domain import Rewards,TREASURY
from . import visuals
from .icons import resolve

log=logging.getLogger('worldx.rewards')

def ryo(value):return f'{value:,}'.replace(',','.')+' Ryo'
def prize_text(p):
    if p['kind']=='money':return ryo(p['amount'])
    if p['kind']=='reroll':return str(p['amount'])+' reroll(s) completos'
    return p['item']['name']+' · rango '+p['item']['rank']

class SafeView(discord.ui.View):
    async def on_error(self,i,error,item):
        log.exception('Error en botón de recompensas',exc_info=error)
        text=str(error) if isinstance(error,ValueError) else 'No se pudo mostrar el resultado. Revisa /recompensas historial antes de volver a girar.'
        if i.response.is_done():await i.followup.send(text,ephemeral=True)
        else:await i.response.send_message(text,ephemeral=True)

class RouletteView(SafeView):
    def __init__(self,s):super().__init__(timeout=None);self.s=s
    @discord.ui.button(label='Girar · 500.000 Ryo',emoji='🎡',style=discord.ButtonStyle.success,custom_id='wxr:spin:v1')
    async def spin(self,i,b):await self.s.spin(i)
    @discord.ui.button(label='Mis recompensas',emoji='🎁',custom_id='wxr:daily:v1')
    async def daily(self,i,b):await self.s.board(i)
    @discord.ui.button(label='Premios y probabilidades',emoji='📊',custom_id='wxr:odds:v1')
    async def odds(self,i,b):
        self.s.guild(i)
        await i.response.send_message(embed=self.s.odds(),ephemeral=True)

class ResetModal(discord.ui.Modal,title='Borrar todas las fichas del servidor'):
    confirmation=discord.ui.TextInput(label='Escribe BORRAR seguido del ID del servidor',max_length=60)
    def __init__(self,s,token):super().__init__(timeout=300);self.s=s;self.token=token
    async def on_submit(self,i):
        try:
            self.s.owner(i);await i.response.defer(ephemeral=True)
            async with self.s.ctx['OPERATIONS']:
                counts,path=self.s.store.reset(i.user.id,self.token,str(self.confirmation).strip())
            await i.followup.send(f"Reinicio realizado: {counts['registrations']} fichas y {counts['assignments']} asignaciones eliminadas. Cupos únicos liberados. Copia privada en `respaldos/{path.name}`. La retirada de roles y mensajes se procesa en segundo plano. La economía e inventarios se conservan.",ephemeral=True)
        except (ValueError,OSError) as e:await self.s.reply(i,str(e))

class ResetView(SafeView):
    def __init__(self,s,token):
        super().__init__(timeout=300);self.s=s;self.token=token
    @discord.ui.button(label='Confirmar borrado global',style=discord.ButtonStyle.danger)
    async def confirm(self,i,b):
        self.s.owner(i);await i.response.send_modal(ResetModal(self.s,self.token))

class RerollModal(discord.ui.Modal,title='Usar un reroll completo'):
    confirmation=discord.ui.TextInput(label='Escribe REEMPLAZAR para aceptar',max_length=20)
    def __init__(self,s,user,revision,epoch):super().__init__(timeout=300);self.s=s;self.user=user;self.revision=revision;self.epoch=epoch
    async def on_submit(self,i):
        try:
            self.s.guild(i)
            if i.user.id!=self.user or self.s.store.epoch()!=self.epoch or str(self.confirmation).strip()!='REEMPLAZAR':raise ValueError('Confirmación inválida o vencida.')
            await i.response.defer(ephemeral=True)
            async with self.s.ctx['OPERATIONS']:self.s.store.use_reroll(i.user.id,self.revision)
            await i.followup.send('Reroll usado. Conservas tu aldea y tienes 3 giros por cada categoría. Continúa en /asignacion iniciar, elige el orden, termina la asignación y vuelve a enviar tu ficha. Los únicos históricos no se liberan por un reroll.',ephemeral=True)
        except ValueError as e:await self.s.reply(i,str(e))

class PageView(SafeView):
    def __init__(self,s,user,page,logs=False):
        super().__init__(timeout=180);self.s=s;self.user=user;self.page=page;self.logs=logs
        for label,delta in [('Anterior',-1),('Siguiente',1)]:
            b=discord.ui.Button(label=label,disabled=delta<0 and page==1)
            async def cb(i,delta=delta):
                if i.user.id!=user:raise ValueError('Abre tu propio historial.')
                await s.history(i,page+delta,logs)
            b.callback=cb;self.add_item(b)

class RewardsSystem:
    def __init__(self,bot,ctx):
        self.bot=bot;self.ctx=ctx;self.market=ctx['MARKET'];self.task=None;self.render_lock=asyncio.Lock()
        def cleanup(r):
            messages=[]
            if r['review_message']:messages.append([ctx['REVIEW'],r['review_message']])
            if r['public_message']:messages.append([ctx['PUBLIC'],r['public_message']])
            return {'user':r['user'],'messages':messages,'roles':list(ctx['assigned_roles'](r))}
        self.store=Rewards(self.market.store,ctx['ASSIGNMENTS'].store,ctx['GUILD'],lambda:ctx['BOT_OWNER'],ctx['BASE'],cleanup)
        self.view=RouletteView(self)
        self.db=self.store.db
        self.db.executescript('CREATE TABLE IF NOT EXISTS reward_icons(item TEXT PRIMARY KEY,url TEXT,checked INTEGER NOT NULL);')
        self.install()
    def guild(self,i):
        if i.guild_id!=self.ctx['GUILD'] or not isinstance(i.user,discord.Member):raise ValueError('Usa el servidor World X configurado.')
    def owner(self,i):self.guild(i);self.store.owner_only(i.user.id)
    async def reply(self,i,text):
        if i.response.is_done():return await i.followup.send(text,ephemeral=True)
        return await i.response.send_message(text,ephemeral=True)
    def restore(self):
        self.bot.add_view(RouletteView(self))
        for cid,cb in [
            ('worldx:roulette:spin',self.spin),
            ('worldx:roulette:daily',self.board),
            ('wx:roulette:spin',self.spin),
            ('wx:roulette:daily',self.board),
        ]:
            v=SafeView(timeout=None)
            b=discord.ui.Button(label='Ruleta',custom_id=cid)
            async def _run(i,fn=cb):await fn(i)
            b.callback=_run;v.add_item(b)
            self.bot.add_view(v)
        if not self.task:self.task=asyncio.create_task(self.worker())
    async def worker(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                async with self.ctx['OPERATIONS']:self.store.scan()
            except Exception:log.exception('Recompensas pendientes; se reintentará sin duplicar premios')
            await asyncio.sleep(20)
    def odds(self):
        s=self.store
        e=discord.Embed(title='World X · Ruleta',description='Cada giro cuesta **500.000 Ryo** y entra en el fondo general. El premio en dinero se paga desde ese mismo fondo.',color=0xEAC587)
        e.add_field(name='Premios',value='\n'.join(f"**{p['label']}** — {p['weight']/100:g} %" for p in s.c['prizes']),inline=False)
        e.add_field(name='Reroll completo',value='3 giros de elementos + 3 de principal + 3 de secundarios. Reemplaza la asignación; conserva la aldea. Los bloodlines únicos ya nacidos siguen ocupados.',inline=False)
        e.set_footer(text='Probabilidades por giro independientes · Sin premios garantizados por número de intentos')
        return e
    async def spin(self,i):
        self.guild(i)
        if i.channel_id!=self.store.channel():
            if i.type!=discord.InteractionType.component:
                raise ValueError('Gira en el canal configurado para la ruleta.')
        await i.response.defer(ephemeral=True)
        await self.market.refresh(i.guild_id,i.user.id)
        if self.render_lock.locked():raise ValueError('La ruleta está terminando otra animación. Reintenta en unos segundos; no se cobró.')
        async with self.render_lock:
            row=self.store.spin(i.user.id,str(i.id));p=json.loads(row['result'])
            e=discord.Embed(title='Tu premio: '+prize_text(p),description=f"Giro #{row['id']} · Tarifa: {ryo(p['fee'])}\nSaldo: {ryo(self.store.e.balance(i.guild_id,'u:'+str(i.user.id)))}\nRerolls disponibles: {self.store.credits(i.user.id)}",color=0xEAC587)
            try:
                stream=await asyncio.to_thread(visuals.wheel,self.store.c,p['id'])
                e.set_image(url='attachment://ruleta.gif')
                await i.followup.send(embed=e,file=discord.File(stream,'ruleta.gif'),ephemeral=True)
            except Exception:
                log.exception('Premio guardado, animación no entregada')
                e.set_image(url=None)
                await i.followup.send(embed=e,content='El premio ya se guardó. Puedes consultarlo en /recompensas historial.',ephemeral=True)
    async def board(self,i):
        self.guild(i);await i.response.defer(ephemeral=True)
        self.store.scan()
        c=self.store.one('SELECT * FROM reward_cycles WHERE guild=? AND user=?',(i.guild_id,i.user.id)) or {'day':0,'permit_day':None,'cycle':1}
        async with self.render_lock:
            stream=await asyncio.to_thread(visuals.daily,self.store.c,c['day'],c['permit_day'],c['cycle'],self.store.credits(i.user.id))
        text='Las recompensas se entregan automáticamente al aprobar la primera misión del día. Dinero → saldo; permisos → /mercado inventario; rerolls → /recompensas reroll. Sin permisos compatibles verificados, el premio se sustituye por '+ryo(self.store.c['permit_fallback_money'])+'.'
        e=discord.Embed(title='Recompensas de siete días',description=text,color=0xEAC587)
        e.set_image(url='attachment://recompensas.png')
        gift=self.store.one("SELECT id,result,state FROM reward_days WHERE guild=? AND user=? AND cycle=? AND json_extract(result,'$.kind')='permit' ORDER BY id DESC LIMIT 1",(i.guild_id,i.user.id,c['cycle']))
        if gift:
            item=json.loads(gift['result'])['item'];url=await self.icon(item)
            if url:e.set_thumbnail(url=url)
            e.add_field(name='Tu permiso de este ciclo',value=f"{item['name']} · {item['rank']} · {gift['state']}\n/recompensas premio numero:{gift['id']}",inline=False)
        await i.followup.send(embed=e,file=discord.File(stream,'recompensas.png'),ephemeral=True)
    async def icon(self,item):
        overrides=json.loads((self.ctx['BASE']/'config/reward_icons.json').read_text())
        if overrides.get(item['id']):return overrides[item['id']]
        cached=self.store.one('SELECT * FROM reward_icons WHERE item=?',(item['id'],))
        if cached and cached['checked']>self.store.now()-604800:return cached['url']
        try:url=await resolve(item,overrides)
        except Exception:url=None
        with self.market.store.transaction():self.db.execute('INSERT INTO reward_icons VALUES(?,?,?) ON CONFLICT(item) DO UPDATE SET url=excluded.url,checked=excluded.checked',(item['id'],url,self.store.now()))
        return url
    async def history(self,i,page=1,logs=False):
        self.guild(i)
        if logs:self.owner(i)
        page=max(1,page);args=[i.guild_id];where='guild=?'
        if not logs:where+=' AND user=?';args.append(i.user.id)
        rows=self.market.store.rows('SELECT * FROM reward_audit WHERE '+where+' ORDER BY id DESC LIMIT 8 OFFSET ?',args+[(page-1)*8])
        e=discord.Embed(title=('Auditoría' if logs else 'Mis recompensas')+f' · página {page}',color=0xEAC587)
        for r in rows:
            data=json.loads(r['detail']);result=data.get('result')
            label=prize_text(result) if result else (prize_text(data) if r['action']=='spin' else dump_short(data))
            if r['action'].startswith('daily_'):label=f"Premio #{data.get('id','?')} · "+label
            labels={'daily_planned':'Premio reservado','daily_paid':'Premio entregado','daily_cancelled':'Premio cancelado','spin':'Giro de ruleta','reroll_used':'Reroll utilizado','fund':'Capital del fondo','global_registration_reset':'Reinicio global','channel':'Canal configurado'}
            e.add_field(name=f"#{r['id']} · {labels.get(r['action'],r['action'])}",value=f"<t:{r['created']}:f> · {label}"[:1000],inline=False)
        if not rows:e.description='No hay más registros.'
        if i.response.is_done():await i.followup.send(embed=e,view=PageView(self,i.user.id,page,logs),ephemeral=True)
        else:await i.response.send_message(embed=e,view=PageView(self,i.user.id,page,logs),ephemeral=True)
    def install(self):
        roulette=app_commands.Group(name='ruleta',description='Ruleta de World X')
        rewards=app_commands.Group(name='recompensas',description='Premios por actividad y rerolls')
        maintenance=app_commands.Group(name='mantenimiento',description='Acciones exclusivas de BOT_OWNER_ID')
        @roulette.command(name='publicar',description='Dueño: asignar canal y publicar la ruleta')
        async def publish(i:discord.Interaction,canal:discord.TextChannel):
            self.owner(i)
            if canal.guild.id!=i.guild_id:raise ValueError('Canal de otro servidor.')
            p=canal.permissions_for(i.guild.me)
            if not all((p.view_channel,p.send_messages,p.attach_files,p.embed_links)):raise ValueError('El bot necesita ver canal, enviar mensajes, adjuntar archivos e insertar enlaces.')
            await i.response.defer(ephemeral=True)
            async with self.render_lock:stream=await asyncio.to_thread(visuals.wheel,self.store.c)
            e=self.odds();e.set_image(url='attachment://ruleta.png')
            msg=await canal.send(embed=e,file=discord.File(stream,'ruleta.png'),view=RouletteView(self))
            self.store.configure(i.user.id,canal.id)
            await i.followup.send('Ruleta publicada: '+msg.jump_url,ephemeral=True)
        @roulette.command(name='girar',description='Gira en el canal de ruleta por 500.000 Ryo')
        async def spin(i:discord.Interaction):await self.spin(i)
        @roulette.command(name='fondo',description='Consulta saldo del fondo general de premios')
        async def fund_status(i:discord.Interaction):
            self.guild(i);await self.reply(i,'Fondo general: '+ryo(self.store.e.balance(i.guild_id,TREASURY))+'. La ruleta exige 1.500.000 Ryo previos para garantizar cobertura del premio máximo.')
        @roulette.command(name='capital',description='Dueño: financiar fondo; sin aldea, emisión registrada de capital')
        async def fund(i:discord.Interaction,importe:app_commands.Range[int,1,1000000000000],aldea_id:int|None=None):
            self.owner(i)
            if aldea_id is not None:self.store.e.village(i.guild_id,aldea_id)
            self.store.fund(i.user.id,importe,str(i.id),'v:'+str(aldea_id) if aldea_id is not None else 'system')
            await self.reply(i,'Fondo financiado: '+ryo(importe)+'. Origen: '+('tesorería de aldea' if aldea_id is not None else 'emisión de capital auditada')+'.')
        @rewards.command(name='panel',description='Ver tu progreso de siete días y premios')
        async def panel(i:discord.Interaction):await self.board(i)
        @rewards.command(name='historial',description='Consultar premios, giros y entregas paginados')
        async def history(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1):await self.history(i,pagina)
        @rewards.command(name='reroll',description='Consumir 1 reroll: reemplaza nacimiento, 3 giros por categoría')
        async def reroll(i:discord.Interaction):
            self.guild(i);row=self.ctx['ASSIGNMENTS'].store.get(i.guild_id,i.user.id)
            if not row or row['state']['stage']!=3 or self.store.credits(i.user.id)<1:raise ValueError('Necesitas nacimiento terminado y al menos un reroll. Consulta /recompensas panel.')
            await i.response.send_modal(RerollModal(self,i.user.id,row['revision'],self.store.epoch()))
        @rewards.command(name='premio',description='Ver un premio diario y el icono de su técnica')
        async def award(i:discord.Interaction,numero:int):
            self.guild(i);r=self.store.one('SELECT * FROM reward_days WHERE guild=? AND user=? AND id=?',(i.guild_id,i.user.id,numero))
            if not r:raise ValueError('Premio inexistente o ajeno.')
            await i.response.defer(ephemeral=True);p=json.loads(r['result'])
            e=discord.Embed(title=prize_text(p),description=f"Premio #{r['id']} · Día {r['day']} · {r['state']}\n{r['error']}",color=0xEAC587)
            if p['kind']=='permit':
                url=await self.icon(p['item'])
                if url:e.set_thumbnail(url=url)
                e.add_field(name='Fuente',value=p['item'].get('wiki_url','Catálogo World X'),inline=False)
            await i.followup.send(embed=e,ephemeral=True)
        @maintenance.command(name='reiniciar_fichas',description='SOLO DUEÑO: vista previa y borrado global con respaldo')
        async def reset(i:discord.Interaction):
            self.owner(i);token,counts=self.store.preview_reset(i.user.id)
            e=discord.Embed(title='Reinicio GLOBAL de fichas',color=0xD94D59,description=f"Servidor: **{i.guild_id}**\nFichas: **{counts['registrations']}**\nAsignaciones: **{counts['assignments']}**\nReservas únicas históricas: **{counts['birth_history_unique']}**\n\nBorra fichas, capturas de registro referenciadas y asignaciones; libera aldeas y únicos. Conserva saldos, inventarios, deudas, historial económico y premios. Las capturas originales quedan en disco para respaldo. Cancela compras pendientes y bloquea reportes abiertos.\n\nSe crea una copia antes del borrado. Para continuar escribe **BORRAR {i.guild_id}** en la confirmación. Caduca en 5 minutos.")
            await i.response.send_message(embed=e,view=ResetView(self,token),ephemeral=True)
        @maintenance.command(name='recompensas_logs',description='Dueño: auditoría de ruleta, premios y reinicios')
        async def logs(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1):await self.history(i,pagina,True)
        @maintenance.command(name='aldeas',description='Cupos de las aldeas del registro')
        async def villages(i:discord.Interaction):
            self.guild(i);e=discord.Embed(title='World X · Cupos de aldeas',color=0xEAC587)
            for key,label in self.store.c['villages'].items():
                n=self.db.execute('SELECT COUNT(*) FROM wx_village_slots WHERE guild=? AND village=?',(i.guild_id,key)).fetchone()[0]
                e.add_field(name=label,value=f"{n}/{self.store.c['village_cap']}",inline=False)
            await i.response.send_message(embed=e,ephemeral=True)
        for group in (roulette,rewards,maintenance):self.bot.tree.add_command(group)

def dump_short(data):return json.dumps(data,ensure_ascii=False)[:600]
