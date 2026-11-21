"""Discord presentation; URLs only. One persistent view, SQL pagination, durable outbox."""
import asyncio
import copy
import json
import logging
import os
from pathlib import Path
import discord
from discord import app_commands
from .domain import Market, CATEGORIES, dump, rank_label
log=logging.getLogger('worldx.market')
LABELS=dict(zip(CATEGORIES,['Jutsus de elementos','Modos','Sub-modos','Subjutsus','Taijutsus','Jutsus de bloodline','Lanzables','Consumibles','Armas','Objetos','Permisos de razas']))
def short(s,n=1000):return str(s)[:n]
def ryo(n):return f'{n:,}'.replace(',','.')+' Ryo'
class SafeView(discord.ui.View):
    async def on_error(self,i,error,item):
        log.exception('market UI failure',exc_info=error)
        msg=str(error) if isinstance(error,ValueError) else 'Error interno. No repitas un pago; consulta la solicitud.'
        if i.response.is_done():await i.followup.send(msg,ephemeral=True)
        else:await i.response.send_message(msg,ephemeral=True)
class DecisionModal(discord.ui.Modal,title='Resolución del staff'):
    def __init__(self,system,rid,approve,channel):
        super().__init__();self.system=system;self.rid=rid;self.approve=approve;self.channel=channel
        if approve:
            self.reason=discord.ui.TextInput(label='Revisión de pruebas: escribe CONFIRMO',placeholder='Comprueba rango, desbloqueo y nivel del elemento en las capturas. CONFIRMO + observaciones.',min_length=5,max_length=1000,style=discord.TextStyle.paragraph)
        else:
            self.reason=discord.ui.TextInput(label='Motivo y verificación de las capturas',min_length=5,max_length=1000,style=discord.TextStyle.paragraph)
        self.add_item(self.reason)
    async def on_error(self,i,error):
        log.exception('market decision modal failure',exc_info=error)
        await self.system.reply(i,content='No se pudo completar la revisión. Consulta el estado de la solicitud antes de repetir; el detalle está en la consola.')
    async def on_submit(self,i):
        try:
            self.system.staff(i)
            req=self.system.store.req(i.guild_id,self.rid)
            if i.channel_id!=self.channel or i.channel_id!=self.system.channel_id(req['kind']):raise ValueError('Revisa desde el canal correspondiente.')
            await i.response.defer(ephemeral=True)
            await self.system.refresh(i.guild_id,req['actor'],req['target'])
            self.system.staff(i)
            self.system.store.review(i.guild_id,i.user.id,self.rid,self.approve,str(self.reason),manual_verified='CONFIRMO' in str(self.reason).upper().split())
            await i.followup.send('Resolución guardada; inventario y saldo actualizados en una sola transacción.',ephemeral=True)
        except ValueError as e:await self.system.reply(i,content=str(e))
class PersistentActions(SafeView):
    def __init__(self,system):super().__init__(timeout=None);self.system=system
    def request(self,i):
        self.system.guild(i)
        if not i.message or i.message.author.id!=self.system.bot.user.id:raise ValueError('Mensaje no válido.')
        try:rid=int(i.message.embeds[0].footer.text.split('WX-MARKET:')[1])
        except (IndexError,ValueError,AttributeError):raise ValueError('No se encontró la solicitud.')
        return self.system.store.req(i.guild_id,rid)
    @discord.ui.button(label='Aceptar oferta',style=discord.ButtonStyle.primary,custom_id='wxm:accept')
    async def accept(self,i,b):
        r=self.request(i);await i.response.defer(ephemeral=True);await self.system.refresh(i.guild_id,i.user.id);self.system.store.accept(i.guild_id,i.user.id,r['id']);await i.followup.send('Oferta aceptada. Adjunta evidencias con /mercado evidencia.',ephemeral=True)
    @discord.ui.button(label='Cancelar / declinar',custom_id='wxm:cancel')
    async def cancel(self,i,b):
        r=self.request(i);self.system.store.cancel(i.guild_id,i.user.id,r['id']);await i.response.send_message('Cancelada; reservas liberadas.',ephemeral=True)
    @discord.ui.button(label='Aprobar',style=discord.ButtonStyle.success,custom_id='wxm:approve')
    async def approve(self,i,b):
        self.system.staff(i);r=self.request(i)
        if i.channel_id!=self.system.channel_id(r['kind']):raise ValueError('Usa el canal privado de revisión.')
        await i.response.send_modal(DecisionModal(self.system,r['id'],True,i.channel_id))
    @discord.ui.button(label='Rechazar',style=discord.ButtonStyle.danger,custom_id='wxm:reject')
    async def reject(self,i,b):
        self.system.staff(i);r=self.request(i)
        if i.channel_id!=self.system.channel_id(r['kind']):raise ValueError('Usa el canal privado de revisión.')
        await i.response.send_modal(DecisionModal(self.system,r['id'],False,i.channel_id))
class FiltersModal(discord.ui.Modal,title='Buscar y filtrar'):
    name=discord.ui.TextInput(label='Nombre',required=False,max_length=100)
    bloodline=discord.ui.TextInput(label='Bloodline (ID, ejemplo: paper)',required=False,max_length=60)
    element=discord.ui.TextInput(label='Elemento (water, fire, wind...)',required=False,max_length=60)
    race=discord.ui.TextInput(label='Raza: humano / shark / snake / puppet',required=False,max_length=20)
    prices=discord.ui.TextInput(label='Precio normal mínimo,máximo (ej. 0,500000)',required=False,max_length=40)
    def __init__(self,system,filters):super().__init__();self.system=system;self.filters=dict(filters)
    async def on_submit(self,i):
        try:
            for k,obj in [('name',self.name),('bloodline',self.bloodline),('element',self.element),('race',self.race)]:
                val=str(obj).strip();self.filters[k]=val or None
            if str(self.prices).strip():
                lo,hi=str(self.prices).split(',');self.filters['min_price']=int(lo);self.filters['max_price']=int(hi)
                if int(lo)>int(hi):raise ValueError('Precio mínimo mayor que máximo.')
            else:self.filters.pop('min_price',None);self.filters.pop('max_price',None)
            await self.system.shop(i,self.filters)
        except ValueError as e:await i.response.send_message(str(e),ephemeral=True)
class ShopView(SafeView):
    def __init__(self,system,owner,filters,page,rows,total):
        super().__init__(timeout=300);self.system=system;self.owner=owner;self.filters=dict(filters);self.page=page
        cat=discord.ui.Select(placeholder='Categoría',options=[discord.SelectOption(label='Todas las categorías',value='all')]+[discord.SelectOption(label=LABELS[x],value=x) for x in CATEGORIES],row=0)
        async def category(i):
            f=dict(self.filters);f['category']=None if cat.values[0]=='all' else cat.values[0];await system.shop(i,f)
        cat.callback=category;self.add_item(cat)
        rank=discord.ui.Select(placeholder='Rango / tipo',options=[discord.SelectOption(label='Todos los rangos y tipos',value='all')]+[discord.SelectOption(label=rank_label(r),value='rank:'+r) for r in 'DCBAS']+[discord.SelectOption(label='Permisos',value='type:permission'),discord.SelectOption(label='Objetos físicos',value='type:physical')],row=1)
        async def rank_cb(i):
            f=dict(self.filters)
            if rank.values[0]=='all':f.pop('rank',None);f.pop('type',None)
            else:k,v=rank.values[0].split(':');f[k]=v
            await system.shop(i,f)
        rank.callback=rank_cb;self.add_item(rank)
        if rows:
            select=discord.ui.Select(placeholder='Ver artículo y solicitar compra',options=[discord.SelectOption(label=x['name'][:100],value=x['id'],description=(rank_label(x['rank'])+' · '+ryo(x['price']))[:100]) for x in rows],row=2)
            async def selected(i):await system.detail(i,select.values[0])
            select.callback=selected;self.add_item(select)
        for label,delta in [('◀',-1),('▶',1)]:
            b=discord.ui.Button(label=label,row=3,disabled=(delta<0 and page==1) or (delta>0 and page*8>=total))
            async def turn(i,delta=delta):await system.shop(i,self.filters,self.page+delta)
            b.callback=turn;self.add_item(b)
        b=discord.ui.Button(label='Buscar / más filtros',row=3)
        async def modal(i):await i.response.send_modal(FiltersModal(system,self.filters))
        b.callback=modal;self.add_item(b)
        b=discord.ui.Button(label='Limpiar',row=3)
        async def clear(i):await system.shop(i,{})
        b.callback=clear;self.add_item(b)
    async def interaction_check(self,i):
        self.system.guild(i)
        if i.user.id!=self.owner:await i.response.send_message('Abre tu propia tienda.',ephemeral=True);return False
        return True
class MarketSystem:
    def __init__(self,bot,ctx):
        self.bot=bot;self.ctx=ctx;self.base=Path(__file__).resolve().parent.parent
        self.config=json.loads((self.base/'config/market/settings.json').read_text())
        for kind in self.config['channels']:
            self.config['channels'][kind]=int(os.getenv('MARKET_'+kind.upper()+'_CHANNEL_ID',str(self.config['channels'][kind])))
        for key,env in [('review_role_ids','MARKET_REVIEW_ROLE_IDS'),('admin_role_ids','MARKET_ADMIN_ROLE_IDS')]:
            if os.getenv(env):self.config[key]=[int(x.strip()) for x in os.environ[env].split(',') if x.strip()]
        self.store=Market(ctx['ECONOMY'].store,self.config)
        self.load();self.view=PersistentActions(self);self.task=None;self.harden();self.install()
    def actions_view(self):
        """Vista independiente por mensaje; self.view solo registra el respaldo."""
        view=PersistentActions(self)
        existing={item.custom_id for item in view.children}
        for item in self.view.children:
            if item.custom_id not in existing:
                # Las extensiones (Pedir corrección) usan callbacks del sistema.
                view.add_item(copy.copy(item))
        return view
    def load(self):return self.store.load_catalog([self.base/'config/market'/p for p in self.config['catalog_files']])
    def guild(self,i):
        if i.guild_id!=self.ctx['GUILD'] or not isinstance(i.user,discord.Member):raise ValueError('Usa el servidor World X configurado.')
    def staff(self,i):
        self.guild(i)
        if not self.is_staff(i.user):raise ValueError('Solo el rol explícito de revisión puede resolver solicitudes.')
    def is_staff(self,member):return bool({r.id for r in member.roles}&set(self.config['review_role_ids'])) or member.id==self.ctx['BOT_OWNER'] or (self.ctx.get('REVIEWER') and any(r.id==self.ctx['REVIEWER'] for r in member.roles)) or getattr(member,'guild_permissions',None) and member.guild_permissions.manage_guild
    def admin(self,i):
        self.guild(i)
        if i.user.id!=self.ctx['BOT_OWNER'] and not ({r.id for r in i.user.roles}&set(self.config['admin_role_ids'])) and not i.user.guild_permissions.manage_guild:raise ValueError('Solo administración del mercado.')
    def channel_id(self,kind):return self.config['channels'][kind]
    def channel(self,g,kind):
        guild=self.bot.get_guild(g);ch=guild.get_channel(self.channel_id(kind)) if guild else None
        if not isinstance(ch,discord.TextChannel):raise ValueError('Configura MARKET_'+kind.upper()+'_CHANNEL_ID con un canal de texto.')
        if ch.permissions_for(guild.default_role).view_channel:raise ValueError('El canal '+kind+' debe ser privado para @everyone.')
        # Detect explicitly allowed non-review roles; do not silently expose evidence.
        allowed=set(self.config['review_role_ids']+self.config['admin_role_ids'])
        for target,over in ch.overwrites.items():
            if over.view_channel is True:
                if isinstance(target,discord.Role) and target.id not in allowed and not target.permissions.administrator and not (target.is_bot_managed() and target.tags.bot_id==self.bot.user.id):raise ValueError('Un rol no autorizado tiene acceso al canal '+kind)
                if isinstance(target,discord.Member) and target.id not in (self.bot.user.id,self.ctx['BOT_OWNER']) and not self.is_staff(target) and not target.guild_permissions.administrator:raise ValueError('Un usuario no autorizado tiene acceso al canal '+kind)
        perms=ch.permissions_for(guild.me)
        if not all((perms.view_channel,perms.send_messages,perms.embed_links,perms.read_message_history,perms.attach_files)):raise ValueError('Faltan permisos del bot en '+kind)
        for rid in self.config['review_role_ids']:
            role=guild.get_role(rid)
            if role is None or not ch.permissions_for(role).view_channel:raise ValueError('El rol revisor no puede ver '+kind)
        return ch
    async def refresh(self,g,*users):
        for u in set(users):await self.ctx['ECONOMY'].sync(g,u)
    async def reply(self,i,**kw):
        kw.setdefault('ephemeral',True)
        if i.response.is_done():return await i.followup.send(**kw)
        return await i.response.send_message(**kw)
    def harden(self):
        e=self.store.e
        # Guard raw ledger too: old v7 automatic debt collection must respect holds.
        old_raw=e._raw_move if hasattr(e,'_raw_move') else e._move
        def guarded(g,ref,actor,reason,entries):
            entries=list(entries);totals={}
            for key,delta in entries:totals[key]=totals.get(key,0)+delta
            for key,delta in totals.items():
                if key.startswith('u:') and delta<0 and self.store.available(g,int(key[2:]))+delta<0:raise ValueError('Fondos reservados por solicitudes del mercado.')
            return old_raw(g,ref,actor,reason,entries)
        if hasattr(e,'_raw_move'):e._raw_move=guarded
        else:e._move=guarded
        def closed(*a,**k):raise ValueError('Tienda antigua desactivada. Usa /mercado; toda adquisición requiere revisión.')
        for method in ['buy','propose','accept','review_teaching','grant','revoke']:setattr(e,method,closed)
    def restore(self):
        self.bot.add_view(self.view)
        if not self.task:self.task=asyncio.create_task(self.worker())
    async def worker(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                self.store.expire();await self.flush()
            except Exception:log.exception('Market outbox retry on next tick')
            await asyncio.sleep(30)
    def request_embed(self,r):
        snap=json.loads(r['snapshot']);ev=json.loads(r['evidence']);it=snap.get('item',{})
        em=discord.Embed(title=f"Solicitud #{r['id']} · {r['kind']} · {r['state']}",description=short(it.get('name','Préstamo de dinero')),color=0xC4A76B)
        em.add_field(name='Participantes',value=f"Origen: <@{r['actor']}>\nDestino: <@{r['target']}>")
        em.add_field(name='Importe reservado',value=ryo(r['price']))
        em.add_field(name='Caduca',value=f"<t:{r['expires']}:R>")
        if snap.get('manual_review'):
            em.add_field(name='Verificación manual obligatoria',value='Comprueba desbloqueo y niveles en las capturas. El nivel del personaje NO prueba el del elemento. Para aprobar escribe CONFIRMO en el motivo.',inline=False)
        if snap.get('missing'):em.add_field(name='Precio ×3 por rango',value=short('; '.join(snap['missing'])),inline=False)
        if it:
            em.add_field(name='Requisitos: comprobar todos',value=short('\n'.join(x['label']+' ['+x['policy']+']' for x in it['requirements'])),inline=False)
            em.add_field(name='Condiciones de origen',value=short(it.get('notes',it['source'])),inline=False)
            if it.get('image_url'):em.set_thumbnail(url=it['image_url'])
        if ev:
            em.add_field(name='Evidencias (mensajes originales)',value=short('\n'.join(f"<@{x['user']}> [captura {j+1}]({x['message']})" for j,x in enumerate(ev))),inline=False)
            em.set_image(url=ev[-1]['url'])
        else:em.add_field(name='Evidencias',value='Pendientes: /mercado evidencia',inline=False)
        if snap.get('funding'):
            f=snap['funding'];em.add_field(name='Procedencia comprobada al solicitar',value=f"Saldo: {ryo(f['balance'])} · misiones pagadas: {f['mission_reports']}\nIngresos de misiones: {ryo(f['mission_income'])}. Detalle: /mercado historial.",inline=False)
        if r['reason']:em.add_field(name='Resolución',value=short(r['reason']),inline=False)
        em.set_footer(text='WX-MARKET:'+str(r['id']));return em
    async def flush(self):
        g=self.ctx['GUILD']
        for r in self.store.rows('SELECT * FROM market_requests WHERE guild=? AND dirty=1 ORDER BY id LIMIT 10',(g,)):
            try:
                ch=self.channel(g,r['kind']);msg=None
                if r['review_message'] and r['review_channel']==ch.id:
                    try:msg=await ch.fetch_message(r['review_message'])
                    except discord.NotFound:pass
                if msg:await msg.edit(embed=self.request_embed(r),view=self.actions_view())
                else:msg=await ch.send(embed=self.request_embed(r),view=self.actions_view(),allowed_mentions=discord.AllowedMentions.none())
                with self.store.transaction():self.store.db.execute('UPDATE market_requests SET review_channel=?,review_message=?,dirty=CASE WHEN state=? AND evidence=? THEN 0 ELSE 1 END WHERE id=?',(ch.id,msg.id,r['state'],r['evidence'],r['id']))
            except (ValueError,discord.HTTPException):log.exception('Unable to deliver market request %s',r['id'])
        for a in self.store.rows('SELECT * FROM market_audit WHERE guild=? AND posted=0 ORDER BY id LIMIT 10',(g,)):
            try:
                ch=self.channel(g,'audit')
                await ch.send(embed=discord.Embed(title=f"Registro #{a['id']} · {a['action']}",description=short(f"Actor: <@{a['actor']}> · solicitud: {a['request']}\n{a['detail']}",3500),color=0xC4A76B),allowed_mentions=discord.AllowedMentions.none())
                with self.store.transaction():self.store.db.execute('UPDATE market_audit SET posted=1 WHERE id=?',(a['id'],))
            except (ValueError,discord.HTTPException):log.exception('Unable to deliver market audit %s',a['id']);break
    async def shop(self,i,filters=None,page=1):
        self.guild(i);rows,total,page=self.store.search(filters,page)
        em=discord.Embed(title='World X | Hardcore · Tienda',description='Precio del permiso; costes del juego aparte. ×3 solo para requisitos marcados x3.',color=0xC4A76B)
        for r in rows:em.add_field(name=short(r['emoji']+' '+r['name'],240),value=f"{LABELS[r['category']]} · {rank_label(r['rank'])}\nNormal: **{ryo(r['price'])}** · ×3: {ryo(r['price_x3'])}",inline=False)
        if not rows:em.description='No hay resultados para estos filtros.'
        em.set_footer(text=f'Página {page}/{max(1,(total+7)//8)} · {total} artículos · filtro de precio normal')
        view=ShopView(self,i.user.id,filters or {},page,rows,total)
        if i.type==discord.InteractionType.component and i.message and i.message.flags.ephemeral:await i.response.edit_message(embed=em,view=view)
        else:await self.reply(i,embed=em,view=view)
    async def detail(self,i,ident):
        self.guild(i);it=self.store.item(ident)
        em=discord.Embed(title=short(it['emoji']+' '+it['name'],250),description=short(it.get('notes',it['source']),2000),color=0xC4A76B)
        em.add_field(name='Permiso',value=f"Normal: {ryo(it['price'])}\nFuera de rango: {ryo(it['price_x3'])}")
        em.add_field(name='Requisitos',value=short('\n'.join(x['label']+' ['+x['policy']+']' for x in it['requirements'])),inline=False)
        source=it.get('source_values',{})
        if source:em.add_field(name='Fuente HTML · requisitos nativos aparte',value=short('Nivel: '+source['level']+' · Stats: '+source['stats']+'\nCoste del juego: '+source['native_cost']+'\nAdquisición: '+source['acquisition']),inline=False)
        if it.get('image_url'):em.set_image(url=it['image_url'])
        try:q=self.store.quote(i.guild_id,i.user.id,it);em.add_field(name='Tu precio',value=ryo(q['price']))
        except ValueError as e:em.add_field(name='Pendiente para comprar',value=short(e))
        em.set_footer(text=it['id'])
        v=SafeView(timeout=180);b=discord.ui.Button(label='Solicitar compra',style=discord.ButtonStyle.success)
        async def buy(inter):
            self.guild(inter)
            if inter.user.id!=i.user.id:raise ValueError('Abre tu propia tienda.')
            self.channel(inter.guild_id,'purchase')
            await inter.response.defer(ephemeral=True)
            await self.refresh(inter.guild_id,inter.user.id)
            rid=self.store.purchase(inter.guild_id,inter.user.id,ident,str(inter.id))
            await inter.followup.send(f'Solicitud #{rid} creada. Saldo reservado, aún no descontado. Usa /mercado evidencia para adjuntar capturas.',ephemeral=True)
        b.callback=buy;v.add_item(b);await self.reply(i,embed=em,view=v)
    async def listing(self,i,kind,page=1,user=None):
        self.guild(i);u=user or i.user.id
        if u!=i.user.id and kind!='inventory':self.staff(i)
        page=max(1,page);offset=(page-1)*8;g=i.guild_id
        if kind=='inventory':
            rows=self.store.rows("SELECT * FROM market_inventory WHERE guild=? AND user=? AND state='owned' ORDER BY id DESC LIMIT 8 OFFSET ?",(g,u,offset))
            texts=[f"**#{r['id']} · {json.loads(r['snapshot'])['name']}**\n{LABELS.get(r['category'],r['category'])} · {r['origin']} · <t:{r['approved_at']}:d>" for r in rows]
            member=i.guild.get_member(u) if i.guild else None
            title=f'🎒 Inventario de {member.display_name}' if member and user else '🎒 Inventario del mercado'
        elif kind=='logs':
            self.staff(i);rows=self.store.rows('SELECT * FROM market_audit WHERE guild=? ORDER BY id DESC LIMIT 8 OFFSET ?',(g,offset));texts=[f"#{r['id']} · {r['action']} · <@{r['actor']}>\n{short(r['detail'],250)}" for r in rows];title='Auditoría'
        elif kind=='loans':
            rows=self.store.rows('SELECT * FROM market_loans WHERE guild=? AND (lender=? OR borrower=?) ORDER BY request DESC LIMIT 8 OFFSET ?',(g,u,u,offset));texts=[f"#{r['request']} · <@{r['lender']}> → <@{r['borrower']}>\nPendiente {ryo(r['principal']-r['repaid'])} · vence <t:{r['due']}:d>" for r in rows];title='Préstamos (sin intereses)'
        elif kind=='requests':
            rows=self.store.rows('SELECT * FROM market_requests WHERE guild=? AND (actor=? OR target=?) ORDER BY id DESC LIMIT 8 OFFSET ?',(g,u,u,offset));texts=[f"#{r['id']} · {r['kind']} · {r['state']} · {ryo(r['price'])}" for r in rows];title='Solicitudes'
        else:
            p=self.store.provenance(g,u,page);rows=p['entries'];texts=[f"TX #{r['id']} · {ryo(r['delta'])}\n{short(r['ref'],100)} · {short(r['reason'],160)}" for r in rows];title=f"Cartera {ryo(p['balance'])} · reservas {ryo(p['reserved'])}"
        em=discord.Embed(title=short(title,250),description='\n\n'.join(texts)[:3900] or 'Sin registros.',color=0xC4A76B);em.set_footer(text=f'Página {page}')
        if kind=='inventory':
            em.add_field(name='💰 Dinero',value=ryo(self.store.e.balance(g,f'u:{u}')),inline=True)
            if 'REWARDS' in self.ctx:
                em.add_field(name='🎲 Rerolls',value=str(self.ctx['REWARDS'].store.credits(u)),inline=True)
            if 'MISSIONS' in self.ctx:
                totals=self.ctx['MISSIONS'].store.totals(g,u)
                em.add_field(
                    name='📚 Misiones completadas',
                    value=' · '.join(f'**{r}:** {int(totals.get(r,0))}' for r in ('D','C','B','A','S'))+f' · **Total:** {sum(int(totals.get(r,0)) for r in ("D","C","B","A","S"))}',
                    inline=False
                )
        if kind=='history' and 'MISSIONS' in self.ctx:
            totals=self.ctx['MISSIONS'].store.totals(g,u)
            em.add_field(
                name='📚 Misiones aprobadas por rango',
                value=' · '.join(f'**{r}:** {int(totals.get(r,0))}' for r in ('D','C','B','A','S'))+f' · **Total:** {sum(int(totals.get(r,0)) for r in ("D","C","B","A","S"))}',
                inline=False
            )
        v=SafeView(timeout=300)
        for label,delta in [('◀',-1),('▶',1)]:
            b=discord.ui.Button(label=label,disabled=(page==1 and delta<0) or (len(rows)<8 and delta>0))
            async def cb(inter,delta=delta):
                if inter.user.id!=i.user.id:raise ValueError('Abre tu propia lista.')
                await self.listing(inter,kind,page+delta,u)
            b.callback=cb;v.add_item(b)
        if i.type==discord.InteractionType.component and i.message and i.message.flags.ephemeral:await i.response.edit_message(embed=em,view=v)
        else:await self.reply(i,embed=em,view=v)
    def install(self):
        group=app_commands.Group(name='mercado',description='Tienda, permisos y revisión World X',guild_only=True)
        @group.command(name='tienda',description='Catálogo con filtros, búsqueda y paginación')
        async def shop(i:discord.Interaction):await self.shop(i)
        @group.command(name='buscar',description='Buscar por nombre y filtros combinables')
        async def search(i:discord.Interaction,nombre:str='',categoria:str='',tipo:str='',raza:str='',bloodline:str='',elemento:str='',rango:str='',minimo:app_commands.Range[int,0,10**12]=0,maximo:app_commands.Range[int,0,10**12]=10**12):
            if minimo>maximo:raise ValueError('Mínimo mayor que máximo.')
            await self.shop(i,dict(name=nombre,category=categoria,type=tipo,race=raza,bloodline=bloodline,element=elemento,rank=rango.upper(),min_price=minimo,max_price=maximo))
        @group.command(name='comprar',description='Ver un artículo por ID y solicitar su permiso')
        async def buy(i:discord.Interaction,item_id:str):await self.detail(i,item_id)
        @buy.autocomplete('item_id')
        async def autocomplete(i,current):
            rows,_,_=self.store.search({'name':current},size=20);return [app_commands.Choice(name=short(r['name'],100),value=r['id']) for r in rows]
        @group.command(name='inventario',description='Tu inventario aprobado, origen y fecha')
        async def inv(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1):await self.listing(i,'inventory',pagina)
        @group.command(name='historial',description='Libro contable y procedencia; staff puede consultar otro usuario')
        async def history(i:discord.Interaction,usuario:discord.Member|None=None,pagina:app_commands.Range[int,1,100000]=1):await self.listing(i,'history',pagina,usuario.id if usuario else None)
        @group.command(name='logs',description='Auditoría paginada del staff')
        async def logs(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1):await self.listing(i,'logs',pagina)
        @group.command(name='solicitudes',description='Ver tus solicitudes y ofertas recibidas')
        async def requests(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1):await self.listing(i,'requests',pagina)
        @group.command(name='solicitud',description='Consultar, aceptar o cancelar una solicitud')
        async def request(i:discord.Interaction,numero:int):
            self.guild(i);r=self.store.req(i.guild_id,numero)
            if i.user.id not in (r['actor'],r['target']):self.staff(i)
            await self.reply(i,embed=self.request_embed(r),view=self.actions_view())
        @group.command(name='evidencia',description='Adjuntar una captura para revisión privada del staff')
        async def evidence(i:discord.Interaction,solicitud:int,foto:discord.Attachment):
            self.guild(i);r=self.store.req(i.guild_id,solicitud)
            if i.user.id not in (r['actor'],r['target']) or r['state'] not in ('pending','offered'):raise ValueError('Solicitud ajena o cerrada.')
            if foto.content_type not in ('image/png','image/jpeg','image/webp') or foto.size>8*1024*1024:raise ValueError('Adjunta PNG/JPG/WEBP de hasta 8 MB.')
            ch=self.channel(i.guild_id,r['kind']);await i.response.defer(ephemeral=True)
            em=discord.Embed(title=f'Evidencia solicitud #{solicitud}',description=f'Enviada por <@{i.user.id}>');em.set_image(url=foto.url)
            msg=await ch.send(embed=em,allowed_mentions=discord.AllowedMentions.none())
            self.store.evidence(i.guild_id,i.user.id,solicitud,foto.url,msg.jump_url)
            await i.followup.send('Captura enlazada en el canal privado de revisión. Conserva el adjunto original hasta la resolución.',ephemeral=True)
        @group.command(name='tradeo',description='Ofrecer un permiso/objeto propio a cambio de Ryo; requiere aceptación y staff')
        async def trade(i:discord.Interaction,lote:int,destinatario:discord.Member,precio:app_commands.Range[int,0,10**12]):
            self.guild(i);self.channel(i.guild_id,'trade')
            if destinatario.bot:raise ValueError('El destinatario debe ser un jugador.')
            await i.response.defer(ephemeral=True)
            await self.refresh(i.guild_id,i.user.id,destinatario.id)
            rid=self.store.offer(i.guild_id,i.user.id,destinatario.id,'trade',str(i.id),lot=lote,amount=precio)
            await self.reply(i,content=f'Oferta #{rid}. El destinatario debe abrir /mercado solicitud {rid} y aceptarla. Después ambos aportan evidencia.')
        @group.command(name='ensenar',description='Enseñar una técnica propia; alumno y staff deben aprobar')
        async def teach(i:discord.Interaction,lote:int,alumno:discord.Member):
            self.guild(i);self.channel(i.guild_id,'teaching')
            if alumno.bot:raise ValueError('El alumno debe ser un jugador.')
            await i.response.defer(ephemeral=True)
            await self.refresh(i.guild_id,i.user.id,alumno.id)
            rid=self.store.offer(i.guild_id,i.user.id,alumno.id,'teaching',str(i.id),lot=lote)
            await self.reply(i,content=f'Enseñanza #{rid}; alumno: /mercado solicitud {rid}. Tarifa fijada en el catálogo; el maestro conserva la técnica.')
        @group.command(name='prestar',description='Ofrecer un préstamo sin intereses, con plazo y consentimiento')
        async def lend(i:discord.Interaction,destinatario:discord.Member,importe:app_commands.Range[int,1,10**12],dias:app_commands.Range[int,1,90]=7):
            self.guild(i);self.channel(i.guild_id,'loan')
            if destinatario.bot:raise ValueError('El destinatario debe ser un jugador.')
            await i.response.defer(ephemeral=True)
            await self.refresh(i.guild_id,i.user.id,destinatario.id)
            rid=self.store.offer(i.guild_id,i.user.id,destinatario.id,'loan',str(i.id),amount=importe,days=dias)
            await self.reply(i,content=f'Préstamo #{rid}; destinatario: /mercado solicitud {rid}. Sin débito hasta aceptación y revisión. Adjunta prueba del acuerdo.')
        @group.command(name='devolver',description='Devolver total o parcialmente un préstamo')
        async def repay(i:discord.Interaction,solicitud:int,importe:app_commands.Range[int,1,10**12]):
            self.guild(i);self.store.repay(i.guild_id,i.user.id,solicitud,importe,str(i.id));await self.reply(i,content='Devolución registrada.')
        @group.command(name='prestamos',description='Ver tus préstamos y sus vencimientos')
        async def loans(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1):await self.listing(i,'loans',pagina)
        @group.command(name='usar',description='Marcar un consumible/lanzable como utilizado')
        async def consume(i:discord.Interaction,lote:int):
            self.guild(i);self.store.consume(i.guild_id,i.user.id,lote);await self.reply(i,content='Consumo registrado. El historial se conserva.')
        @group.command(name='perfil',description='Staff: verificar ficha mediante JSON editable, sin autoasignación')
        async def profile(i:discord.Interaction,usuario:discord.Member,archivo:discord.Attachment):
            self.staff(i)
            if archivo.size>16384:raise ValueError('Perfil JSON máximo 16 KB.')
            await i.response.defer(ephemeral=True)
            try:data=json.loads(await archivo.read())
            except (ValueError,UnicodeDecodeError):raise ValueError('JSON inválido.')
            self.store.set_profile(i.guild_id,i.user.id,usuario.id,data);await i.followup.send('Ficha verificada y auditada.',ephemeral=True)
        @group.command(name='ver_perfil',description='Ver tu ficha de requisitos verificada')
        async def showprofile(i:discord.Interaction):
            self.guild(i);p=self.store.profile(i.guild_id,i.user.id)
            await self.reply(i,content='```json\n'+short(json.dumps(p,ensure_ascii=False,indent=2),1850)+'\n```')
        @group.command(name='recargar',description='Administración: validar y recargar catálogo editable')
        async def reload(i:discord.Interaction):
            self.admin(i);n=self.load()
            with self.store.transaction():self.store.audit(i.guild_id,i.user.id,'catalog_reloaded',{'items':n})
            await self.reply(i,content=f'{n} artículos validados. Solicitudes cuyo artículo cambió requieren recrearse.')
        @group.command(name='diagnostico',description='Administración: comprobar los canales y conciliación')
        async def diagnose(i:discord.Interaction):
            self.admin(i);lines=[]
            for kind in self.config['channels']:
                try:ch=self.channel(i.guild_id,kind);lines.append('✅ '+kind+' '+ch.mention)
                except ValueError as e:lines.append('❌ '+str(e))
            await self.reply(i,content='\n'.join(lines))
        legacy=self.bot.tree.remove_command('mercado')
        if legacy:
            legacy.name='mercado_anterior'
            self.bot.tree.add_command(legacy)
        self.bot.tree.add_command(group)

        @self.bot.tree.command(name='inventario',description='Ver inventario, dinero y misiones completadas (tuyo o de otro jugador)')
        @app_commands.guild_only()
        async def public_inventory(i:discord.Interaction,usuario:discord.Member|None=None,pagina:app_commands.Range[int,1,100000]=1):
            await self.listing(i,'inventory',pagina,usuario.id if usuario else None)

        @self.bot.tree.command(name='trade',description='Tradear o prestar un objeto, arma, lanzable o consumible de tu inventario')
        @app_commands.guild_only()
        async def simple_trade(i:discord.Interaction,usuario:discord.Member,objeto:int,motivo:str='Trade / préstamo entre jugadores'):
            self.guild(i)
            if usuario.bot:raise ValueError('No puedes tradear con un bot.')
            res=self.store.trade_direct(i.guild_id,i.user.id,usuario.id,objeto,motivo)
            em=discord.Embed(
                title='🤝 Trade completado',
                description=f'**{res["name"]}** (`#{res["lot"]}`) fue transferido de <@{i.user.id}> a <@{usuario.id}>.\n**Motivo:** {motivo}',
                color=0x388E63
            )
            await self.reply(i,embed=em)
        @simple_trade.autocomplete('objeto')
        async def simple_trade_ac(i:discord.Interaction,current:str):
            rows=self.store.rows(
                "SELECT id,item,category,snapshot FROM market_inventory WHERE guild=? AND user=? AND state='owned' AND locked_by IS NULL ORDER BY id DESC LIMIT 40",
                (i.guild_id,i.user.id)
            )
            out=[]
            for r in rows:
                snap=json.loads(r['snapshot'])
                if r['category'] not in ('weapons','throwables','consumables','objects') and snap.get('type')!='physical' and not snap.get('tradable'):
                    continue
                label=f"#{r['id']} · {snap.get('name',r['item'])} ({LABELS.get(r['category'],r['category'])})"
                if not current or current.lower() in label.lower():
                    out.append(app_commands.Choice(name=short(label,100),value=r['id']))
                if len(out)>=25:break
            return out

        @self.bot.tree.command(name='quitar_inventario',description='Staff: eliminar un objeto o permiso del inventario de un jugador')
        @app_commands.guild_only()
        async def staff_revoke_inv(i:discord.Interaction,usuario:discord.Member,objeto:int,motivo:str='Eliminado por Staff'):
            self.staff(i)
            res=self.store.revoke_inventory(i.guild_id,i.user.id,usuario.id,objeto,motivo)
            await self.reply(i,content=f'🗑️ Se eliminó **{res["name"]}** (`#{res["lot"]}`) del inventario de <@{usuario.id}>.\n**Motivo:** {motivo}')
        @staff_revoke_inv.autocomplete('objeto')
        async def staff_revoke_ac(i:discord.Interaction,current:str):
            if not self.is_staff(i.user):return []
            target=getattr(i.namespace,'usuario',None)
            uid=target.id if hasattr(target,'id') else (int(target) if target else 0)
            if not uid:return []
            rows=self.store.rows(
                "SELECT id,item,category,snapshot FROM market_inventory WHERE guild=? AND user=? AND state='owned' ORDER BY id DESC LIMIT 40",
                (i.guild_id,uid)
            )
            out=[]
            for r in rows:
                snap=json.loads(r['snapshot'])
                label=f"#{r['id']} · {snap.get('name',r['item'])} ({LABELS.get(r['category'],r['category'])})"
                if not current or current.lower() in label.lower():
                    out.append(app_commands.Choice(name=short(label,100),value=r['id']))
                if len(out)>=25:break
            return out
