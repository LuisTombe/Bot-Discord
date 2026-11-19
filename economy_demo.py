"""Isolated demonstration using the real economy service and a disposable database."""
from i18n import t
import tempfile
from pathlib import Path
from datetime import datetime
from domain import Store
from economy_domain import Economy,TZ,RANKS

def run_demo():
    with tempfile.TemporaryDirectory(prefix='worldx_economy_test_') as folder:
        reg=Store(Path(folder)/'demo.sqlite3')
        try:
            clock=[int(datetime(2026,10,1,tzinfo=TZ).timestamp())];s=Economy(reg,clock=lambda:clock[0])
            village=s.add_village(1,99,'Aldea de pruebas',10,20,15000000);s.configure(1,99,enabled=True)
            keys=['genin']*5+['chunin']*2+['jounin','sensei','kage']
            for index,key in enumerate(RANKS):s.rank(1,99,key,1000+index)
            for index,key in enumerate(keys):
                user=100+index
                with reg.db:reg.db.execute("INSERT INTO registrations(guild,user,state,data) VALUES(1,?,'approved','{}')",(user,))
                s.observe(1,user,[10,1000+list(RANKS).index(key)])
            clock[0]=int(datetime(2026,11,1,tzinfo=TZ).timestamp());paid=s.pay_payroll(1)
            before=s.balance(1,'u:100');again=s.pay_payroll(1)
            if again or before!=s.balance(1,'u:100'):raise RuntimeError('Falló la protección de nómina.')
            category=s.category(1,99,'Armas');item=s.save_item(1,99,village,category,{'name':'Kunai de prueba','kind':'arma','rank':'D','price':1000},1)
            s.buy(1,100,item,1,1,'buy:demo');lot=s.rows('SELECT id FROM eco_inventory WHERE user=100')[0]['id']
            deal=s.propose(1,100,101,lot,1,500);s.accept(1,101,deal)
            duplicated=False
            try:s.accept(1,101,deal);duplicated=True
            except ValueError:pass
            if duplicated:raise RuntimeError('Falló la protección de intercambio.')
            total=reg.db.execute('SELECT SUM(balance) FROM eco_accounts').fetchone()[0]
            if total!=15000000:raise RuntimeError('Falló la conservación de Ryo.')
            return t('economy.demo_summary', paid=paid)
        finally:reg.db.close()
