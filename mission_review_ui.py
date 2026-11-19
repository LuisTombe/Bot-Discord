"""Flujo guiado de reportes y lista de comprobación del moderador (Python)."""
import json
import os
from pathlib import Path
import discord
from discord import app_commands
from evidence import safe_path
from mission_evidence_policy import EvidencePolicy, has_shinobi

WORLDS={'open_world':'Open World','world_x':'World X','rpg':'RPG / normal (encargos D)'}

class SafeView(discord.ui.View):
    async def on_error(self,i,error,item):
        if i.response.is_done():await i.followup.send(str(error)[:1800],ephemeral=True)
        else:await i.response.send_message(str(error)[:1800],ephemeral=True)

class AttestModal(discord.ui.Modal,title='Comprobar una captura'):
    date=discord.ui.TextInput(label='Fecha y hora que ves: DD/MM/AAAA HH:MM',max_length=16)
    offset=discord.ui.TextInput(label='UTC del dispositivo (Colombia: -05:00)',default='-05:00',max_length=6)
    notes=discord.ui.TextInput(label='Objetivo y resultado que observaste',style=discord.TextStyle.paragraph,max_length=500)
    confirm=discord.ui.TextInput(label='Todos los puntos visibles: escribe CONFIRMO',max_length=8)
    def __init__(self,ui,row,index):super().__init__();self.ui=ui;self.row=row;self.index=index
    async def on_submit(self,i):
        try:
            self.ui.review_access(i,self.row)
            self.ui.policy.attest(self.row['id'],self.row['revision'],i.user.id,self.index,str(self.date),str(self.offset),str(self.notes),str(self.confirm))
            await i.response.send_message('Verificación guardada para esta captura y esta versión. Revisa las demás y después aprueba.',ephemeral=True)
        except ValueError as e:await i.response.send_message(str(e)[:1800],ephemeral=True)

class ReviewImages(SafeView):
    def __init__(self,ui,row,index,owner):
        super().__init__(timeout=600);self.ui=ui;self.row=row;self.index=index;self.owner=owner
        for label,delta in [('Anterior',-1),('Siguiente',1)]:
            b=discord.ui.Button(label=label,disabled=(index+delta<0 or index+delta>=len(ui.policy.proofs(row))))
            async def turn(i,d=delta):await ui.show_review(i,row,index+d)
            b.callback=turn;self.add_item(b)
    async def interaction_check(self,i):
        if i.user.id!=self.owner:raise ValueError('Abre tu propia revisión.')
        self.ui.review_access(i,self.row);return True
    @discord.ui.button(label='Verifiqué todos los puntos',style=discord.ButtonStyle.primary,row=1)
    async def attest(self,i,b):await i.response.send_modal(AttestModal(self.ui,self.row,self.index))
    @discord.ui.button(label='Rechazar: falta fecha/hora',style=discord.ButtonStyle.danger,row=1)
    async def reject(self,i,b):
        reason=f'Captura {self.index+1}: no muestra fecha y hora del dispositivo legibles. Reenvía pantalla completa.'
        self.ui.system.store.decision(self.row['id'],self.row['revision'],i.user.id,'reject',reason)
        await i.response.edit_message(content='Reporte rechazado por evidencia sin fecha/hora. No suma misiones ni genera pagos.',view=None)
    @discord.ui.button(label='Aprobar reporte verificado',style=discord.ButtonStyle.success,row=2)
    async def approve(self,i,b):
        await i.response.defer(ephemeral=True)
        # Obtener roles actuales de cada participante justo antes de resolver.
        for uid in set(self.row['data'].get('members',[self.row['owner']])):
            member=await i.guild.fetch_member(uid)
            self.ui.shinobi(member)
        self.ui.review_access(i,self.row)
        self.ui.system.store.decision(self.row['id'],self.row['revision'],i.user.id,'approve','Todas las capturas y requisitos verificados; constancia por imagen en auditoría.')
        await i.followup.send('Reporte aprobado. Se cuenta una misión por integrante. El pago conserva su revisión independiente.',ephemeral=True)

class ReportStart(SafeView):
    def __init__(self,ui,owner,world='open_world',kind=None,page=0):
        super().__init__(timeout=600);self.ui=ui;self.owner=owner;self.world=world;self.kind=kind
        world_select=discord.ui.Select(placeholder='1. Selecciona el mundo',options=[discord.SelectOption(label=v,value=k,default=k==world) for k,v in WORLDS.items()],row=0)
        async def choose_world(i):await i.response.edit_message(view=ReportStart(ui,owner,world_select.values[0]))
        world_select.callback=choose_world;self.add_item(world_select)
        choices=[(k,v) for k,v in ui.policy.config['missions'].items() if world in v['worlds']]
        page=max(0,min(page,(len(choices)-1)//25));batch=choices[page*25:(page+1)*25]
        types=discord.ui.Select(placeholder='2. Selecciona el objetivo',options=[discord.SelectOption(label=v['label'][:100],value=k,description='Rango base '+ui.system.config['types'][k]['base_rank'],default=k==kind) for k,v in batch],row=1)
        async def choose_type(i):await i.response.edit_message(view=ReportStart(ui,owner,world,types.values[0],page))
        types.callback=choose_type;self.add_item(types)
        if len(choices)>25:
            for label,step in [('Tipos anteriores',-1),('Más tipos',1)]:
                b=discord.ui.Button(label=label,row=2,disabled=not 0<=page+step<=(len(choices)-1)//25)
                async def turn(i,d=step):await i.response.edit_message(view=ReportStart(ui,owner,world,kind,page+d))
                b.callback=turn;self.add_item(b)
    async def interaction_check(self,i):
        self.ui.system.player(i)
        if i.user.id!=self.owner:raise ValueError('Abre tu propio /reporte.')
        return True
    @discord.ui.button(label='3. Completar reporte',style=discord.ButtonStyle.success,row=3)
    async def start(self,i,b):
        if not self.kind:raise ValueError('Selecciona primero el objetivo.')
        ms=self.ui.system.store
        if self.world=='rpg':
            self.ui.ensure_economy(i)
            # Reutiliza el borrador azul del usuario en lugar de crear borradores ilimitados.
            found=ms.db.execute("SELECT id FROM mission_reports WHERE guild=? AND owner=? AND session=0 AND kind='mission' AND state IN ('draft','correction','rejected') ORDER BY id DESC LIMIT 1",(i.guild_id,i.user.id)).fetchone()
            if found:row=ms.get(found[0])
            else:
                with ms.db:
                    data={'type':self.kind,'world':'rpg','enemy':'none','members':[i.user.id],'opponents':'Sin enemigo'}
                    c=ms.db.execute("INSERT INTO mission_reports(guild,session,owner,kind,data,created) VALUES(?,0,?,'mission',?,?)",(i.guild_id,i.user.id,json.dumps(data),ms.now()))
                    ms.log(i.guild_id,c.lastrowid,i.user.id,'blue_start',{})
                row=ms.get(c.lastrowid)
        else:row=ms.start(i.guild_id,i.user.id,'mission')
        data={**row['data'],'type':self.kind,'world':self.world}
        if self.world=='rpg':data['enemy']='none'
        row=ms.edit(row['id'],row['revision'],i.user.id,data=data)
        await i.response.send_message(content=self.ui.instructions(row),embed=self.ui.system.card(row),view=self.ui.system.Wizard(row),ephemeral=True)

class MissionReviewUI:
    def __init__(self,system,eco,ctx):
        self.system=system;self.eco=eco;self.ctx=ctx
        path=ctx['BASE']/'config/missions/evidence.json';config=json.loads(path.read_text(encoding='utf-8'))
        self.role=int(os.getenv('SHINOBI_ROLE_ID',str(config['shinobi_role_id'])))
        for key,rule in config['missions'].items():
            # No se sustituyen rangos configurados anteriormente por el servidor.
            system.config['types'].setdefault(key,{'label':rule['label'],'base_rank':rule['base_rank']})
        self.policy=EvidencePolicy(system.store,config);self.policy.install();system.review_ui=self
        previous=system.player
        def player(i):
            system.guild(i);self.shinobi(i.user);previous(i)
        system.player=player
        self.install_commands()
    def shinobi(self,member):has_shinobi({r.id for r in member.roles},self.role)
    def ensure_economy(self,i):self.eco.player(i.guild_id,i.user.id)
    def validate_channel(self,channel):
        if channel.permissions_for(channel.guild.default_role).view_channel:raise ValueError('El canal de revisión debe ocultarse a @everyone.')
        for target,overwrite in channel.overwrites.items():
            if overwrite.view_channel is not True:continue
            if isinstance(target,discord.Role):
                allowed=(target.id==self.ctx.get('REVIEWER') or target.permissions.manage_guild or target.permissions.administrator or target in channel.guild.me.roles)
            else:allowed=(target.id in (self.ctx.get('BOT_OWNER'),channel.guild.owner_id,self.system.bot.user.id) or target.guild_permissions.manage_guild or target.guild_permissions.administrator or any(r.id==self.ctx.get('REVIEWER') for r in target.roles))
            if not allowed:raise ValueError('El canal de pruebas permite acceso a un rol/usuario ajeno al staff. Revisa sus permisos.')
        permissions=channel.permissions_for(channel.guild.me)
        if not all(getattr(permissions,x) for x in ('view_channel','send_messages','attach_files','embed_links','read_message_history')):raise ValueError('Faltan permisos del bot para enviar y conservar pruebas en el canal privado.')
    def review_access(self,i,row):
        self.system.staff(i)
        channel=row['review_channel'] or self.system.channel_id(row['kind'])
        if i.channel_id!=channel or not isinstance(i.channel,discord.TextChannel):raise ValueError('Abre la revisión desde el canal privado asignado a este reporte.')
        self.validate_channel(i.channel)
        fresh=self.system.get(i,row['id'])
        if fresh['revision']!=row['revision'] or fresh['state']!='pending':raise ValueError('El reporte cambió o ya fue resuelto.')
        if i.user.id in {row['owner'],*row['data'].get('members',[])}:raise ValueError('Otro moderador debe revisar este reporte.')
    def instructions(self,row):
        rule=self.policy.rule(row)
        return ('**Evidencia obligatoria · '+str(rule['min_images'])+' a 4 imágenes**\n'+
          '\n'.join('• '+x for x in self.policy.checklist(row))+
          '\nPNG/JPG/WEBP, máximo 8 MiB por imagen. Usa captura de pantalla completa, no recorte. '
          'La hora del temporizador del juego NO sustituye la fecha y hora del sistema. '
          'Sin fecha u hora visibles, el staff rechazará el reporte. Horarios del formulario: Colombia UTC−5.')[:1950]
    async def show_review(self,i,row,index=0):
        self.review_access(i,row);proofs=self.policy.proofs(row)
        if not 0<=index<len(proofs):raise ValueError('La captura solicitada no existe.')
        verified=self.system.store.db.execute('SELECT 1 FROM mission_evidence_checks WHERE report=? AND revision=? AND actor=? AND image_index=?',(row['id'],row['revision'],i.user.id,index)).fetchone()
        em=discord.Embed(title=f"Reporte #{row['id']} · captura {index+1}/{len(proofs)}",description=self.instructions(row)+'\n\n'+('✅ Verificada por ti.' if verified else '⏳ Pendiente de tu verificación.'),color=0x527B52)
        em.set_image(url='attachment://evidencia'+Path(proofs[index]['path']).suffix)
        path=safe_path(self.system.store.root,proofs[index]['path'])
        with discord.File(path,filename='evidencia'+path.suffix) as file:
            view=ReviewImages(self,row,index,i.user.id)
            if i.type==discord.InteractionType.component and i.message and i.message.flags.ephemeral:
                await i.response.edit_message(embed=em,attachments=[file],view=view)
            else:await i.response.send_message(embed=em,file=file,view=view,ephemeral=True)
    def install_commands(self):
        @self.system.bot.tree.command(name='reporte',description='Shinobi: reportar una misión de RPG, Open World o World X')
        @app_commands.guild_only()
        async def report(i:discord.Interaction):
            self.system.player(i)
            await i.response.send_message('Selecciona mundo y objetivo. Una misión por reporte; adjunta pantalla completa con fecha y hora del dispositivo.',view=ReportStart(self,i.user.id),ephemeral=True)
        @self.system.bot.tree.command(name='revision_mision',description='Staff: verificar cada captura antes de aprobar el reporte')
        @app_commands.guild_only()
        async def review(i:discord.Interaction,reporte:int):await self.show_review(i,self.system.get(i,reporte))
        @self.system.bot.tree.command(name='catalogo_misiones',description='Ver tipos de misión y clasificación de rol por peligrosidad')
        @app_commands.guild_only()
        async def catalog(i:discord.Interaction,pagina:app_commands.Range[int,1]=1):
            self.system.guild(i);rows=list(self.policy.config['missions'].items());pages=(len(rows)+7)//8
            if pagina>pages:raise ValueError(f'Solo hay {pages} páginas.')
            em=discord.Embed(title=f'Catálogo de misiones · {pagina}/{pages}',description='Rangos del servidor. No se afirma que sea una lista oficial exhaustiva. El enemigo más peligroso puede elevar el rango base.',color=0x527B52)
            for key,rule in rows[(pagina-1)*8:pagina*8]:
                rank=self.system.config['types'][key]['base_rank'];em.add_field(name=rule['label'],value=f"Base {rank} · {', '.join(WORLDS[x] for x in rule['worlds'])}\n{rule['evidence']}",inline=False)
            await i.response.send_message(embed=em,ephemeral=True)

def install(system,eco,ctx):return MissionReviewUI(system,eco,ctx)
