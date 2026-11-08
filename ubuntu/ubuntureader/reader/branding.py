"""Application display name follows the preferred desktop language."""


def display_name(languages=None):
    if languages is None:
        from gi.repository import GLib
        languages = GLib.get_language_names()
    language = languages[0] if languages else 'C'
    return '彩雲' if language.lower().replace('-', '_').split('_', 1)[0].split('.', 1)[0] == 'ja' else 'Saiun'
