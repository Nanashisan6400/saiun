"""Unmodified IPAex fonts, private to the application and its WebKit context."""
import ctypes
from ctypes.util import find_library
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent.parent/'assets/fonts'
FONTS = {'ipaexm.ttf':'IPAexMincho', 'ipaexg.ttf':'IPAexGothic'}
_registered = False
_contexts = set()


def register_native_fonts():
    global _registered
    if _registered:return
    library = ctypes.CDLL(find_library('fontconfig') or 'libfontconfig.so.1')
    library.FcConfigGetCurrent.restype = ctypes.c_void_p
    library.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    library.FcConfigAppFontAddFile.restype = ctypes.c_int
    config = library.FcConfigGetCurrent()
    for filename in FONTS:
        if not library.FcConfigAppFontAddFile(config, bytes(FONT_DIR/filename)):
            raise RuntimeError('内蔵フォントを登録できません: '+filename)
    import gi
    gi.require_version('PangoCairo','1.0')
    from gi.repository import PangoCairo
    PangoCairo.FontMap.get_default().changed()
    _registered = True


def register_web_fonts(context):
    if context in _contexts:return
    import gi
    gi.require_version('WebKit','6.0')
    gi.require_version('Soup','3.0')
    from gi.repository import WebKit,Gio,GLib,Soup
    allowed={'reader-font://fonts/'+name:FONT_DIR/name for name in FONTS}
    def requested(request, unused):
        path=allowed.get(request.get_uri())
        if path is None:
            request.finish_error(GLib.Error.new_literal(Gio.io_error_quark(),'Unknown bundled font',Gio.IOErrorEnum.NOT_FOUND))
            return
        try:
            stream=Gio.File.new_for_path(str(path)).read(None)
            response=WebKit.URISchemeResponse.new(stream,path.stat().st_size)
            response.set_content_type('font/ttf')
            headers=Soup.MessageHeaders.new(Soup.MessageHeadersType.RESPONSE)
            headers.append('Access-Control-Allow-Origin','*')
            response.set_http_headers(headers)
            request.finish_with_response(response)
        except GLib.Error as error:
            request.finish_error(error)
    context.register_uri_scheme('reader-font',requested,None)
    security=context.get_security_manager()
    security.register_uri_scheme_as_cors_enabled('reader-font')
    security.register_uri_scheme_as_secure('reader-font')
    _contexts.add(context)


def font_css():
    return '\n'.join('@font-face{font-family:"'+family+'";src:url("reader-font://fonts/'+filename+'");font-style:normal;font-weight:400;font-display:block}' for filename,family in FONTS.items())
