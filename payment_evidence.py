"""Validated mission-payment evidence and immutable registration snapshots."""
from i18n import t
import hashlib
import io
from PIL import Image, UnidentifiedImageError
from evidence import MAX_BYTES, image_extension, safe_path

CAPTURE_GUIDE = 'Revisa pantalla completa, fecha y hora del dispositivo, username y objetivo. Permanencia: 3 segundos o menos. Ejecución: objetivo abatido y jugador ejecutándolo. Una captura suficiente si contiene toda la evidencia; no se certifica automáticamente.'

def validate_image(blob):
    extension=image_extension(blob)
    try:
        with Image.open(io.BytesIO(blob)) as image:
            if image.format not in ('PNG','JPEG','WEBP') or image.width*image.height>40000000:
                raise ValueError(t('La captura debe ser PNG/JPG/WEBP de hasta 40 megapíxeles.'))
            image.verify()
    except (UnidentifiedImageError,OSError,SyntaxError,Image.DecompressionBombError) as exc:
        raise ValueError(t('La imagen está dañada o no es una captura válida.')) from exc
    return extension,hashlib.sha256(blob).hexdigest()

def identity(registration,rank,discord_username=None,discord_display=None):
    d=registration['data']
    return {'user':registration['user'],'registration':registration['id'],
            'registration_revision':registration['revision'],'roblox_username':d.get('roblox_name',''),
            'name':d.get('character_name') or d.get('roblox_name',''),'age':d.get('age',''),
            'discord_registered':d.get('discord_name',''),
            'discord_username':discord_username or d.get('discord_name',''),
            'displayname':d.get('display_name',''),
            'discord_display':discord_display or '', 'rank':rank}

def same_registration(snapshot,registration):
    if not registration or registration['state']!='approved':return False
    fresh=identity(registration,snapshot['rank'])
    return all(snapshot.get(key)==fresh.get(key) for key in ('user','registration','registration_revision','name','age','discord_registered','displayname'))

def file_blob(root,relative):
    path=safe_path(root,relative)
    if not path.is_file() or not 0<path.stat().st_size<=MAX_BYTES:raise ValueError(t('No se encuentra una captura válida del reporte. Adjunta la imagen.'))
    blob=path.read_bytes();validate_image(blob);return blob
