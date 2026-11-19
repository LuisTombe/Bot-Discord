"""Staff-authored catalogue translations; prices, IDs and permissions remain unchanged."""
import copy
import json
import discord
from discord import app_commands
from i18n import t, language, CATALOGS
from discord_i18n import COMMANDS


def content_text(data, field='name'):
    original = data.get(field, '')
    if language() == 'es' or not original:
        return original
    translation = data.get('translations', {}).get('en', {})
    if translation.get('source', {}).get(field) == original and translation.get(field):
        return translation[field]
    return original + ' [original]'


class ContentTranslations:
    def __init__(self, bot, ctx):
        self.ctx = ctx
        self.eco = ctx['ECONOMY']
        self.db = ctx['STORE'].db
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS category_translations (guild INTEGER, category INTEGER, source TEXT NOT NULL, english TEXT NOT NULL, PRIMARY KEY(guild,category))')
        self.install(bot)

    def category(self, guild, ident, original):
        if language() != 'en':
            return original
        row = self.db.execute('SELECT source,english FROM category_translations WHERE guild=? AND category=?', (guild, ident)).fetchone()
        return row['english'] if row and row['source'] == original else original + ' [original]'

    def install(self, bot):
        system = self
        group = app_commands.Group(name='traducciones', description='Administrar textos en inglés de la tienda y las misiones')
        COMMANDS['names']['traducciones'] = 'translations'
        descriptions = {
            'Administrar textos en inglés de la tienda y las misiones': 'Manage English shop and mission text',
            'Añadir nombre y descripción en inglés a un artículo': 'Add an English name and description to an item',
            'Añadir el nombre en inglés de una categoría': 'Add the English name of a category',
            'Añadir el nombre en inglés de una plantilla de misión': 'Add the English name of a mission template',
        }
        COMMANDS['descriptions'].update(descriptions)

        @group.command(name='articulo', description='Añadir nombre y descripción en inglés a un artículo')
        async def item(interaction: discord.Interaction, articulo: int):
            system.eco.admin(interaction)
            row = system.eco.store.item(interaction.guild_id, articulo)
            existing = row['data'].get('translations', {}).get('en', {})
            class Editor(system.ctx['SafeModal']):
                def __init__(self):
                    super().__init__(title=t('content.editor'))
                    self.name = discord.ui.TextInput(label=t('content.name'), max_length=80, default=existing.get('name', ''))
                    self.description = discord.ui.TextInput(label=t('content.description'), max_length=1800, style=discord.TextStyle.paragraph, required=False, default=existing.get('description', ''))
                    self.add_item(self.name)
                    self.add_item(self.description)

                async def on_submit(self, current):
                    system.eco.admin(current)
                    if current.user.id != interaction.user.id:
                        raise ValueError(t('Esta confirmación pertenece a otra persona.'))
                    fresh = system.eco.store.item(current.guild_id, articulo)
                    if fresh['version'] != row['version']:
                        raise ValueError(t('Otro administrador editó el artículo. Ábrelo otra vez.'))
                    data = copy.deepcopy(fresh['data'])
                    data.setdefault('translations', {})['en'] = {
                        'name': str(self.name).strip(), 'description': str(self.description).strip(),
                        'source': {key: data.get(key, '') for key in ('name', 'description')},
                    }
                    if not data['translations']['en']['name']:
                        raise ValueError(t('Nombre entre 1 y 80 caracteres.'))
                    system.eco.store.save_item(current.guild_id, current.user.id, fresh['village'], fresh['category'], data, fresh['stock'], articulo)
                    await current.response.send_message(t('content.saved_item'), ephemeral=True)
            await interaction.response.send_modal(Editor())

        @group.command(name='categoria', description='Añadir el nombre en inglés de una categoría')
        async def category(interaction: discord.Interaction, categoria: int, nombre: app_commands.Range[str, 1, 70]):
            system.eco.admin(interaction)
            row = system.db.execute('SELECT name FROM eco_categories WHERE guild=? AND id=?', (interaction.guild_id, categoria)).fetchone()
            if not row:
                raise ValueError(t('Categoría inexistente.'))
            if not nombre.strip():
                raise ValueError(t('Nombre de categoría de 1 a 70 caracteres.'))
            with system.db:
                system.db.execute('INSERT INTO category_translations VALUES(?,?,?,?) ON CONFLICT(guild,category) DO UPDATE SET source=excluded.source,english=excluded.english', (interaction.guild_id, categoria, row['name'], nombre.strip()))
                system.eco.store.audit(interaction.guild_id, interaction.user.id, 'category_translation', {'category': categoria, 'en': nombre.strip()})
            await interaction.response.send_message(t('content.saved'), ephemeral=True)

        @group.command(name='plantilla', description='Añadir el nombre en inglés de una plantilla de misión')
        async def template(interaction: discord.Interaction, plantilla: str, nombre: app_commands.Range[str, 1, 80]):
            system.eco.admin(interaction)
            mission_system = system.ctx['MISSIONS']
            if plantilla not in mission_system.config['types'] or not nombre.strip():
                raise ValueError(t('Usa una plantilla Open World válida.'))
            config = copy.deepcopy(mission_system.config)
            data = config['types'][plantilla]
            data.setdefault('translations', {})['en'] = {'label': nombre.strip(), 'source': {'label': data['label']}}
            path = system.ctx['BASE'] / 'misiones_config.json'
            temp = path.with_suffix('.i18n.tmp')
            temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(path)
            # Keep the shared object alive for previously installed callbacks.
            mission_system.config.clear()
            mission_system.config.update(config)
            with system.db:
                system.eco.store.audit(interaction.guild_id, interaction.user.id, 'mission_translation', {'key': plantilla, 'en': nombre.strip()})
            await interaction.response.send_message(t('content.saved'), ephemeral=True)

        bot.tree.add_command(group)


def mission_label(data):
    if data.get('translations'):
        return content_text(data, 'label')
    original = data.get('label', '')
    if language() == 'es' or original in CATALOGS['en'] or original in ('Genin','Chunin','Medical Corps','Police Corps','Jounin','Anbu','Jounin Sensei','Subkage','Sannin','Kage','Akatsuki'):
        return t(original)
    return original + ' [original]'
