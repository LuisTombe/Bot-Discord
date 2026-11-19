"""Discord commands for v7. Access checks are repeated at confirmation."""
from i18n import format_fragment, t
from content_i18n import content_text
import io,json,csv
from datetime import datetime
from typing import Literal
import discord
from discord import app_commands
from worldx_v7 import week,dump
from economy_domain import TZ
from missions_domain import timestamp
from v7_missions import BLUE

class V7System:
    def __init__(self,bot,ctx):
        self.bot=bot;self.ctx=ctx;self.eco=ctx['ECONOMY'];self.s=self.eco.store;self.p=self.s.policy;self.ms=ctx['MISSIONS'].store
        self.p.ensure(ctx['GUILD'])
        if self.s.settings(ctx['GUILD']).get('enabled'):self.p.monthly(ctx['GUILD'])
        from v7_missions import install
        install(ctx['MISSIONS'],self.s,ctx)
        from mission_review_ui import install as install_evidence_review
        install_evidence_review(ctx['MISSIONS'],self.s,ctx)
        self.install()
    def authority(self,i):
        self.eco.guild(i);role=self.p.ensure(i.guild_id)['authority']
        if not self.ctx['owner'](i) and not (role and role in [r.id for r in i.user.roles]):raise ValueError(t('Solo el dueño del bot o el rol de máxima autoridad configurado.'))
    def party(self,i,key):
        if key.startswith('u:'):
            if key!=f'u:{i.user.id}':raise ValueError(t('Este contrato pertenece a otro usuario.'))
        else:self.eco.kage(i,int(key[2:]))
    def report_access(self,i,row,review=False):
        self.eco.guild(i)
        if not row or row['guild']!=i.guild_id:raise ValueError(t('Reporte inexistente.'))
        if self.ctx['staff'](i):return
        if not review and i.user.id in {row['owner']}|{x['user'] for x in row['participants']}:return
        profile=self.s.profile(i.guild_id,row['owner'])
        if not profile or not profile['village']:raise ValueError(t('El autor no tiene aldea acreditada.'))
        self.eco.kage(i,profile['village'])
    async def display_report(self,i,row):
        system=self.ctx['MISSIONS']
        class Dest:
            async def send(_,**kwargs):return await self.eco.reply(i,**kwargs)
        await system.file_send(Dest(),row)
    def install(self):
        eco=self.eco;s=self.s;p=self.p
        loans=app_commands.Group(name='prestamos',description='Contratos personales y entre aldeas')
        @loans.command(name='ofrecer',description='Ofrecer un préstamo personal con interés único opcional')
        async def offer(i:discord.Interaction,destinatario:discord.Member,capital:app_commands.Range[int,1,100000],vencimiento:str,condiciones:str,interes_porcentaje:str='0'):
            await eco.player(i);await eco.sync(i.guild_id,destinatario.id)
            ident=p.offer(i.guild_id,f'u:{i.user.id}',f'u:{destinatario.id}',capital,interes_porcentaje,timestamp(vencimiento),condiciones)
            await eco.reply(i,''.join([format_fragment(t('Oferta #'), '', -1), format_fragment(ident, '', -1), format_fragment(t('. El receptor debe revisar /prestamos ver y aceptar. No se ha movido dinero.'), '', -1)]))
        @loans.command(name='aldeas',description='Proponer un préstamo entre tesoros como Kage de la aldea prestamista')
        async def village_loan(i:discord.Interaction,origen:int,destino:int,capital:app_commands.Range[int,1,1000000000000],vencimiento:str,condiciones:str,interes_porcentaje:str='0'):
            eco.kage(i,origen);ident=p.offer(i.guild_id,f'v:{origen}',f'v:{destino}',capital,interes_porcentaje,timestamp(vencimiento),condiciones)
            await eco.reply(i,''.join([format_fragment(t('Contrato #'), '', -1), format_fragment(ident, '', -1), format_fragment(t('. Debe aceptarlo el Kage de la aldea receptora. Cobro vencido: 50 % de ingresos futuros.'), '', -1)]))
        @loans.command(name='ver',description='Consultar capital, interés, condiciones y saldo de un contrato')
        async def view(i:discord.Interaction,contrato:int):
            eco.guild(i);r=p.loan(i.guild_id,contrato)
            if not self.ctx['staff'](i):
                try:self.party(i,r['lender'])
                except ValueError:self.party(i,r['borrower'])
            await eco.reply(i,''.join([format_fragment(t('Contrato #'), '', -1), format_fragment(contrato, '', -1), '\n', format_fragment(r['lender'], '', -1), ' → ', format_fragment(r['borrower'], '', -1), format_fragment(t('\nCapital: '), '', -1), format_fragment(r['principal'], ''.join([',']), -1), format_fragment(t(' Ryo · Interés único: '), '', -1), format_fragment(r['interest'], ''.join([',']), -1), format_fragment(t('\nTotal: '), '', -1), format_fragment(r['principal'] + r['interest'], ''.join([',']), -1), format_fragment(t(' · Pagado: '), '', -1), format_fragment(r['paid'], ''.join([',']), -1), format_fragment(t('\nEstado: '), '', -1), format_fragment(r['state'], '', -1), format_fragment(t(' · Vence: '), '', -1), format_fragment(datetime.fromtimestamp(r['due'], TZ), ''.join(['%d/%m/%Y %H:%M']), -1), format_fragment(t('\nCondiciones: '), '', -1), format_fragment(discord.utils.escape_mentions(r['terms']), '', -1)]))
        @loans.command(name='aceptar',description='Aceptar como receptor las condiciones y recibir el capital')
        async def accept(i:discord.Interaction,contrato:int):
            eco.guild(i);r=p.loan(i.guild_id,contrato);self.party(i,r['borrower'])
            async def apply(inter):
                eco.guild(inter);self.party(inter,p.loan(inter.guild_id,contrato)['borrower']);p.accept(inter.guild_id,inter.user.id,contrato);await eco.reply(inter,t('Contrato aceptado y capital transferido.'))
            await eco.reply(i,''.join([format_fragment(t('Aceptar #'), '', -1), format_fragment(contrato, '', -1), format_fragment(t(': recibir '), '', -1), format_fragment(r['principal'], ''.join([',']), -1), format_fragment(t(', devolver '), '', -1), format_fragment(r['principal'] + r['interest'], ''.join([',']), -1), format_fragment(t(' Ryo. Vence '), '', -1), format_fragment(datetime.fromtimestamp(r['due'], TZ), ''.join(['%d/%m/%Y %H:%M']), -1), '.\n', format_fragment(discord.utils.escape_mentions(r['terms']), '', -1)]),view=eco.confirmation(i.user.id,apply))
        @loans.command(name='devolver',description='Devolver voluntariamente todo o parte del préstamo')
        async def repay(i:discord.Interaction,contrato:int,importe:app_commands.Range[int,1,1000000000000]):
            eco.guild(i);self.party(i,p.loan(i.guild_id,contrato)['borrower'])
            async def apply(inter):
                eco.guild(inter);self.party(inter,p.loan(inter.guild_id,contrato)['borrower']);p.repay(inter.guild_id,inter.user.id,contrato,importe);await eco.reply(inter,t('Devolución registrada.'))
            await eco.reply(i,''.join([format_fragment(t('Devolver '), '', -1), format_fragment(importe, ''.join([',']), -1), format_fragment(t(' Ryo en contrato #'), '', -1), format_fragment(contrato, '', -1), '.']),view=eco.confirmation(i.user.id,apply))
        @loans.command(name='cancelar',description='Cancelar o rechazar una oferta todavía no aceptada')
        async def cancel(i:discord.Interaction,contrato:int):
            eco.guild(i);r=p.loan(i.guild_id,contrato)
            try:self.party(i,r['lender'])
            except ValueError:self.party(i,r['borrower'])
            with s.db:
                c=s.db.execute("UPDATE wx_loans SET state='cancelled' WHERE id=? AND state='offered'",(contrato,))
                if not c.rowcount:raise ValueError(t('No se puede cancelar una deuda aceptada.'))
                s.audit(i.guild_id,i.user.id,'loan_cancel',{'id':contrato})
            await eco.reply(i,t('Oferta cancelada.'))
        @loans.command(name='listar',description='Consultar préstamos personales o de una aldea que administras')
        async def listing(i:discord.Interaction,aldea:int|None=None,pagina:app_commands.Range[int,1,100000]=1):
            eco.guild(i);key=f'u:{i.user.id}'
            if aldea is not None:eco.kage(i,aldea);key=f'v:{aldea}'
            rows=s.rows('SELECT * FROM wx_loans WHERE guild=? AND (lender=? OR borrower=?) ORDER BY id DESC LIMIT 10 OFFSET ?',(i.guild_id,key,key,(pagina-1)*10))
            await eco.reply(i,'\n'.join(''.join(['#', format_fragment(r['id'], '', -1), ' ', format_fragment(r['state'], '', -1), ' · ', format_fragment(r['lender'], '', -1), ' → ', format_fragment(r['borrower'], '', -1), format_fragment(t(' · saldo '), '', -1), format_fragment(r['principal'] + r['interest'] - r['paid'], ''.join([',']), -1)]) for r in rows) or t('Sin contratos.'))
        self.bot.tree.add_command(loans)
        treasury=app_commands.Group(name='tesoreria',description='Financiación mensual y acuerdos de guerra')
        @treasury.command(name='autoridad',description='Designar el rol de máxima autoridad para guerras (solo dueño)')
        async def authority(i:discord.Interaction,rol:discord.Role):
            eco.guild(i)
            if not self.ctx['owner'](i):raise ValueError(t('Solo el dueño del bot configura este rol.'))
            if rol.is_default():raise ValueError(t('No se permite @everyone.'))
            with s.db:s.db.execute('UPDATE wx_policy SET authority=? WHERE guild=?',(rol.id,i.guild_id));s.audit(i.guild_id,i.user.id,'authority',{'role':rol.id})
            await eco.reply(i,t('Rol de máxima autoridad configurado.'))
        @treasury.command(name='guerra',description='Transferir el porcentaje acordado del tesoro derrotado (máxima autoridad)')
        async def war(i:discord.Interaction,derrotada:int,ganadora:int,porcentaje:app_commands.Range[int,5,100],acuerdo:str):
            self.authority(i);s.village(i.guild_id,derrotada);s.village(i.guild_id,ganadora);balance=s.balance(i.guild_id,f'v:{derrotada}');n=balance*porcentaje//100
            async def apply(inter):
                self.authority(inter);tx,paid=p.war(inter.guild_id,inter.user.id,derrotada,ganadora,porcentaje,acuerdo,balance,f'war:{i.id}');await eco.reply(inter,''.join([format_fragment(t('Transferencia #'), '', -1), format_fragment(tx, '', -1), ': ', format_fragment(paid, ''.join([',']), -1), format_fragment(t(' Ryo. Las deudas vencidas de la receptora pueden retener el 50 %.'), '', -1)]))
            await eco.reply(i,''.join([format_fragment(t('Tesoro derrotado: '), '', -1), format_fragment(balance, ''.join([',']), -1), format_fragment(t('. Porcentaje: '), '', -1), format_fragment(porcentaje, '', -1), format_fragment(t(' %. Transferencia: '), '', -1), format_fragment(n, ''.join([',']), -1), format_fragment(t('. Saldo derrotada: '), '', -1), format_fragment(balance - n, ''.join([',']), -1), format_fragment(t('. Receptora antes: '), '', -1), format_fragment(s.balance(i.guild_id, f'v:{ganadora}'), ''.join([',']), -1), format_fragment(t('.\nAcuerdo: '), '', -1), format_fragment(discord.utils.escape_mentions(acuerdo), '', -1), format_fragment(t('\nHabitual 10–25 %; cifras superiores requieren acuerdo expreso.'), '', -1)]),view=eco.confirmation(i.user.id,apply))
        @treasury.command(name='estado',description='Ver tesoro, financiación mensual y deuda de una aldea')
        async def treasury_state(i:discord.Interaction,aldea:int):
            eco.kage(i,aldea);v=s.village(i.guild_id,aldea);debts=s.rows("SELECT * FROM wx_loans WHERE guild=? AND borrower=? AND state='active'",(i.guild_id,f'v:{aldea}'))
            await eco.reply(i,''.join([format_fragment(v['name'], '', -1), format_fragment(t(' · Tesoro '), '', -1), format_fragment(s.balance(i.guild_id, f'v:{aldea}'), ''.join([',']), -1), format_fragment(t(' Ryo\nFinanciación: 10.000.000/mes, día 1. Primera entrega: mes siguiente al alta v7.\nDeuda: '), '', -1), format_fragment(sum((r['principal'] + r['interest'] - r['paid'] for r in debts)), ''.join([',']), -1), format_fragment(t('. Retención vencida: 50 %.\nNómina sujeta a actividad semanal.'), '', -1)]))
        self.bot.tree.add_command(treasury)
        activity=app_commands.Group(name='actividad',description='Seguimiento diario semanal y mensual de misiones')
        @activity.command(name='ficha',description='Mostrar actividad del mes y descargar detalle diario en CSV')
        async def activity_card(i:discord.Interaction,mes:str,usuario:discord.Member|None=None):
            eco.guild(i);u=usuario or i.user
            if u.id!=i.user.id:eco.staff(i)
            try:result=p.activity(i.guild_id,u.id,mes)
            except ValueError:raise ValueError(t('Mes con formato AAAA-MM; ejemplo 2026-10.'))
            text=''.join([format_fragment(t('Actividad de '), '', -1), format_fragment(u.display_name, '', -1), ' · ', format_fragment(mes, '', -1), '\n'])+str(result['mensual'])+'\n'+'\n'.join(''.join([format_fragment(t('Semana '), '', -1), format_fragment(w['inicio'], '', -1), ': ', format_fragment(w['total'], '', -1), format_fragment(t('/10 · pendientes '), '', -1), format_fragment(w['pendientes'], '', -1), ' · ', format_fragment('EXENTA' if w['exento'] else 'CUMPLE' if w['cumple'] else t('NO ALCANZA'), '', -1)]) for w in result['semanal'])
            buf=io.StringIO();writer=csv.DictWriter(buf,fieldnames=['fecha','D','C','B','A','S','total']);writer.writerow({'fecha':t('csv.date'),'D':'D','C':'C','B':'B','A':'A','S':'S','total':t('csv.total')});writer.writerows(result['diario'])
            file=discord.File(io.BytesIO(buf.getvalue().encode('utf-8-sig')),filename='actividad-diaria.csv')
            try:await eco.reply(i,text,file=file)
            finally:file.close()
        @activity.command(name='excepcion',description='Justificar una semana por ausencia o ingreso reciente (administración)')
        async def exempt(i:discord.Interaction,usuario:discord.Member,fecha:str,motivo:str):
            eco.admin(i);ws,_=week(timestamp(fecha+' 00:00'))
            if len(motivo.strip())<5:raise ValueError(t('Motivo obligatorio.'))
            with s.db:s.db.execute('INSERT OR REPLACE INTO wx_exempt VALUES(?,?,?,?,?)',(i.guild_id,usuario.id,ws,motivo,i.user.id));s.audit(i.guild_id,i.user.id,'activity_exempt',{'user':usuario.id,'week':ws,'reason':motivo})
            await eco.reply(i,t('Semana justificada. No modifica nóminas ya cerradas; esas requieren un ajuste registrado.'))
        self.bot.tree.add_command(activity)
        life=app_commands.Group(name='personaje',description='Nueva vida y muerte definitiva')
        @life.command(name='permadeath',description='Archivar personaje y sortear legado una sola vez (administración)')
        async def death(i:discord.Interaction,usuario:discord.Member,motivo:str):
            eco.admin(i);r=self.ctx['STORE'].find(i.guild_id,usuario.id)
            if not r:raise ValueError(t('No tiene ficha.'))
            async def apply(inter):
                eco.admin(inter);cleanup={'user':usuario.id,'roles':list(self.ctx['assigned_roles'](r)),'messages':[[self.ctx['REVIEW'],r['review_message']]] if r['review_message'] else []}
                if r['public_message']:cleanup['messages'].append([self.ctx['PUBLIC'],r['public_message']])
                ident,legacy=p.death(inter.guild_id,usuario.id,inter.user.id,motivo,r['revision'],cleanup);await eco.reply(inter,''.join([format_fragment(t('Vida #'), '', -1), format_fragment(ident, '', -1), format_fragment(t(' archivada. Legado: '), '', -1), format_fragment(legacy['money'], ''.join([',']), -1), format_fragment(t(' Ryo y '), '', -1)])+', '.join(content_text(x['data']) for x in legacy['items'])+t('. Crea la nueva ficha y luego usa /legado reclamar.'))
            await eco.reply(i,t('Muerte definitiva: archiva la ficha, retira el 75 % del saldo y conserva 25 %, un arma, un mode y una técnica al azar si existen. Cancela operaciones pendientes; conserva deudas personales. Confirma solo después de resolver misiones y nóminas pendientes.'),view=eco.confirmation(i.user.id,apply))
        self.bot.tree.add_command(life)
        legacy=app_commands.Group(name='legado',description='Clasificación y entrega del legado del personaje')
        @legacy.command(name='clasificar',description='Clasificar un permiso como mode técnica o ninguno para el legado')
        async def classify_item(i:discord.Interaction,articulo:int,tipo:Literal['mode','tecnica','ninguno']):
            eco.admin(i);item=s.item(i.guild_id,articulo)
            if item['data']['kind']!='permiso':raise ValueError(t('Solo se clasifican permisos; armas se reconocen automáticamente.'))
            with s.db:
                d=item['data'];d['legacy_type']=tipo;s.db.execute('UPDATE eco_items SET data=?,version=version+1 WHERE id=?',(dump(d),articulo))
                for lot in s.rows('SELECT * FROM eco_inventory WHERE guild=? AND item=?',(i.guild_id,articulo)):
                    ld=json.loads(lot['data']);ld['legacy_type']=tipo;s.db.execute('UPDATE eco_inventory SET data=? WHERE id=?',(dump(ld),lot['id']))
                s.audit(i.guild_id,i.user.id,'legacy_classification',{'item':articulo,'type':tipo})
            await eco.reply(i,t('Categoría de legado aplicada al artículo y sus ejemplares existentes.'))
        @legacy.command(name='reclamar',description='Recibir el legado después de aprobar tu nuevo personaje')
        async def claim(i:discord.Interaction):
            await eco.player(i);result=p.claim(i.guild_id,i.user.id);await eco.reply(i,''.join([format_fragment(t('Legado entregado: '), '', -1), format_fragment(result['money'], ''.join([',']), -1), format_fragment(t(' Ryo y '), '', -1), format_fragment(len(result['items']), '', -1), format_fragment(t(' objetos/permisos. Sus requisitos siguen vigentes.'), '', -1)]))
        self.bot.tree.add_command(legacy)
        self.install_reports();self.install_events()
    def install_reports(self):
        eco=self.eco;s=self.s;ms=self.ms
        group=app_commands.Group(name='reportes',description='Encargos azules y revisión de misiones por Kages')
        @group.command(name='azul',description='Crear reporte de una misión D realizada fuera de Open World')
        async def blue(i:discord.Interaction,tipo:Literal['azul_hierba','azul_gato','azul_oficina','azul_compras','azul_mensaje','azul_grafiti','azul_limpieza']):
            self.ctx['MISSIONS'].player(i)
            await eco.player(i);ms.registered(i.guild_id,i.user.id)
            with s.db:
                data={'type':tipo,'enemy':'none','members':[i.user.id],'opponents':'Sin enemigo','notes':'','start':'','end':''}
                c=s.db.execute("INSERT INTO mission_reports(guild,session,owner,kind,data,created) VALUES(?,0,?,'mission',?,?)",(i.guild_id,i.user.id,dump(data),ms.now()))
                ms.log(i.guild_id,c.lastrowid,i.user.id,'blue_start',{})
            row=ms.get(c.lastrowid)
            await eco.reply(i,t('Encargo D individual. Completa los datos y las capturas después de realizarlo; no requiere sesión Open World.'),embed=self.ctx['MISSIONS'].card(row),view=self.ctx['MISSIONS'].Wizard(row))
        @group.command(name='ver',description='Ver todas las capturas del reporte como participante staff o Kage autorizado')
        async def view(i:discord.Interaction,reporte:int):
            row=ms.get(reporte);self.report_access(i,row);await self.display_report(i,row)
        @group.command(name='clasificar',description='Asignar rango a una misión pendiente con motivo y evidencia del enemigo')
        async def classify(i:discord.Interaction,reporte:int,rango:Literal['D','C','B','A','S'],motivo:str):
            row=ms.get(reporte);self.report_access(i,row,True)
            if row['kind']!='mission' or row['state']!='pending':raise ValueError(t('Solo misiones pendientes; devuelve una aprobada para corregirla.'))
            if row['session']==0 and rango!='D':raise ValueError(t('Los encargos azules siempre son D.'))
            if len(motivo.strip())<10:raise ValueError(t('Explica el enemigo, su rango y la razón de la clasificación.'))
            d=row['data'];d['classification']={'rank':rango,'actor':i.user.id,'reason':motivo};d['mission_rank']=rango
            with s.db:
                s.db.execute('UPDATE mission_reports SET data=?,revision=revision+1 WHERE id=? AND revision=?',(dump(d),reporte,row['revision']));ms.log(i.guild_id,reporte,i.user.id,'classification',d['classification'])
            await eco.reply(i,t('Clasificación registrada. Revisa /reportes ver antes de resolver.'))
        @group.command(name='resolver',description='Aprobar corregir rechazar congelar o reabrir una misión de tu aldea')
        async def resolve(i:discord.Interaction,reporte:int,accion:Literal['aprobar','corregir','rechazar','congelar','reabrir'],motivo:str):
            row=ms.get(reporte);self.report_access(i,row,True)
            if row['kind']!='mission' and not self.ctx['staff'](i):raise ValueError(t('Las autorizaciones de estadísticas requieren staff.'))
            actions={'aprobar':'approve','corregir':'correct','rechazar':'reject','congelar':'freeze','reabrir':'reopen'}
            ms.decision(reporte,row['revision'],i.user.id,actions[accion],motivo)
            await eco.reply(i,t('Decisión guardada. Aprobar no realiza el pago; usa /pagos mision.'))
        @group.command(name='plantilla',description='Crear o editar un tipo de misión de rol (administración)')
        async def template(i:discord.Interaction,clave:str,nombre:str,rango_base:Literal['D','C','B','A','S']):
            eco.admin(i)
            if not clave.isidentifier() or len(clave)>40 or clave.startswith('azul_'):raise ValueError(t('Clave sin espacios ni símbolos, máximo 40; azul_ está reservado.'))
            if not 1<=len(nombre)<=80:raise ValueError(t('Nombre entre 1 y 80 caracteres.'))
            system=self.ctx['MISSIONS'];system.config['types'][clave]={'label':nombre,'base_rank':rango_base}
            path=self.ctx['BASE']/'misiones_config.json';temp=path.with_suffix('.tmp');temp.write_text(json.dumps(system.config,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
            with s.db:s.audit(i.guild_id,i.user.id,'mission_template',{'key':clave,'rank':rango_base,'name':nombre})
            await eco.reply(i,t('Plantilla guardada. Si hay más de 25 tipos, usa /reportes nueva para elegir por clave.'))
        @group.command(name='nueva',description='Crear reporte de sesión eligiendo una plantilla por su clave')
        async def new(i:discord.Interaction,plantilla:str,sesion:int|None=None):
            self.ctx['MISSIONS'].player(i)
            if plantilla not in ms.config['types'] or plantilla in BLUE:raise ValueError(t('Plantilla Open World inexistente; para azules usa /reportes azul.'))
            row=ms.start(i.guild_id,i.user.id,'mission',sesion);row=ms.edit(row['id'],row['revision'],i.user.id,{**row['data'],'type':plantilla})
            await eco.reply(i,embed=self.ctx['MISSIONS'].card(row),view=self.ctx['MISSIONS'].Wizard(row))
        @group.command(name='lote',description='Preparar varios reportes de la misma plantilla sin repetir la squad')
        async def batch(i:discord.Interaction,plantilla:str,cantidad:app_commands.Range[int,1,10],companeros:str='',sesion:int|None=None):
            self.ctx['MISSIONS'].player(i)
            if plantilla not in ms.config['types'] or plantilla in BLUE:raise ValueError(t('Usa una plantilla Open World válida.'))
            import re
            members=sorted({i.user.id,*map(int,re.findall(r'\d+',companeros))})
            if len(members)>ms.config['max_squad']:raise ValueError(t('La squad supera el máximo configurado.'))
            session=ms.session(sesion) if sesion else ms.current(i.guild_id)
            if not session or session['guild']!=i.guild_id:raise ValueError(t('Sesión inexistente.'))
            for u in members:
                ms.registered(i.guild_id,u);a=ms.authorization(session['id'],u)
                if not a or a['state']!='approved':raise ValueError(t('Todos necesitan autorización aprobada en esa sesión.'))
            ids=[]
            with s.db:
                for _ in range(cantidad):
                    c=s.db.execute("INSERT INTO mission_reports(guild,session,owner,kind,data,created) VALUES(?,?,?,'mission',?,?)",(i.guild_id,session['id'],i.user.id,dump({'members':members,'type':plantilla}),ms.now()));ids.append(c.lastrowid)
                s.audit(i.guild_id,i.user.id,'mission_batch',{'reports':ids})
            await eco.reply(i,t('Reportes preparados: ')+', '.join(map(str,ids))+t('. Usa /misiones continuar para el horario, resultado y capturas de cada uno. Cada misión se revisa y paga por separado.'))
        self.bot.tree.add_command(group)
    def install_events(self):
        eco=self.eco;s=self.s
        group=app_commands.Group(name='eventos',description='Presupuestos y pagos acordados por Kages')
        def get(g,ident):
            r=s.db.execute('SELECT * FROM wx_events WHERE guild=? AND id=?',(g,ident)).fetchone()
            if not r:raise ValueError(t('Evento inexistente.'))
            return dict(r)
        @group.command(name='crear',description='Reservar presupuesto del tesoro para un evento examen o torneo')
        async def create(i:discord.Interaction,aldea:int,nombre:str,presupuesto:app_commands.Range[int,1,1000000000000],detalles:str):
            eco.kage(i,aldea)
            if not 1<=len(nombre)<=80 or not 5<=len(detalles)<=1500:raise ValueError(t('Nombre hasta 80 y detalles de 5 a 1500 caracteres.'))
            async def apply(inter):
                eco.kage(inter,aldea);s.live(inter.guild_id)
                with s.db:
                    s.db.execute('BEGIN IMMEDIATE');cur=s.db.execute('INSERT INTO wx_events(guild,village,name,budget,details) VALUES(?,?,?,?,?)',(inter.guild_id,aldea,nombre,presupuesto,detalles));ident=cur.lastrowid
                    s._move(inter.guild_id,f'event:{ident}:reserve',inter.user.id,t('Reserva de evento'),[(f'v:{aldea}',-presupuesto),(f'event:{ident}',presupuesto)])
                    s.audit(inter.guild_id,inter.user.id,'event_create',{'id':ident,'village':aldea,'budget':presupuesto})
                await eco.reply(inter,''.join([format_fragment(t('Evento #'), '', -1), format_fragment(ident, '', -1), format_fragment(t(' creado. Presupuesto reservado: '), '', -1), format_fragment(presupuesto, ''.join([',']), -1), ' Ryo.']))
            await eco.reply(i,''.join([format_fragment(t('Reservar '), '', -1), format_fragment(presupuesto, ''.join([',']), -1), format_fragment(t(' Ryo para '), '', -1), format_fragment(discord.utils.escape_mentions(nombre), '', -1), '.']),view=eco.confirmation(i.user.id,apply))
        @group.command(name='pagar',description='Pagar un servicio o premio del evento con referencia única y evidencia')
        async def pay(i:discord.Interaction,evento:int,usuario:discord.Member,importe:app_commands.Range[int,1,1000000000000],referencia:str,motivo:str,evidencia:str):
            r=get(i.guild_id,evento);eco.kage(i,r['village'])
            if not 1<=len(referencia)<=80 or len(motivo)<5 or len(evidencia)<5:raise ValueError(t('Referencia, motivo y evidencia obligatorios.'))
            async def apply(inter):
                fresh=get(inter.guild_id,evento);eco.kage(inter,fresh['village']);await eco.sync(inter.guild_id,usuario.id);s.player(inter.guild_id,usuario.id)
                with s.db:
                    s.db.execute('BEGIN IMMEDIATE');fresh=get(inter.guild_id,evento)
                    if fresh['state']!='open':raise ValueError(t('Evento cerrado.'))
                    tx=s._move(inter.guild_id,f'event:{evento}:{referencia}',inter.user.id,motivo,[(f'event:{evento}',-importe),(f'u:{usuario.id}',importe)])
                    s.db.execute('INSERT INTO wx_event_payments VALUES(?,?,?)',(evento,referencia,tx));s.db.execute('UPDATE wx_events SET spent=spent+? WHERE id=?',(importe,evento))
                    s.audit(inter.guild_id,inter.user.id,'event_pay',{'event':evento,'user':usuario.id,'amount':importe,'evidence':evidencia,'reference':referencia})
                await eco.reply(inter,''.join([format_fragment(t('Pago #'), '', -1), format_fragment(tx, '', -1), format_fragment(t(' registrado.'), '', -1)]))
            await eco.reply(i,''.join([format_fragment(t('Pagar '), '', -1), format_fragment(importe, ''.join([',']), -1), format_fragment(t(' a '), '', -1), format_fragment(usuario.display_name, '', -1), format_fragment(t(' por '), '', -1), format_fragment(discord.utils.escape_mentions(motivo), '', -1), format_fragment(t('. Ref. '), '', -1), format_fragment(referencia, '', -1)]),view=eco.confirmation(i.user.id,apply))
        @group.command(name='ver',description='Consultar presupuesto reservado gastos y condiciones del evento')
        async def view(i:discord.Interaction,evento:int):
            r=get(i.guild_id,evento);eco.kage(i,r['village'])
            await eco.reply(i,''.join([format_fragment(t('Evento #'), '', -1), format_fragment(evento, '', -1), ': ', format_fragment(discord.utils.escape_mentions(r['name']), '', -1), format_fragment(t('\nEstado: '), '', -1), format_fragment(r['state'], '', -1), format_fragment(t(' · Presupuesto '), '', -1), format_fragment(r['budget'], ''.join([',']), -1), format_fragment(t(' · Gastado '), '', -1), format_fragment(r['spent'], ''.join([',']), -1), format_fragment(t(' · Disponible '), '', -1), format_fragment(s.balance(i.guild_id, f'event:{evento}'), ''.join([',']), -1), '\n', format_fragment(discord.utils.escape_mentions(r['details']), '', -1)]))
        @group.command(name='cerrar',description='Cerrar el evento y devolver el presupuesto no utilizado a su tesoro')
        async def close(i:discord.Interaction,evento:int):
            r=get(i.guild_id,evento);eco.kage(i,r['village'])
            async def apply(inter):
                r=get(inter.guild_id,evento);eco.kage(inter,r['village'])
                with s.db:
                    s.db.execute('BEGIN IMMEDIATE');r=get(inter.guild_id,evento)
                    if r['state']!='open':raise ValueError(t('Evento ya cerrado.'))
                    n=s.balance(inter.guild_id,f'event:{evento}');s._raw_move(inter.guild_id,f'event:{evento}:close',inter.user.id,t('Devolución de reserva'),[(f'event:{evento}',-n),(f'v:{r["village"]}',n)])
                    s.db.execute("UPDATE wx_events SET state='closed' WHERE id=?",(evento,))
                await eco.reply(inter,t('Evento cerrado; remanente devuelto sin considerarse ingreso nuevo.'))
            await eco.reply(i,t('Cerrar el evento y devolver su saldo reservado.'),view=eco.confirmation(i.user.id,apply))
        self.bot.tree.add_command(group)
