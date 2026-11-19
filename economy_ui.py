"""Discord controls for economy v6.0. All authorization is checked on execution."""
from i18n import bilingual, format_fragment, number, t
from content_i18n import content_text
from panels_i18n import panel_embeds
from discord_i18n import audit_output
import asyncio
import json
import logging
import time
from typing import Literal
import discord
from discord import app_commands
from economy_domain import Economy, RANKS, TIERS, PHYSICAL, MISSION_REWARDS

log=logging.getLogger('worldx.economy')
def money(n):return number(n)+' Ryo'
def safe(s):return discord.utils.escape_mentions(discord.utils.escape_markdown(str(s)))

class EconomySystem:
    def __init__(self,bot,ctx):
        self.bot=bot;self.ctx=ctx;self.store=Economy(ctx['STORE'],ctx['MISSIONS'].store)
        from worldx_v7 import install_core
        install_core(self.store)
        self.next_tick=0;self.lock=asyncio.Lock();self.install()
        from mission_payment_ui import MissionPayments
        self.payments=MissionPayments(self)
    def guild(self,i):
        if i.guild_id!=self.ctx['GUILD'] or not isinstance(i.user,discord.Member):raise ValueError(t('Usa el servidor configurado.'))
        if self.ctx['is_demo']() and not self.ctx['staff'](i):raise ValueError(t('El bot está en pruebas privadas del staff.'))
    def admin(self,i):
        self.guild(i)
        if not (self.ctx['owner'](i) or i.user.guild_permissions.manage_guild):raise ValueError(t('Solo administración puede configurar la economía.'))
    def staff(self,i):
        self.guild(i)
        if not self.ctx['staff'](i):raise ValueError(t('Solo moderación puede revisar enseñanzas.'))
    def kage(self,i,v):
        self.guild(i);village=self.store.village(i.guild_id,v)
        if self.ctx['owner'](i) or i.user.guild_permissions.manage_guild:return
        roles={r.id for r in i.user.roles}
        if village['kage_role'] not in roles or village['role'] not in roles:raise ValueError(t('Solo el Kage de esta aldea o administración puede autorizar su pago.'))
    async def sync(self,g,user):
        guild=self.bot.get_guild(g)
        if guild is None:raise ValueError(t('Servidor temporalmente no disponible.'))
        try:member=await guild.fetch_member(user)
        except discord.NotFound:return self.store.observe(g,user,[],False)
        return self.store.observe(g,user,[r.id for r in member.roles],not member.bot)
    async def player(self,i):
        self.guild(i);await self.sync(i.guild_id,i.user.id);return self.store.player(i.guild_id,i.user.id)
    async def reply(self,i,text=None,**kw):
        if i.response.is_done():return await i.followup.send(text,ephemeral=True,**kw)
        return await i.response.send_message(text,ephemeral=True,**kw)
    def confirmation(self,actor,action):
        system=self
        class Confirm(self.ctx['SafeView']):
            def __init__(self):super().__init__(timeout=180);self.done=False
            async def interaction_check(self,i):
                if i.user.id!=actor:
                    await system.reply(i,t('Esta confirmación pertenece a otra persona.'));return False
                return True
            @discord.ui.button(label='Confirmar',style=discord.ButtonStyle.success)
            async def yes(self,i,b):
                if self.done:raise ValueError(t('Esta confirmación ya se utilizó.'))
                await i.response.defer(ephemeral=True)
                async with system.lock:
                    if self.done:raise ValueError(t('Esta confirmación ya se utilizó.'))
                    await action(i);self.done=True
                for child in self.children:child.disabled=True
                await i.edit_original_response(view=self)
            @discord.ui.button(label='Cancelar',style=discord.ButtonStyle.secondary)
            async def no(self,i,b):
                self.done=True
                for child in self.children:child.disabled=True
                await i.response.edit_message(content=t('Operación cancelada.'),embed=None,view=self)
        return Confirm()
    def home(self):
        system=self
        class Panel(self.ctx['SafeView']):
            def __init__(self):super().__init__(timeout=None)
            @discord.ui.button(label=bilingual('🏪 Tienda'),custom_id='worldx:eco:shop')
            async def shop(self,i,b):system.guild(i);await system.shop(i)
            @discord.ui.button(label=bilingual('🎒 Inventario'),custom_id='worldx:eco:inventory')
            async def inv(self,i,b):system.guild(i);await system.inventory(i)
            @discord.ui.button(label=bilingual('💰 Tarjeta y cartera'),custom_id='worldx:eco:balance')
            async def balance(self,i,b):
                from ninja_card import show
                await show(system,i)
            @discord.ui.button(label=bilingual('🤝 Mercado'),custom_id='worldx:eco:market')
            async def market(self,i,b):system.guild(i);await system.deals(i,'market')
        return Panel()
    def restore(self):
        self.bot.add_view(self.home())
        for suffix in (':v1', ':v2'):
            v = self.ctx['SafeView'](timeout=None)
            async def _shop(i): self.guild(i); await self.shop(i)
            async def _inv(i): self.guild(i); await self.inventory(i)
            async def _bal(i):
                from ninja_card import show
                await show(self, i)
            async def _mkt(i): self.guild(i); await self.deals(i, 'market')
            for cid, cb in [('shop', _shop), ('inventory', _inv), ('balance', _bal), ('market', _mkt)]:
                b = discord.ui.Button(label=cid, custom_id=f'worldx:eco:{cid}{suffix}')
                b.callback = cb
                v.add_item(b)
            self.bot.add_view(v)
    async def shop(self,i,page=1,category=0,village=0,rank='',search=''):
        self.guild(i);sql='SELECT id FROM eco_items WHERE guild=? AND active=1';args=[i.guild_id]
        if category:sql+=' AND category=?';args.append(category)
        if village:sql+=' AND village=?';args.append(village)
        items=[self.store.item(i.guild_id,r['id']) for r in self.store.rows(sql+' ORDER BY id',args)]
        items=[r for r in items if (not rank or r['data']['rank']==rank) and search.lower() in (r['data']['name']+' '+content_text(r['data'])).lower()]
        pages=max(1,(len(items)+9)//10);page=max(1,min(page,pages));batch=items[(page-1)*10:page*10]
        embed=discord.Embed(title=t('🏪 Mercado de suministros · TIENDA'),color=0x388E63,description=t('Compra permisos y suministros con Ryo. Las ventas entre jugadores están en /mercado.\n\n')+'\n'.join(f"**#{r['id']} {safe(r['data'].get('emoji',''))} {safe(content_text(r['data']))}** · {r['data']['rank']} · {money(r['data']['price'])}" for r in batch))
        embed.set_footer(text=''.join([format_fragment(t('Página '), '', -1), format_fragment(page, '', -1), '/', format_fragment(pages, '', -1), format_fragment(t(' · /tienda ver permite filtrar categoría, aldea, rango y nombre'), '', -1)]))
        system=self
        class Shop(self.ctx['SafeView']):
            def __init__(self):
                super().__init__(timeout=600)
                if batch:
                    sel=discord.ui.Select(placeholder=t('Ver artículo y requisitos'),options=[discord.SelectOption(label=content_text(r['data'])[:100],value=str(r['id']),description=''.join([format_fragment(t('Rango '), '', -1), format_fragment(r['data']['rank'], '', -1), ' · ', format_fragment(money(r['data']['price']), '', -1)])) for r in batch])
                    async def select(inter):await system.show_item(inter,int(sel.values[0]))
                    sel.callback=select;self.add_item(sel)
                for title,delta in [(t('Anterior'),-1),(t('Siguiente'),1)]:
                    b=discord.ui.Button(label=title,disabled=(page==1 if delta<0 else page==pages))
                    async def cb(inter,delta=delta):await system.shop(inter,page+delta,category,village,rank,search)
                    b.callback=cb;self.add_item(b)
        await self.reply(i,embed=embed,view=Shop())
    async def show_item(self,i,ident):
        self.guild(i);r=self.store.item(i.guild_id,ident);d=r['data'];v=self.store.village(i.guild_id,r['village'])
        e=discord.Embed(title=f"{d.get('emoji','📜')} {content_text(d)}"[:256],description=content_text(d,'description') or t('Sin descripción.'),color=0xC99B45)
        e.add_field(name=t('Precio / rango'),value=f"{money(d['price'])} · {d['rank']}")
        e.add_field(name=t('Aldea / existencias'),value=f"{safe(v['name'])} · {'Ilimitadas' if r['stock']==-1 else r['stock']}")
        e.add_field(name=t('Tipo'),value=t(d['kind'])+(t(' · Enseñable') if d.get('teachable') else ''))
        e.add_field(name=t('Requisitos adicionales'),value=safe(json.dumps(d.get('requires',{}),ensure_ascii=False))[:1000] or t('Ninguno'),inline=False)
        e.set_footer(text=''.join([format_fragment(t('Artículo #'), '', -1), format_fragment(ident, '', -1), format_fragment(t(' · Revisión '), '', -1), format_fragment(r['version'], '', -1), format_fragment(t(' · Solo permisos de RP, no desbloqueos en Roblox'), '', -1)]))
        if d.get('image'):e.set_thumbnail(url=d['image'])
        async def buy(inter):
            await self.player(inter)
            tx=self.store.buy(inter.guild_id,inter.user.id,ident,1,r['version'],f'buy:{i.id}')
            await self.reply(inter,''.join([format_fragment(t('Compra completada. Comprobante #'), '', -1), format_fragment(tx, '', -1), format_fragment(t('. Usa /inventario.'), '', -1)]))
        await self.reply(i,embed=e,view=self.confirmation(i.user.id,buy))
    async def inventory(self,i,page=1,permissions=False):
        self.guild(i);rows=self.store.rows('SELECT * FROM eco_inventory WHERE guild=? AND user=? AND quantity>0 ORDER BY id',(i.guild_id,i.user.id))
        rows=[r for r in rows if not permissions or json.loads(r['data'])['kind']=='permiso']
        pages=max(1,(len(rows)+9)//10);page=max(1,min(page,pages));batch=rows[(page-1)*10:page*10]
        lines=[]
        for r in batch:
            d=json.loads(r['data']);lines.append(''.join([format_fragment(t('**Lote #'), '', -1), format_fragment(r['id'], '', -1), ' · ', format_fragment(safe(content_text(d)), '', -1), '** ×', format_fragment(r['quantity'], '', -1), ' · ', format_fragment(d['rank'], '', -1), ' · ', format_fragment(t(d['kind']), '', -1)]))
        await self.reply(i,t('📜 **Permisos**\n') if permissions else t('🎒 **Inventario**\n'),embed=discord.Embed(description='\n'.join(lines) or t('Vacío.'),color=0x388E63).set_footer(text=''.join([format_fragment(t('Página '), '', -1), format_fragment(page, '', -1), '/', format_fragment(pages, '', -1), format_fragment(t(' · /inventario pagina · /tienda usar comprueba requisitos actuales'), '', -1)])))
    async def deals(self,i,kind,page=1):
        self.guild(i)
        rows=self.store.rows("SELECT * FROM eco_deals WHERE guild=? AND kind=? AND state IN ('open','accepted') ORDER BY id DESC",(i.guild_id,kind))
        if kind=='teaching' and not self.ctx['staff'](i):rows=[r for r in rows if i.user.id in (r['buyer'],r['seller'])]
        elif kind=='market':rows=[r for r in rows if r['buyer'] in (0,i.user.id) or r['seller']==i.user.id]
        batch=rows[(page-1)*10:page*10]
        await self.reply(i,t('🤝 **Mercado**') if kind=='market' else t('📚 **Enseñanzas**'),embed=discord.Embed(description='\n'.join(''.join(['#', format_fragment(r['id'], '', -1), format_fragment(t(' · lote '), '', -1), format_fragment(r['lot'], '', -1), ' ×', format_fragment(r['quantity'], '', -1), ' · ', format_fragment(money(r['price']), '', -1), ' · ', format_fragment(r['state'], '', -1)]) for r in batch) or t('Sin solicitudes.'),color=0x388E63).set_footer(text=t('Usa ver con el ID para revisar las condiciones.')))
    async def tick(self):
        if time.monotonic()<self.next_tick:return
        self.next_tick=time.monotonic()+300;g=self.ctx['GUILD']
        if not self.store.settings(g).get('enabled'):return
        try:
            now=self.store.now()
            users={r['user'] for r in self.ctx['STORE'].all() if r['guild']==g and r['state']=='approved'}
            recent={r['user'] for r in self.store.rows('SELECT user FROM eco_profiles WHERE guild=? AND observed>?',(g,now-3600))}
            for u in users-recent:
                try:await self.sync(g,u)
                except discord.HTTPException:log.warning(t('No se pudo sincronizar el usuario %s; se reintentará.'),u)
            self.store.policy.monthly(g)
            self.store.pay_payroll(g)
            await self.send_logs(g)
        except Exception:log.exception(t('Economía: ciclo pendiente; se reintentará sin duplicar pagos.'))
    @audit_output
    async def send_logs(self,g):
        settings=self.store.settings(g);channel=settings.get('log_channel')
        if not channel:return
        ch=await self.bot.channel(channel)
        rows=self.store.rows('SELECT * FROM eco_transactions WHERE guild=? AND id>? ORDER BY id LIMIT 15',(g,settings.get('log_cursor',0)))
        for r in rows:
            entries=self.store.rows('SELECT account,delta FROM eco_entries WHERE tx=?',(r['id'],))
            text='\n'.join(f"{x['account']}: {x['delta']:+,} Ryo" for x in entries)
            await ch.send(embed=discord.Embed(title=''.join([format_fragment(t('💰 Movimiento #'), '', -1), format_fragment(r['id'], '', -1)]),description=safe(r['reason'])+'\n'+text[:3000],color=0x388E63))
            if r['ref'].startswith('mission:'):
                receipt=self.store.mission_receipt(g,int(r['ref'].split(':')[1]))
                if receipt:
                    from evidence import safe_path
                    path=safe_path(self.ctx['STORE'].root,receipt['proof'])
                    embed=self.payments.receipt_card(receipt)
                    if path.is_file():
                        filename='captura-mision'+path.suffix;embed.set_image(url='attachment://'+filename)
                        file=discord.File(path,filename=filename)
                        try:await ch.send(embed=embed,file=file)
                        finally:file.close()
                    else:await ch.send(t('No se encuentra la captura archivada de este pago.'),embed=embed)
            settings=self.store.settings(g);settings['log_cursor']=r['id']
            with self.store.db:self.store.db.execute('UPDATE eco_settings SET data=? WHERE guild=?',(json.dumps(settings),g))
        pending=self.store.rows('SELECT village,SUM(amount) total FROM eco_payroll WHERE guild=? AND paid=0 GROUP BY village',(g,))
        issues=self.store.issues(g)
        signature=json.dumps({'pending':pending,'issues':issues},sort_keys=True)
        settings=self.store.settings(g)
        if signature!=settings.get('health_signature'):
            if pending or issues:
                text='\n'.join(''.join([format_fragment(t('Aldea #'), '', -1), format_fragment(r['village'], '', -1), format_fragment(t(': nómina pendiente '), '', -1), format_fragment(money(r['total']), '', -1)]) for r in pending)
                text+='\n'+'\n'.join(''.join([format_fragment(t('Reporte pagado #'), '', -1), format_fragment(r['report'], '', -1), format_fragment(t(' cambió: '), '', -1), format_fragment(r['state'], '', -1)]) for r in issues)
                await ch.send(embed=discord.Embed(title=t('⚠️ Tesorería · Revisión necesaria'),description=text[:3900],color=0xD89028))
            settings['health_signature']=signature
            with self.store.db:self.store.db.execute('UPDATE eco_settings SET data=? WHERE guild=?',(json.dumps(settings),g))
    def install(self):
        system=self;s=self.store;ctx=self.ctx
        eco=app_commands.Group(name='economia',description='Tesoros, salarios y pagos de World X')
        shop=app_commands.Group(name='tienda',description='Catálogo, compras y uso de permisos')
        admin=app_commands.Group(name='tienda_admin',description='Administrar artículos y categorías')
        market=app_commands.Group(name='mercado',description='Intercambiar armas, lanzables y consumibles')
        teach=app_commands.Group(name='ensenanza',description='Aprender técnicas con otros shinobis')

        @self.bot.tree.command(name='tarjeta_ninja',description='Ver ficha shinobi, cartera y misiones (tuya o de otro jugador)')
        @app_commands.guild_only()
        async def ninja_card(i:discord.Interaction,usuario:discord.Member|None=None):
            from ninja_card import show
            await show(self,i,target=usuario)
        @self.bot.tree.command(name='cartera',description='Consultar cartera, Ryo y misiones (tuya o de otro jugador)')
        @app_commands.guild_only()
        async def wallet(i:discord.Interaction,usuario:discord.Member|None=None):
            from ninja_card import show
            await show(self,i,target=usuario)
        @self.bot.tree.command(name='ver_ficha',description='Ver ficha shinobi, cartera y registro de otro jugador')
        @app_commands.guild_only()
        async def view_shinobi(i:discord.Interaction,usuario:discord.Member):
            from ninja_card import show
            await show(self,i,target=usuario)
        @eco.command(name='panel',description='Publicar el panel de economía (administración)')
        async def panel(i:discord.Interaction,canal:discord.TextChannel):
            self.admin(i)
            if canal.guild.id!=i.guild_id:raise ValueError(t('Canal de otro servidor.'))
            await i.response.defer(ephemeral=True)
            await canal.send(embeds=panel_embeds('economy'),view=self.home())
            await self.reply(i,t('Panel publicado.'))
        @eco.command(name='estado',description='Consultar tesoros, configuración e incidencias')
        async def status(i:discord.Interaction):
            self.guild(i);vs=s.villages(i.guild_id)
            lines=[f"#{v['id']} **{safe(v['name'])}**: {money(s.balance(i.guild_id,'v:'+str(v['id'])))}" for v in vs]
            pending=s.db.execute('SELECT COALESCE(SUM(amount),0) FROM eco_payroll WHERE guild=? AND paid=0',(i.guild_id,)).fetchone()[0]
            await self.reply(i,(t('🟢 Activa') if s.settings(i.guild_id).get('enabled') else t('⏸️ Pausada'))+'\n'+'\n'.join(lines[:25])+''.join([format_fragment(t('\nNómina pendiente: '), '', -1), format_fragment(money(pending), '', -1), format_fragment(t('\nReportes pagados que necesitan revisión: '), '', -1), format_fragment(len(s.issues(i.guild_id)), '', -1), format_fragment(t('\n/ayuda: usa /help.'), '', -1)]))
        @eco.command(name='activar',description='Activar o pausar la economía y configurar auditoría (administración)')
        async def activate(i:discord.Interaction,activa:bool,canal_auditoria:discord.TextChannel):
            self.admin(i)
            if activa and (not s.villages(i.guild_id) or not s.rows('SELECT * FROM eco_ranks WHERE guild=?',(i.guild_id,))):raise ValueError(t('Configura al menos una aldea y un rango primero.'))
            async def apply(inter):
                self.admin(inter)
                # Closing all observed salary intervals prevents accrual during pauses.
                s.configure(inter.guild_id,inter.user.id,enabled=activa,log_channel=canal_auditoria.id)
                if not activa:
                    for p in s.rows('SELECT user FROM eco_profiles WHERE guild=?',(inter.guild_id,)):s.observe(inter.guild_id,p['user'],[],False)
                self.next_tick=0
                await self.reply(inter,t('Economía activada. La acreditación comienza al sincronizar roles.') if activa else t('Economía pausada. Se conservan saldos y obligaciones anteriores.'))
            await self.reply(i,t('Se activarán pagos reales en Ryo del servidor.') if activa else t('Se pausarán operaciones y nueva acumulación salarial.'),view=self.confirmation(i.user.id,apply))
        @eco.command(name='aldea_crear',description='Crear una aldea, sus roles y capital inicial (administración)')
        async def village_create(i:discord.Interaction,nombre:str,rol_aldea:discord.Role,rol_kage:discord.Role,capital:app_commands.Range[int,0,1000000000000]=15000000):
            self.admin(i)
            if rol_aldea.is_default() or rol_kage.is_default() or rol_aldea.id==rol_kage.id:raise ValueError(t('Usa dos roles distintos que no sean @everyone.'))
            async def apply(inter):
                self.admin(inter);v=s.add_village(inter.guild_id,inter.user.id,nombre,rol_aldea.id,rol_kage.id,capital)
                await self.reply(inter,''.join([format_fragment(t('Aldea #'), '', -1), format_fragment(v, '', -1), format_fragment(t(' creada con '), '', -1), format_fragment(money(capital), '', -1), '.']))
            await self.reply(i,''.join([format_fragment(t('Crear **'), '', -1), format_fragment(safe(nombre), '', -1), format_fragment(t('** con capital de **'), '', -1), format_fragment(money(capital), '', -1), format_fragment(t('**. Usa el mismo capital para todas las aldeas iniciales.'), '', -1)]),view=self.confirmation(i.user.id,apply))
        @eco.command(name='aldea_editar',description='Editar nombre y roles de una aldea conservando su tesoro')
        async def village_edit(i:discord.Interaction,aldea:int,nombre:str,rol_aldea:discord.Role,rol_kage:discord.Role):
            self.admin(i);s.village(i.guild_id,aldea)
            if not 1<=len(nombre.strip())<=60 or rol_aldea.is_default() or rol_kage.is_default() or rol_aldea.id==rol_kage.id:raise ValueError(t('Nombre o roles inválidos.'))
            with s.db:
                s.db.execute('UPDATE eco_villages SET name=?,role=?,kage_role=? WHERE guild=? AND id=?',(nombre.strip(),rol_aldea.id,rol_kage.id,i.guild_id,aldea));s.audit(i.guild_id,i.user.id,'village_edit',{'id':aldea,'name':nombre})
            self.next_tick=0;await self.reply(i,t('Aldea actualizada; los cambios salariales se aplican al sincronizar.'))
        @eco.command(name='rango',description='Vincular un rol a su salario y nivel de permisos (administración)')
        @app_commands.choices(rango=[app_commands.Choice(name=v[0],value=k) for k,v in RANKS.items()])
        async def rank(i:discord.Interaction,rango:str,rol:discord.Role,mensual:app_commands.Range[int,0,1000000000000]|None=None,nivel_permisos:app_commands.Range[int,1,5]|None=None):
            self.admin(i)
            if rol.is_default():raise ValueError(t('No se puede usar @everyone como rango.'))
            s.rank(i.guild_id,i.user.id,rango,rol.id,mensual,nivel_permisos);self.next_tick=0
            await self.reply(i,t('Rango vinculado. Niveles: 1 Genin (D), 2 Chunin (D/C/B), 3–5 Jounin o superior (D–S). Si hay varios rangos se usa el sueldo mayor, sin sumarlos.'))
        @eco.command(name='acreditar',description='Actualizar tu aldea y salario a partir de tu ficha y roles')
        async def accredit(i:discord.Interaction):
            self.guild(i);await i.response.defer(ephemeral=True);p=await self.player(i)
            await self.reply(i,''.join([format_fragment(t('Acreditado en aldea #'), '', -1), format_fragment(p['village'], '', -1), ' · ', format_fragment(RANKS[p['rank']][0], '', -1), format_fragment(t('. El tiempo salarial comienza en la primera acreditación válida.'), '', -1)]))
        @eco.command(name='sueldo',description='Ver sueldo mensual y sus dos quincenas; no hay pagos diarios')
        async def salary(i:discord.Interaction):
            self.guild(i);await i.response.defer(ephemeral=True);p=await self.player(i)
            rank=s.rows('SELECT monthly FROM eco_ranks WHERE guild=? AND key=?',(i.guild_id,p['rank']))[0]
            await self.reply(i,''.join(['**', format_fragment(RANKS[p['rank']][0], '', -1), format_fragment(t('**\nSueldo mensual: **'), '', -1), format_fragment(money(rank['monthly']), '', -1), format_fragment(t('**\nCada quincena completa: **'), '', -1), format_fragment(money(rank['monthly'] // 2), '', -1), format_fragment(t('**\nEl mensual se abona en dos quincenas. No hay pagos diarios ni un tercer abono mensual. Las misiones se pagan aparte.'), '', -1)]))
        @eco.command(name='saldo',description='Consultar tu saldo en Ryo')
        async def balance(i:discord.Interaction):
            from ninja_card import show
            await show(self,i)
        @eco.command(name='transferir',description='Enviar tus propios Ryo a otro shinobi')
        async def transfer(i:discord.Interaction,destinatario:discord.Member,importe:app_commands.Range[int,1,1000000000000]):
            self.guild(i)
            async def apply(inter):
                await self.player(inter);await self.sync(inter.guild_id,destinatario.id)
                tx=s.transfer(inter.guild_id,inter.user.id,destinatario.id,importe,f'transfer:{i.id}');await self.reply(inter,''.join([format_fragment(t('Transferencia registrada #'), '', -1), format_fragment(tx, '', -1), '.']))
            await self.reply(i,''.join([format_fragment(t('Enviar '), '', -1), format_fragment(money(importe), '', -1), format_fragment(t(' a '), '', -1), format_fragment(safe(destinatario.display_name), '', -1), '.']),view=self.confirmation(i.user.id,apply))
        @eco.command(name='ajustar',description='Registrar fondos o retirar Ryo con motivo (administración)')
        async def adjust(i:discord.Interaction,importe:int,motivo:str,aldea:int|None=None,usuario:discord.Member|None=None):
            self.admin(i)
            if (aldea is None)==(usuario is None):raise ValueError(t('Indica únicamente aldea o usuario.'))
            if aldea is not None:s.village(i.guild_id,aldea)
            key=f'v:{aldea}' if aldea is not None else f'u:{usuario.id}'
            async def apply(inter):
                self.admin(inter);tx=s.adjust(inter.guild_id,inter.user.id,key,importe,motivo,f'adjust:{i.id}');await self.reply(inter,''.join([format_fragment(t('Ajuste registrado #'), '', -1), format_fragment(tx, '', -1), '.']))
            await self.reply(i,''.join([format_fragment(t('Ajuste de '), '', -1), format_fragment(importe, ''.join(['+,']), -1), format_fragment(t(' Ryo en '), '', -1), format_fragment(key, '', -1), format_fragment(t('. Motivo: '), '', -1), format_fragment(safe(motivo), '', -1)]),view=self.confirmation(i.user.id,apply))
        @eco.command(name='traslado',description='Transferir fondos entre aldeas por eventos o ataques (administración)')
        async def treasury_transfer(i:discord.Interaction,origen:int,destino:int,importe:app_commands.Range[int,1,1000000000000],motivo:str):
            self.ctx['V7'].authority(i)
            async def apply(inter):
                self.ctx['V7'].authority(inter);tx=s.treasury_transfer(inter.guild_id,inter.user.id,origen,destino,importe,motivo,f'treasury:{i.id}');await self.reply(inter,''.join([format_fragment(t('Traslado #'), '', -1), format_fragment(tx, '', -1), format_fragment(t(' registrado.'), '', -1)]))
            await self.reply(i,''.join([format_fragment(t('Transferir '), '', -1), format_fragment(money(importe), '', -1), format_fragment(t(' de aldea #'), '', -1), format_fragment(origen, '', -1), format_fragment(t(' a #'), '', -1), format_fragment(destino, '', -1), '.']),view=self.confirmation(i.user.id,apply))
        @eco.command(name='mision',description='Pagar un reporte aprobado desde tu aldea (Kage o administración)')
        async def mission(i:discord.Interaction,reporte:int,aldea:int,importe:app_commands.Range[int,1,1000000000000],modalidad:Literal['squad']='squad'):
            await self.payments.start(i,reporte,aldea,importe,modalidad)
        @eco.command(name='revertir_mision',description='Anular un pago sin permitir un segundo cobro (administración)')
        async def reverse(i:discord.Interaction,reporte:int,motivo:str):
            self.admin(i)
            async def apply(inter):
                self.admin(inter);s.reverse_mission(inter.guild_id,inter.user.id,reporte,motivo);await self.reply(inter,t('Pago revertido. El reporte continúa bloqueado para nuevos pagos.'))
            await self.reply(i,t('Se devolverá el dinero al tesoro si todos los destinatarios conservan saldo suficiente. No se retirará parcialmente.'),view=self.confirmation(i.user.id,apply))
        @eco.command(name='nomina',description='Ver nómina pendiente y reintentar pagos vencidos (administración)')
        async def payroll(i:discord.Interaction,reintentar:bool=False):
            self.admin(i);s.prepare_payroll(i.guild_id)
            paid=s.pay_payroll(i.guild_id) if reintentar else 0
            rows=s.rows('SELECT village,SUM(amount) total,COUNT(*) personas FROM eco_payroll WHERE guild=? AND paid=0 GROUP BY village',(i.guild_id,))
            await self.reply(i,''.join([format_fragment(t('Pagos completados: '), '', -1), format_fragment(paid, '', -1), '\n'])+'\n'.join(''.join([format_fragment(t('Aldea #'), '', -1), format_fragment(r['village'], '', -1), ': ', format_fragment(money(r['total']), '', -1), ' / ', format_fragment(r['personas'], '', -1), format_fragment(t(' pagos'), '', -1)]) for r in rows) or t('Sin pendientes.'))
        @eco.command(name='probar',description='Ejecutar un mes económico ficticio sin tocar saldos reales (administración)')
        async def sandbox(i:discord.Interaction):
            self.admin(i);await i.response.defer(ephemeral=True)
            from economy_demo import run_demo
            result=run_demo()
            await self.reply(i,t('🧪 **Prueba aislada completada**\n')+result+t('\nNo se modificaron fichas, tesoros ni inventarios reales.'))
        @eco.command(name='simular',description='Proyectar un mes de salarios sin modificar saldos')
        async def simulate(i:discord.Interaction):
            self.guild(i);rows=s.rows('SELECT village,SUM(monthly) total,COUNT(*) personas FROM eco_service WHERE guild=? AND end IS NULL GROUP BY village',(i.guild_id,))
            await self.reply(i,t('📊 **Proyección mensual con el personal actualmente acreditado**\n')+'\n'.join(''.join([format_fragment(t('Aldea #'), '', -1), format_fragment(r['village'], '', -1), ' · ', format_fragment(r['personas'], '', -1), format_fragment(t(' shinobis · '), '', -1), format_fragment(money(r['total']), '', -1), format_fragment(t('/mes · reserva '), '', -1), format_fragment(s.balance(i.guild_id, 'v:' + str(r['village'])) / r['total'], ''.join(['.1f']), -1), format_fragment(t(' meses'), '', -1)]) if r['total'] else ''.join([format_fragment(t('Aldea #'), '', -1), format_fragment(r['village'], '', -1), format_fragment(t(': sin gasto salarial'), '', -1)]) for r in rows)+t('\nNo incluye futuras misiones, compras, altas ni eventos. No modifica dinero.'))
        @eco.command(name='historial',description='Ver tus movimientos o la auditoría de administración')
        async def history(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1,todos:bool=False):
            self.guild(i)
            if todos:self.admin(i)
            sql='SELECT DISTINCT t.* FROM eco_transactions t JOIN eco_entries e ON e.tx=t.id WHERE t.guild=?';args=[i.guild_id]
            if not todos:sql+=' AND e.account=?';args.append(f'u:{i.user.id}')
            rows=s.rows(sql+' ORDER BY t.id DESC LIMIT 10 OFFSET ?',args+[(pagina-1)*10])
            lines=[]
            for r in rows:
                entries=s.rows('SELECT account,delta FROM eco_entries WHERE tx=?',(r['id'],))
                if not todos:entries=[e for e in entries if e['account']==f'u:{i.user.id}']
                lines.append(f"#{r['id']} {safe(r['reason'])}\n"+'; '.join(f"{e['account']} {e['delta']:+,}" for e in entries))
            await self.reply(i,'\n'.join(lines)[:1900] or t('Sin movimientos.'))
        @eco.command(name='recompensa',description='Configurar el mínimo y máximo por rango de misión (administración)')
        async def reward(i:discord.Interaction,rango:Literal['D','C','B','A','S'],minimo:app_commands.Range[int,1,1000000000000],maximo:app_commands.Range[int,1,1000000000000]):
            self.admin(i)
            if minimo>maximo:raise ValueError(t('El mínimo no puede superar el máximo.'))
            rewards=dict(s.settings(i.guild_id).get('mission_rewards',MISSION_REWARDS));rewards[rango]=[minimo,maximo]
            s.configure(i.guild_id,i.user.id,mission_rewards=rewards);await self.reply(i,t('Intervalo guardado. Se aplica al importe escrito: por persona o al total de squad según la modalidad del pago.'))
        @eco.command(name='auditoria',description='Consultar cambios de configuración, objetos y enseñanzas (administración)')
        async def audit(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1):
            self.admin(i);rows=s.rows('SELECT * FROM eco_audit WHERE guild=? ORDER BY id DESC LIMIT 5 OFFSET ?',(i.guild_id,(pagina-1)*5))
            await self.reply(i,'\n'.join(f"#{r['id']} · {r['action']} · actor {r['actor']}\n{safe(r['detail'])[:250]}" for r in rows) or t('Sin cambios registrados.'))
        @eco.command(name='incidencias',description='Ver reportes pagados modificados o invalidados (administración)')
        async def issues(i:discord.Interaction):
            self.admin(i);await self.reply(i,'\n'.join(''.join([format_fragment(t('Reporte #'), '', -1), format_fragment(r['report'], '', -1), format_fragment(t(' · aldea #'), '', -1), format_fragment(r['village'], '', -1), ' · ', format_fragment(r['state'], '', -1)]) for r in s.issues(i.guild_id))[:1900] or t('Sin incidencias de misiones pagadas.'))

        @shop.command(name='ver',description='Explorar la tienda por categorías y rangos')
        async def browse(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1,categoria:int=0,aldea:int=0,rango:Literal['D','C','B','A','S']|None=None,buscar:str=''):
            await self.shop(i,pagina,categoria,aldea,rango or '',buscar)
        @shop.command(name='articulo',description='Ver un artículo, sus requisitos y comprar una unidad')
        async def detail(i:discord.Interaction,articulo:int):await self.show_item(i,articulo)
        @shop.command(name='comprar',description='Comprar una cantidad de un artículo')
        async def buy(i:discord.Interaction,articulo:int,cantidad:app_commands.Range[int,1,10000]=1):
            self.guild(i);r=s.item(i.guild_id,articulo)
            async def apply(inter):
                await self.player(inter);tx=s.buy(inter.guild_id,inter.user.id,articulo,cantidad,r['version'],f'buy:{i.id}');await self.reply(inter,''.join([format_fragment(t('Compra #'), '', -1), format_fragment(tx, '', -1), format_fragment(t(' completada.'), '', -1)]))
            await self.reply(i,''.join([format_fragment(t('Comprar '), '', -1), format_fragment(safe(content_text(r['data'])), '', -1), ' ×', format_fragment(cantidad, '', -1), ': ', format_fragment(money(r['data']['price'] * cantidad), '', -1)]),view=self.confirmation(i.user.id,apply))
        @shop.command(name='usar',description='Comprobar permiso actual y consumir lanzables o consumibles')
        async def use(i:discord.Interaction,lote:int,cantidad:app_commands.Range[int,1,10000]=1):
            self.guild(i);r=s.lot(i.guild_id,lote,i.user.id)
            async def apply(inter):
                await self.player(inter);s.use(inter.guild_id,inter.user.id,lote,cantidad,f'use:{i.id}');await self.reply(inter,t('Uso registrado y autorizado por tus requisitos actuales. Las consecuencias de RP se revisan por moderación.'))
            await self.reply(i,''.join([format_fragment(t('Usar '), '', -1), format_fragment(safe(content_text(r['data'])), '', -1), format_fragment(t('. Consumibles y lanzables se descuentan del inventario; armas y permisos se conservan.'), '', -1)]),view=self.confirmation(i.user.id,apply))
        @self.bot.tree.command(name='inventario',description='Ver tus objetos y sus identificadores de lote')
        async def inventory(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1):await self.inventory(i,pagina)
        @self.bot.tree.command(name='permisos',description='Ver las técnicas y modes adquiridos; el uso requiere rango actual')
        async def permissions(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1):await self.inventory(i,pagina,True)
        @shop.command(name='categorias',description='Consultar las categorías disponibles')
        async def categories(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1):
            self.guild(i);rows=s.rows('SELECT * FROM eco_categories WHERE guild=? ORDER BY id LIMIT 20 OFFSET ?',(i.guild_id,(pagina-1)*20))
            await self.reply(i,'\n'.join(f"#{r['id']} · {safe(ctx['TRANSLATIONS'].category(i.guild_id,r['id'],r['name']))}" for r in rows) or t('Sin categorías.'))
        @admin.command(name='categoria',description='Crear una categoría sin límite comercial de cantidad')
        async def category(i:discord.Interaction,nombre:str):
            self.admin(i);ident=s.category(i.guild_id,i.user.id,nombre);await self.reply(i,''.join([format_fragment(t('Categoría #'), '', -1), format_fragment(ident, '', -1), format_fragment(t(' disponible.'), '', -1)]))
        @admin.command(name='crear',description='Crear un artículo de tienda')
        async def create(i:discord.Interaction,nombre:str,aldea:int,categoria:int,tipo:Literal['arma','lanzable','consumible','permiso'],rango:Literal['D','C','B','A','S'],precio:app_commands.Range[int,0,1000000000000]):
            self.admin(i);ident=s.save_item(i.guild_id,i.user.id,aldea,categoria,{'name':nombre,'kind':tipo,'rank':rango,'price':precio,'description':'','requires':{}})
            await self.reply(i,''.join([format_fragment(t('Artículo #'), '', -1), format_fragment(ident, '', -1), format_fragment(t(' creado. Usa /tienda_admin editar para descripción e imagen; opciones para stock y enseñanza; requisito para restricciones.'), '', -1)]))
        @admin.command(name='editar',description='Editar nombre, descripción, precio, emoji e imagen mediante formulario')
        async def edit(i:discord.Interaction,articulo:int):
            self.admin(i);r=s.item(i.guild_id,articulo)
            class Edit(ctx['SafeModal']):
                def __init__(self):
                    super().__init__(title=t('📜 Editar artículo'));self.fields={}
                    for key,label,length in [('name',t('Nombre'),80),('description',t('Descripción, consecuencias y reglas RP'),1800),('price',t('Precio entero en Ryo'),13),('emoji',t('Emoji o símbolo'),80),('image',t('URL HTTPS de la imagen'),400)]:
                        f=discord.ui.TextInput(label=label,max_length=length,required=key in ('name','price'),default=str(r['data'].get(key,'')),style=discord.TextStyle.paragraph if key=='description' else discord.TextStyle.short);self.fields[key]=f;self.add_item(f)
                async def on_submit(modal,inter):
                    self.admin(inter);fresh=s.item(inter.guild_id,articulo)
                    if fresh['version']!=r['version']:raise ValueError(t('Otro administrador editó el artículo. Ábrelo otra vez.'))
                    d=dict(r['data']);d.update({k:str(f.value).strip() for k,f in modal.fields.items()})
                    try:d['price']=int(d['price'])
                    except ValueError:raise ValueError(t('Escribe un precio entero sin puntos ni comas.'))
                    s.save_item(inter.guild_id,inter.user.id,r['village'],r['category'],d,fresh['stock'],articulo)
                    await self.reply(inter,t('Artículo actualizado. Los objetos ya comprados conservan sus características originales.'))
            await i.response.send_modal(Edit())
        @admin.command(name='opciones',description='Cambiar stock, rango, venta local, enseñanza y visibilidad')
        async def options(i:discord.Interaction,articulo:int,stock:app_commands.Range[int,-1,1000000000]=-1,visible:bool=True,ensenable:bool=False,solo_aldea:bool=False,rango:Literal['D','C','B','A','S']|None=None):
            self.admin(i);r=s.item(i.guild_id,articulo);d=r['data'];d.update(teachable=ensenable,local_only=solo_aldea)
            if ensenable and d['kind']!='permiso':raise ValueError(t('Solo los permisos pueden ser enseñables.'))
            if rango:d['rank']=rango
            s.save_item(i.guild_id,i.user.id,r['village'],r['category'],d,stock,articulo)
            with s.db:s.db.execute('UPDATE eco_items SET active=? WHERE guild=? AND id=?',(int(visible),i.guild_id,articulo))
            await self.reply(i,t('Opciones guardadas para nuevas compras. Stock -1 significa ilimitado.'))
        @admin.command(name='requisito',description='Añadir o quitar un elemento, bloodline, rol o permiso previo')
        async def requirement(i:discord.Interaction,articulo:int,tipo:Literal['elements','bloodlines','roles','permissions'],clave:str,quitar:bool=False):
            self.admin(i);r=s.item(i.guild_id,articulo);d=r['data'];req=d.setdefault('requires',{});values=req.setdefault(tipo,[])
            if tipo in ('roles','permissions'):
                try:value=int(clave)
                except ValueError:raise ValueError(t('Usa el ID del rol o del artículo requerido.'))
                if tipo=='roles' and not i.guild.get_role(value):raise ValueError(t('Rol no encontrado.'))
                if tipo=='permissions':s.item(i.guild_id,value)
            else:
                value=clave.lower().strip();rules=ctx['ASSIGNMENTS'].config
                available=rules['elements'] if tipo=='elements' else {**rules['main'],**rules['secondary']}
                if value not in available:raise ValueError(t('Clave no encontrada. Elige una de las sugerencias.'))
            if quitar:
                if value in values:values.remove(value)
            elif tipo=='elements' or value not in values:values.append(value)
            if tipo=='elements' and len(values)>4:raise ValueError(t('Máximo cuatro elementos requeridos.'))
            if tipo=='permissions' and int(clave)==articulo:raise ValueError(t('Un artículo no puede requerirse a sí mismo.'))
            s.save_item(i.guild_id,i.user.id,r['village'],r['category'],d,r['stock'],articulo);await self.reply(i,t('Requisitos guardados. Todos deben cumplirse; repetir un elemento exige dos slots de esa naturaleza.'))
        @requirement.autocomplete('clave')
        async def key_choices(i:discord.Interaction,current:str):
            kind=getattr(i.namespace,'tipo','');rules=ctx['ASSIGNMENTS'].config
            if kind=='roles':return [app_commands.Choice(name=r.name[:100],value=str(r.id)) for r in i.guild.roles if str(current).lower() in r.name.lower() and not r.is_default()][:25]
            if kind=='permissions':return [app_commands.Choice(name=f"#{r['id']} {content_text(json.loads(r['data']))}"[:100],value=str(r['id'])) for r in s.rows('SELECT id,data FROM eco_items WHERE guild=? ORDER BY id',(i.guild_id,)) if str(current).lower() in content_text(json.loads(r['data'])).lower()][:25]
            choices=rules['elements'] if kind=='elements' else {**rules['main'],**rules['secondary']} if kind=='bloodlines' else {}
            return [app_commands.Choice(name=f"{v.get('label',k)} ({k})"[:100],value=k) for k,v in choices.items() if str(current).lower() in (k+' '+v.get('label',k)).lower()][:25]

        @admin.command(name='inventario',description='Inspeccionar objetos y permisos de un shinobi (moderación)')
        async def inspect_inventory(i:discord.Interaction,usuario:discord.Member,pagina:app_commands.Range[int,1,100000]=1):
            self.staff(i);rows=s.rows('SELECT * FROM eco_inventory WHERE guild=? AND user=? AND quantity>0 ORDER BY id LIMIT 10 OFFSET ?',(i.guild_id,usuario.id,(pagina-1)*10))
            await self.reply(i,''.join([format_fragment(t('Inventario de '), '', -1), format_fragment(safe(usuario.display_name), '', -1), '\n'])+'\n'.join(''.join([format_fragment(t('Lote #'), '', -1), format_fragment(r['id'], '', -1), ' · ', format_fragment(safe(content_text(json.loads(r['data']))), '', -1), ' ×', format_fragment(r['quantity'], '', -1), format_fragment(t(' · rango '), '', -1), format_fragment(json.loads(r['data'])['rank'], '', -1)]) for r in rows))
        @admin.command(name='otorgar',description='Entregar un objeto o permiso excepcional con motivo (administración)')
        async def grant(i:discord.Interaction,usuario:discord.Member,articulo:int,cantidad:app_commands.Range[int,1,10000],motivo:str):
            self.admin(i)
            async def apply(inter):
                self.admin(inter);await self.sync(inter.guild_id,usuario.id)
                lot=s.grant(inter.guild_id,inter.user.id,usuario.id,articulo,cantidad,motivo,f'grant:{i.id}');await self.reply(inter,''.join([format_fragment(t('Lote #'), '', -1), format_fragment(lot, '', -1), format_fragment(t(' otorgado. El uso sigue exigiendo sus requisitos.'), '', -1)]))
            await self.reply(i,''.join([format_fragment(t('Otorgar artículo #'), '', -1), format_fragment(articulo, '', -1), ' ×', format_fragment(cantidad, '', -1), format_fragment(t(' a '), '', -1), format_fragment(safe(usuario.display_name), '', -1), format_fragment(t('. Motivo: '), '', -1), format_fragment(safe(motivo), '', -1)]),view=self.confirmation(i.user.id,apply))
        @admin.command(name='retirar',description='Retirar un objeto o permiso con motivo (administración)')
        async def revoke(i:discord.Interaction,lote:int,cantidad:app_commands.Range[int,1,10000],motivo:str):
            self.admin(i);r=s.lot(i.guild_id,lote)
            async def apply(inter):
                self.admin(inter);s.revoke(inter.guild_id,inter.user.id,lote,cantidad,motivo,f'revoke:{i.id}');await self.reply(inter,t('Retirada registrada en auditoría.'))
            await self.reply(i,''.join([format_fragment(t('Retirar '), '', -1), format_fragment(safe(content_text(r['data'])), '', -1), ' ×', format_fragment(cantidad, '', -1), format_fragment(t(' al usuario '), '', -1), format_fragment(r['user'], '', -1), format_fragment(t('. Motivo: '), '', -1), format_fragment(safe(motivo), '', -1)]),view=self.confirmation(i.user.id,apply))
        @market.command(name='ver',description='Consultar ofertas de armas, lanzables y consumibles')
        async def market_list(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1):await self.deals(i,'market',pagina)
        @market.command(name='ofrecer',description='Ofrecer un objeto por Ryo o intercambiarlo con otro objeto')
        async def offer(i:discord.Interaction,lote:int,cantidad:app_commands.Range[int,1,10000],precio:app_commands.Range[int,0,1000000000000],destinatario:discord.Member|None=None,lote_a_cambio:int=0,cantidad_a_cambio:app_commands.Range[int,0,10000]=0):
            self.guild(i);await i.response.defer(ephemeral=True);await self.player(i)
            if destinatario:await self.sync(i.guild_id,destinatario.id)
            ident=s.propose(i.guild_id,i.user.id,destinatario.id if destinatario else 0,lote,cantidad,precio,wanted=lote_a_cambio,wanted_q=cantidad_a_cambio)
            await self.reply(i,''.join([format_fragment(t('Oferta #'), '', -1), format_fragment(ident, '', -1), format_fragment(t(' creada. Comparte su ID. /mercado oferta para revisar y aceptar. El inventario se verifica al aceptar; no queda reservado.'), '', -1)]))
        @market.command(name='oferta',description='Ver las condiciones completas y aceptar una oferta')
        async def view_offer(i:discord.Interaction,oferta:int):
            self.guild(i);d=s.deal(i.guild_id,oferta)
            if d['kind']!='market' or (d['buyer'] and i.user.id not in (d['buyer'],d['seller'])):raise ValueError(t('Esta oferta no está disponible para ti.'))
            r=s.lot(i.guild_id,d['lot']);other=s.lot(i.guild_id,d['wanted']) if d['wanted'] else None
            async def apply(inter):
                await self.player(inter);await self.sync(inter.guild_id,d['seller']);s.accept(inter.guild_id,inter.user.id,oferta);await self.reply(inter,t('Intercambio completado. La propiedad no elimina los requisitos de uso.'))
            text=''.join([format_fragment(t('Oferta #'), '', -1), format_fragment(oferta, '', -1), ' · ', format_fragment(d['state'], '', -1), format_fragment(t('\nVendedor: '), '', -1), format_fragment(d['seller'], '', -1), format_fragment(t('\nRecibes: '), '', -1), format_fragment(safe(content_text(r['data'])), '', -1), ' ×', format_fragment(d['quantity'], '', -1), format_fragment(t('\nPagas: '), '', -1), format_fragment(money(d['price']), '', -1)])
            if other:text+=''.join([format_fragment(t('\nEntregas además: '), '', -1), format_fragment(safe(content_text(other['data'])), '', -1), ' ×', format_fragment(d['wanted_quantity'], '', -1)])
            await self.reply(i,text,view=self.confirmation(i.user.id,apply))
        @market.command(name='cancelar',description='Cancelar una oferta propia aún no completada')
        async def cancel(i:discord.Interaction,oferta:int):
            self.guild(i)
            if s.deal(i.guild_id,oferta)['kind']!='market':raise ValueError(t('Usa /ensenanza cancelar.'))
            s.cancel(i.guild_id,i.user.id,oferta);await self.reply(i,t('Oferta cancelada.'))
        @teach.command(name='proponer',description='Ofrecer enseñar un permiso que ya tienes a un alumno')
        async def teaching(i:discord.Interaction,alumno:discord.Member,lote:int,precio:app_commands.Range[int,0,1000000000000]=0):
            self.guild(i);await i.response.defer(ephemeral=True);await self.player(i);await self.sync(i.guild_id,alumno.id)
            ident=s.propose(i.guild_id,i.user.id,alumno.id,lote,1,precio,'teaching');await self.reply(i,''.join([format_fragment(t('Enseñanza #'), '', -1), format_fragment(ident, '', -1), format_fragment(t('. El alumno debe abrir /ensenanza ver para aceptar. Después se presenta evidencia y la revisa moderación.'), '', -1)]))
        @teach.command(name='listar',description='Consultar enseñanzas propias o pendientes para moderación')
        async def teachings(i:discord.Interaction,pagina:app_commands.Range[int,1,100000]=1):await self.deals(i,'teaching',pagina)
        @teach.command(name='ver',description='Ver una enseñanza y aceptar sus condiciones como alumno')
        async def teaching_view(i:discord.Interaction,solicitud:int):
            self.guild(i);d=s.deal(i.guild_id,solicitud)
            if d['kind']!='teaching' or (i.user.id not in (d['seller'],d['buyer']) and not ctx['staff'](i)):raise ValueError(t('Enseñanza no accesible.'))
            r=s.lot(i.guild_id,d['lot'])
            async def apply(inter):
                await self.player(inter);await self.sync(inter.guild_id,d['seller']);s.accept(inter.guild_id,inter.user.id,solicitud);await self.reply(inter,t('Condiciones aceptadas. Usa /ensenanza evidencia después de realizar el entrenamiento.'))
            await self.reply(i,''.join([format_fragment(t('Enseñanza #'), '', -1), format_fragment(solicitud, '', -1), ' · ', format_fragment(d['state'], '', -1), format_fragment(t('\nTécnica: '), '', -1), format_fragment(safe(content_text(r['data'])), '', -1), format_fragment(t('\nInstructor '), '', -1), format_fragment(d['seller'], '', -1), format_fragment(t(' → alumno '), '', -1), format_fragment(d['buyer'], '', -1), format_fragment(t('\nPrecio '), '', -1), format_fragment(money(d['price']), '', -1), format_fragment(t('\nEvidencia: '), '', -1), format_fragment(d['proof'] or t('Pendiente'), '', -1)]),view=self.confirmation(i.user.id,apply) if d['state']=='open' and i.user.id==d['buyer'] else None)
        @teach.command(name='evidencia',description='Vincular el mensaje de Discord que contiene las pruebas de enseñanza')
        async def proof(i:discord.Interaction,solicitud:int,enlace:str):
            self.guild(i);s.evidence(i.guild_id,i.user.id,solicitud,enlace);await self.reply(i,t('Evidencia registrada. Moderación puede revisarla con /ensenanza ver y /ensenanza resolver.'))
        @teach.command(name='resolver',description='Aprobar o rechazar enseñanza tras revisar evidencia (moderación)')
        async def resolve(i:discord.Interaction,solicitud:int,aprobar:bool):
            self.staff(i);d=s.deal(i.guild_id,solicitud)
            async def apply(inter):
                self.staff(inter);await self.sync(inter.guild_id,d['seller']);await self.sync(inter.guild_id,d['buyer'])
                s.review_teaching(inter.guild_id,inter.user.id,solicitud,aprobar,ctx['owner'](inter));await self.reply(inter,t('Enseñanza aprobada y pagada.') if aprobar else t('Enseñanza rechazada sin cobro.'))
            await self.reply(i,''.join([format_fragment(t('Resolver enseñanza #'), '', -1), format_fragment(solicitud, '', -1), format_fragment(t('. Evidencia: '), '', -1), format_fragment(d['proof'] or t('Pendiente'), '', -1), format_fragment(t('\nAl aprobar se concede el permiso y se paga al instructor en una sola operación.'), '', -1)]),view=self.confirmation(i.user.id,apply))
        @teach.command(name='cancelar',description='Cancelar una enseñanza propia aún no resuelta')
        async def cancel_teaching(i:discord.Interaction,solicitud:int):
            self.guild(i)
            if s.deal(i.guild_id,solicitud)['kind']!='teaching':raise ValueError(t('Usa /mercado cancelar.'))
            s.cancel(i.guild_id,i.user.id,solicitud);await self.reply(i,t('Enseñanza cancelada sin cobro.'))
        async def village_choices(i:discord.Interaction,current:str):
            return [app_commands.Choice(name=f"#{v['id']} {v['name']}"[:100],value=v['id']) for v in s.villages(i.guild_id) if str(current).lower() in (str(v['id'])+' '+v['name']).lower()][:25]
        async def category_choices(i:discord.Interaction,current:str):
            return [app_commands.Choice(name=f"#{r['id']} {r['name']}"[:100],value=r['id']) for r in s.rows('SELECT * FROM eco_categories WHERE guild=? ORDER BY id',(i.guild_id,)) if str(current).lower() in (str(r['id'])+' '+r['name']).lower()][:25]
        async def item_choices(i:discord.Interaction,current:str):
            return [app_commands.Choice(name=f"#{r['id']} {content_text(json.loads(r['data']))}"[:100],value=r['id']) for r in s.rows('SELECT id,data FROM eco_items WHERE guild=? ORDER BY id',(i.guild_id,)) if str(current).lower() in (str(r['id'])+' '+content_text(json.loads(r['data']))).lower()][:25]
        async def lot_choices(i:discord.Interaction,current:str):
            return [app_commands.Choice(name=f"#{r['id']} {content_text(json.loads(r['data']))} ×{r['quantity']}"[:100],value=r['id']) for r in s.rows('SELECT * FROM eco_inventory WHERE guild=? AND user=? AND quantity>0 ORDER BY id',(i.guild_id,i.user.id)) if str(current).lower() in (str(r['id'])+' '+content_text(json.loads(r['data']))).lower()][:25]
        for group in (eco,shop,admin,market,teach):
            for command in group.commands:
                for param in command.parameters:
                    callback = village_choices if param.name in ('aldea','origen','destino') else category_choices if param.name=='categoria' else item_choices if param.name=='articulo' else lot_choices if param.name=='lote' else None
                    if callback:command.autocomplete(param.name)(callback)
            self.bot.tree.add_command(group)
