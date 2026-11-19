"""Explicit UI translations. Never translate identifiers or user-provided text."""
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
import json
import string

ROOT = Path(__file__).resolve().parent / 'locales'
CATALOGS = {lang: json.loads((ROOT / (lang + '.json')).read_text(encoding='utf-8')) for lang in ('es', 'en')}
_language = ContextVar('worldx_language', default='es')
_preferences = None


def language():
    return _language.get()


@contextmanager
def using(lang):
    token = _language.set('en' if str(lang).startswith('en') else 'es')
    try:
        yield
    finally:
        _language.reset(token)


def t(key, **values):
    """Only explicitly marked developer strings pass through here."""
    text = CATALOGS[language()].get(key, CATALOGS['es'].get(key, key))
    return text.format_map(values) if values else text


def number(value):
    formatted = format(value, ',')
    if language() == 'es':
        formatted = formatted.translate(str.maketrans({',': '.', '.': ','}))
    return formatted


class Preferences:
    def __init__(self, db):
        self.db = db
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS language_preferences (guild INTEGER NOT NULL, user INTEGER NOT NULL, language TEXT NOT NULL CHECK(language IN (\'es\',\'en\',\'auto\')), PRIMARY KEY(guild,user))')

            db.execute('CREATE TABLE IF NOT EXISTS language_seen (guild INTEGER, user INTEGER, language TEXT NOT NULL, PRIMARY KEY(guild,user))')
            db.execute('CREATE TABLE IF NOT EXISTS language_channels (guild INTEGER, channel INTEGER, language TEXT NOT NULL, PRIMARY KEY(guild,channel))')

    def observe(self, guild, user, locale):
        if locale is None: return
        lang = 'en' if str(locale).startswith('en') else 'es'
        with self.db:
            self.db.execute('INSERT INTO language_seen VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET language=excluded.language', (guild or 0, user, lang))

    def get(self, guild, user):
        row = self.db.execute('SELECT language FROM language_preferences WHERE guild=? AND user=?', (guild or 0, user)).fetchone()
        return row[0] if row else 'auto'

    def set(self, guild, user, lang):
        if lang not in ('es', 'en', 'auto'):
            raise ValueError('Unsupported language')
        with self.db:
            self.db.execute('INSERT INTO language_preferences VALUES (?,?,?) ON CONFLICT(guild,user) DO UPDATE SET language=excluded.language', (guild or 0, user, lang))

    def resolve(self, guild, user, locale=None):
        selected = self.get(guild, user)
        if selected != 'auto': return selected
        if locale is not None: return 'en' if str(locale).startswith('en') else 'es'
        row = self.db.execute('SELECT language FROM language_seen WHERE guild=? AND user=?', (guild or 0, user)).fetchone()
        return row[0] if row else 'es'


def configure(db):
    global _preferences
    _preferences = Preferences(db)
    return _preferences


def bind(interaction):
    """Called within each independently scheduled Discord interaction task."""
    if _preferences:
        _preferences.observe(interaction.guild_id, interaction.user.id, getattr(interaction, 'locale', None))
    lang = _preferences.resolve(interaction.guild_id, interaction.user.id, getattr(interaction, 'locale', None)) if _preferences else 'es'
    _language.set(lang)
    return lang


def preference_scope(guild, user, locale=None):
    return using(_preferences.resolve(guild, user, locale) if _preferences else 'es')


def validate_catalogs():
    problems = []
    if CATALOGS['es'].keys() != CATALOGS['en'].keys():
        problems.append('Spanish and English keys differ')
    for key, source in CATALOGS['es'].items():
        target = CATALOGS['en'].get(key, '')
        fields = lambda text: sorted((name, spec, conversion) for _, name, spec, conversion in string.Formatter().parse(text) if name is not None)
        try:
            if fields(source) != fields(target):
                problems.append('Placeholder mismatch: ' + key)
        except ValueError:
            problems.append('Invalid format template: ' + key)
    return problems


def format_fragment(value, spec='', conversion=-1):
    """Preserve Python f-string formatting while supporting Python 3.11 syntax."""
    if conversion == 114:
        value = repr(value)
    elif conversion == 115:
        value = str(value)
    elif conversion == 97:
        value = ascii(value)
    rendered = format(value, spec)
    if language() == 'es' and isinstance(value, (int, float)) and ',' in spec:
        rendered = rendered.translate(str.maketrans({',': '.', '.': ','}))
    return rendered


def channel_scope(guild, channel):
    row = _preferences.db.execute('SELECT language FROM language_channels WHERE guild=? AND channel=?', (guild, channel)).fetchone() if _preferences else None
    return using(row[0] if row else 'es')

def bilingual(key):
    es = CATALOGS['es'].get(key, key)
    en = CATALOGS['en'].get(key, key)
    return es if es == en else es + ' / ' + en
