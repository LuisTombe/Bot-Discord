"""Private screenshot uploads, saved locally rather than expiring CDN URLs."""
from i18n import format_fragment, t
import hashlib
from pathlib import Path
import uuid

KINDS=('personaje','username','estadisticas')
TITLES={'personaje':'1. Personaje de Shindo y nivel',
        'username':'2. Username de Roblox',
        'estadisticas':'3. Stats, slots y edad en Shindo'}
MAX_BYTES=8*1024*1024
INSTRUCTIONS=(
    'Antes de enviar tu ficha, crea y prepara tu personaje y sube **tres capturas distintas y legibles**:\n\n'
    '**1. Personaje en Shindo:** debe verse su apariencia y el nivel.\n'
    '**2. Cuenta Roblox:** muestra el username real de tu cuenta (el @usuario, no solo el Displayname).\n'
    '**3. Menú de Shindo:** muestra las estadísticas, la edad del personaje y los slots de bloodlines y elementos. '
    'Si están en pantallas separadas, reúne esas vistas en una sola imagen sin ocultar ni alterar los datos.\n\n'
    'Usa capturas actuales del mismo personaje y cuenta que registraste. PNG, JPG o WEBP; máximo **8 MiB por imagen**. '
    'Cada campo admite una imagen. El staff revisará su contenido.\n\n'
    'Las tres capturas acompañarán la ficha en revisión y, si se aprueba, en el canal de fichas aprobadas. '
    'No incluyas contraseñas, correo ni códigos de acceso. Pulsa **Subir las 3 capturas** cuando estén listas.'
)


def image_extension(blob):
    if not blob or len(blob)>MAX_BYTES:
        raise ValueError(t('Cada captura debe pesar entre 1 byte y 8 MiB.'))
    if blob.startswith(b'\x89PNG\r\n\x1a\n'): return '.png'
    if blob.startswith(b'\xff\xd8\xff'): return '.jpg'
    if blob.startswith(b'RIFF') and blob[8:12]==b'WEBP': return '.webp'
    raise ValueError(t('Sube imágenes PNG, JPG o WEBP; no documentos ni vídeos.'))


async def receive(attachments, root):
    """Validate the entire set before writing. Download only Discord attachments."""
    if set(attachments)!=set(KINDS):
        raise ValueError(t('Se requieren las tres capturas: personaje, username y estadísticas.'))
    blobs={}
    for kind in KINDS:
        a=attachments[kind]
        if not 0<a.size<=MAX_BYTES:
            raise ValueError(''.join([format_fragment(TITLES[kind], '', -1), format_fragment(t(': máximo 8 MiB.'), '', -1)]))
        if a.content_type not in ('image/png','image/jpeg','image/webp') or not a.width or not a.height:
            raise ValueError(''.join([format_fragment(TITLES[kind], '', -1), format_fragment(t(': selecciona una imagen PNG, JPG o WEBP válida.'), '', -1)]))
        blob=await a.read()
        extension=image_extension(blob)
        digest=hashlib.sha256(blob).hexdigest()
        blobs[kind]=(blob,extension,digest)
    if len({v[2] for v in blobs.values()})!=3:
        raise ValueError(t('Las tres capturas deben ser diferentes; no repitas el mismo archivo.'))
    root=Path(root); batch=uuid.uuid4().hex
    entries=[]
    try:
        for kind,(blob,extension,digest) in blobs.items():
            relative=f'capturas/{batch}/{kind}{extension}'
            path=root/relative; path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(blob)
            entries.append({'kind':kind,'path':relative,'sha256':digest,'size':len(blob)})
    except BaseException:
        discard(entries,root)
        raise
    return entries


def safe_path(root,relative):
    root=Path(root).resolve()
    path=(root/relative).resolve()
    if not path.is_relative_to(root/'capturas'):
        raise ValueError(t('Ruta de captura inválida.'))
    return path


def discard(entries,root):
    for entry in entries:
        p=safe_path(root,entry['path'])
        p.unlink(missing_ok=True)
        try: p.parent.rmdir()
        except OSError: pass
