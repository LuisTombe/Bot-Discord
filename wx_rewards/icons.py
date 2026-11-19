"""URLs de iconos de la wiki, cacheadas; jamás descarga imágenes al proceso del bot."""
import json
from urllib.parse import urlparse,unquote
import aiohttp

WIKI='shindo-life-rell.fandom.com'

async def resolve(item,overrides):
    if overrides.get(item['id']):return overrides[item['id']]
    if item.get('image_url'):return item['image_url']
    parsed=urlparse(item.get('wiki_url',''))
    if parsed.hostname!=WIKI or not parsed.path.startswith('/wiki/'):return None
    title=unquote(parsed.path[len('/wiki/'):])
    timeout=aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout,headers={'User-Agent':'WorldXBot/1.0 (wiki image attribution)'}) as client:
        async with client.get('https://'+WIKI+'/api.php',params={'action':'query','format':'json','prop':'pageimages','piprop':'original','titles':title}) as r:
            if r.status!=200:return None
            payload=await r.json()
            for page in payload.get('query',{}).get('pages',{}).values():
                url=page.get('original',{}).get('source')
                if url and urlparse(url).hostname=='static.wikia.nocookie.net':return url
    return None
