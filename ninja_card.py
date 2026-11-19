"""Read-only ninja identity card. Explicit allowlist; never render registration embeds."""
from datetime import datetime,timezone,timedelta
from i18n import format_fragment, number, t
import discord

RANKS=('D','C','B','A','S')

def text(value,limit=180):
    value=str(value or '').strip()
    return discord.utils.escape_markdown(discord.utils.escape_mentions(value[:limit])) or t('Sin registrar')

def data_for(registrations,economy,missions,guild,user,discord_username):
    # No new identities or balances: one Discord ID joins the existing three systems.
    registration=registrations.find(guild,user)
    d=registration['data'] if (registration and registration['state']=='approved') else {}
    totals=missions.totals(guild,user)
    return {
        'registration':registration['id'] if (registration and registration['state']=='approved') else 0,
        'user':user,
        'name':d.get('character_name') or d.get('display_name') or d.get('roblox_name') or discord_username,
        'roblox_username':d.get('roblox_name'),
        'discord_username':discord_username,
        'displayname':d.get('display_name'),
        'gender':d.get('gender'), 'age':d.get('age'),
        'balance':economy.balance(guild,f'u:{user}'),
        'missions':{r:int(totals.get(r,0)) for r in RANKS},
    }

def card(data):
    e=discord.Embed(title=t('📜 忍 · Tarjeta de información ninja'),color=0xBC904A,
        description=t('**WORLD X · ARCHIVO SHINOBI**'),timestamp=datetime.now(timezone.utc))
    for name,key in [(t('🥷 Nombre del ninja'),'name'),(t('🎮 Usuario Roblox'),'roblox_username'),
                     (t('💬 Username Discord'),'discord_username'),(t('🏷️ Displayname del registro'),'displayname'),
                     (t('⚧ Género del personaje'),'gender'),(t('🎂 Edad del personaje'),'age')]:
        e.add_field(name=name,value=text(data[key]),inline=True)
    e.add_field(name=t('💰 Cartera'),value=f"**{number(data['balance'])} Ryo**",inline=False)
    icons={'D':'⚪','C':'🟢','B':'🔵','A':'🟣','S':'🟡'}
    e.add_field(name=t('📚 Misiones completadas por rango'),inline=False,
        value='\n'.join(''.join([format_fragment(icons[r], '', -1), format_fragment(t(' **Rango '), '', -1), format_fragment(r, '', -1), ':** ', format_fragment(data['missions'][r], '', -1)]) for r in RANKS))
    e.add_field(name=t('🏆 Total de misiones aprobadas'),value=str(sum(data['missions'].values())),inline=False)
    e.set_footer(text=''.join([format_fragment(t('Ficha #'), '', -1), format_fragment(data['registration'], '', -1), format_fragment(t(' · Datos actuales al consultar · Pulsa Actualizar para recargar'), '', -1)]))
    return e

async def show(economy,interaction,replace=False,target=None):
    economy.guild(interaction)
    subject=target or interaction.user
    payload=data_for(economy.ctx['STORE'],economy.store,economy.ctx['MISSIONS'].store,
                     interaction.guild_id,subject.id,subject.name)
    actor=interaction.user.id
    class Controls(economy.ctx['SafeView']):
        def __init__(self):super().__init__(timeout=900)
        async def interaction_check(self,i):
            if i.user.id!=actor:
                await economy.reply(i,t('Esta tarjeta pertenece a otro shinobi. Abre la tuya con /tarjeta_ninja.'));return False
            return True
        @discord.ui.button(label='🔄 Actualizar',style=discord.ButtonStyle.primary)
        async def refresh(self,i,b):
            # Also enforce ownership here for direct callback entry points.
            if i.user.id!=actor:raise ValueError(t('Abre tu propia tarjeta con /tarjeta_ninja.'))
            await show(economy,i,replace=True,target=subject)
    embed=card(payload)
    if replace:await interaction.response.edit_message(embed=embed,view=Controls())
    else:await economy.reply(interaction,embed=embed,view=Controls())
