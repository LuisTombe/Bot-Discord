"""Discord integration; callbacks retain their original stable command/value IDs."""
from functools import wraps
import json
import discord
from discord import app_commands
import i18n
from i18n import t

COMMANDS = json.loads((i18n.ROOT / 'commands.en.json').read_text(encoding='utf-8'))


def command_name(qualified_name, lang=None):
    if (lang or i18n.language()) != 'en':
        return qualified_name
    return ' '.join(COMMANDS['names'].get(part, part) for part in qualified_name.split())


class Translator(app_commands.Translator):
    async def translate(self, string, locale, context):
        if str(locale) not in ('en-US', 'en-GB'):
            return None
        source = str(string)
        location = context.location.name
        if location in ('command_name', 'group_name'):
            return COMMANDS['names'].get(source)
        if location == 'parameter_name':
            return COMMANDS['parameters'].get(source)
        if location in ('command_description', 'group_description'):
            return COMMANDS['descriptions'].get(source)
        if location == 'parameter_description':
            if source == '…':
                name = context.data.name
                return COMMANDS['parameters'].get(name, name).replace('_', ' ').capitalize()
            return i18n.CATALOGS['en'].get(source)
        if location == 'choice_name':
            return i18n.CATALOGS['en'].get(source)
        return None


class LocalizedTree(app_commands.CommandTree):
    async def interaction_check(self, interaction):
        i18n.bind(interaction)
        return True


def _bound_check(callback):
    @wraps(callback)
    async def checked(self, interaction):
        i18n.bind(interaction)
        return await callback(self, interaction)
    return checked


class LocalizedView(discord.ui.View):
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if 'interaction_check' in cls.__dict__:
            cls.interaction_check = _bound_check(cls.interaction_check)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # These are class-declared buttons, not labels supplied by a player.
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.label:
                item.label = t(item.label)

    async def interaction_check(self, interaction):
        i18n.bind(interaction)
        return True


class LocalizedModal(discord.ui.Modal):
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if 'interaction_check' in cls.__dict__:
            cls.interaction_check = _bound_check(cls.interaction_check)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.title = t(self.title)
        for item in self.children:
            if isinstance(item, discord.ui.TextInput):
                item.label = t(item.label)
                if item.placeholder:
                    item.placeholder = t(item.placeholder)

    async def interaction_check(self, interaction):
        i18n.bind(interaction)
        return True


def install(bot, ctx):
    preferences = i18n.configure(ctx['STORE'].db)

    @bot.tree.command(name='idioma', description='Elegir el idioma de tus respuestas: español, inglés o automático')
    @app_commands.guild_only()
    @app_commands.choices(seleccion=[
        app_commands.Choice(name='Automático / Automatic', value='auto'),
        app_commands.Choice(name='Español', value='es'),
        app_commands.Choice(name='English', value='en'),
    ])
    async def choose_language(interaction: discord.Interaction, seleccion: app_commands.Choice[str]):
        if interaction.guild_id != ctx['GUILD']:
            raise ValueError(t('Usa el servidor configurado.'))
        preferences.set(interaction.guild_id, interaction.user.id, seleccion.value)
        i18n.bind(interaction)
        await interaction.response.send_message(t('language.saved'), ephemeral=True)

    COMMANDS['descriptions']['Elegir el idioma de tus respuestas: español, inglés o automático'] = 'Choose your response language: Spanish, English or automatic'
    return preferences


def public_output(parameter):
    """Render shared messages in the channel's language, not the last reviewer's."""
    import inspect
    def decorate(callback):
        signature = inspect.signature(callback)
        @wraps(callback)
        async def wrapped(*args, **kwargs):
            arguments = signature.bind_partial(*args, **kwargs).arguments
            channel = arguments.get(parameter)
            if not isinstance(channel, discord.TextChannel):
                return await callback(*args, **kwargs)
            with i18n.channel_scope(channel.guild.id, channel.id):
                view = arguments.get('view')
                if view:
                    reverse = {v: k for k, v in i18n.CATALOGS['en'].items()}
                    for child in view.children:
                        if isinstance(child, discord.ui.Button) and child.label:
                            child.label = t(reverse.get(child.label, child.label))
                return await callback(*args, **kwargs)
        return wrapped
    return decorate


def audit_output(callback):
    @wraps(callback)
    async def wrapped(self, guild, *args, **kwargs):
        channel = self.store.settings(guild).get('log_channel', 0)
        with i18n.channel_scope(guild, channel):
            return await callback(self, guild, *args, **kwargs)
    return wrapped


def install_channel_language(bot, ctx):
    COMMANDS['names']['idioma_canal'] = 'channel_language'
    COMMANDS['descriptions']['Elegir el idioma de fichas y avisos nuevos en un canal'] = 'Choose the language of new shared cards and notices in a channel'
    @bot.tree.command(name='idioma_canal', description='Elegir el idioma de fichas y avisos nuevos en un canal')
    @app_commands.guild_only()
    async def channel_language(interaction: discord.Interaction, canal: discord.TextChannel, seleccion: str):
        if interaction.guild_id != ctx['GUILD'] or canal.guild.id != interaction.guild_id:
            raise ValueError(t('Usa el servidor configurado.'))
        if not (ctx['owner'](interaction) or interaction.user.guild_permissions.manage_guild):
            raise ValueError(t('Necesitas Gestionar servidor.'))
        if seleccion not in ('es', 'en'):
            raise ValueError('es / en')
        with ctx['STORE'].db:
            ctx['STORE'].db.execute('INSERT INTO language_channels VALUES(?,?,?) ON CONFLICT(guild,channel) DO UPDATE SET language=excluded.language', (interaction.guild_id, canal.id, seleccion))
        await interaction.response.send_message(t('channel.saved'), ephemeral=True)
    app_commands.choices(seleccion=[app_commands.Choice(name='Español',value='es'),app_commands.Choice(name='English',value='en')])(channel_language)
