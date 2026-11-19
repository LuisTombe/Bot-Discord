"""Transacciones componibles: una aprobación y su pago se confirman juntos."""
from contextlib import contextmanager
from uuid import uuid4

@contextmanager
def atomic(db):
    nested = db.in_transaction
    name = 'wx_' + uuid4().hex
    db.execute('SAVEPOINT ' + name if nested else 'BEGIN IMMEDIATE')
    try:
        yield
    except BaseException:
        if nested:
            db.execute('ROLLBACK TO ' + name)
            db.execute('RELEASE ' + name)
        else:
            db.rollback()
        raise
    else:
        if nested:
            db.execute('RELEASE ' + name)
        else:
            db.commit()
