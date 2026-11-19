"""Bilingual entry panels; detailed instructions are shown privately in the chosen language."""
import discord
from i18n import t, using


def panel_embeds(kind):
    result = []
    for lang in ('es', 'en'):
        with using(lang):
            result.append(discord.Embed(title=t('panel.'+kind+'.title'), description=t('panel.'+kind+'.body'), color=0xBC904A).set_footer(text='Español' if lang=='es' else 'English'))
    return result
