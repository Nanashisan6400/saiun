"""Bounded embedded assets, supplied to WebKit through data URIs."""
import base64
import math
import re
import struct
from xml.etree import ElementTree as ET

SVG = 'http://www.w3.org/2000/svg'
SVG_TAGS = {'svg','g','defs','title','desc','path','rect','circle','ellipse','line','polyline','polygon',
            'text','tspan','textPath','style','image','linearGradient','radialGradient','stop','clipPath','mask','use','symbol'}
SVG_ATTRIBUTES = set(('id class x y x1 y1 x2 y2 dx dy width height viewBox preserveAspectRatio '
                      'd points cx cy r rx ry fill fill-opacity fill-rule stroke stroke-width stroke-opacity '
                      'stroke-linecap stroke-linejoin stroke-miterlimit stroke-dasharray stroke-dashoffset '
                      'opacity transform gradientTransform gradientUnits spreadMethod offset stop-color stop-opacity '
                      'clip-path clip-rule clipPathUnits mask maskUnits maskContentUnits '
                      'font-family font-size font-weight font-style text-anchor dominant-baseline '
                      'alignment-baseline letter-spacing word-spacing writing-mode text-orientation '
                      'textLength lengthAdjust startOffset rotate href style').split())


def data_uri(raw, mime):
    return 'data:'+mime+';base64,'+base64.b64encode(raw).decode('ascii')


def svg_size(root):
    box = root.get('viewBox', '').replace(',', ' ').split()
    fallback = list(map(float, box)) if len(box) == 4 else None
    def dimension(name, index):
        value = root.get(name, '')
        if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?(?:px)?',value):
            return float(value.removesuffix('px'))
        if fallback:
            return fallback[index]
        raise ValueError('SVGには数値の幅・高さ、またはviewBoxが必要です。')
    width, height = dimension('width',2), dimension('height',3)
    if not all(math.isfinite(x) and 0 < x <= 20000 for x in (width,height)) or width*height > 32_000_000:
        raise ValueError('SVGの寸法が対応範囲を超えています。')
    if fallback and (not all(math.isfinite(x) for x in fallback) or min(fallback[2:]) <= 0):
        raise ValueError('SVGのviewBoxが不正です。')
    return width, height


def safe_svg(root, raster):
    """Keep static SVG only. Resolve raster images using the EPUB resource boundary."""
    if root.tag != '{'+SVG+'}svg' or len(ET.tostring(root)) > 4*1024*1024:
        raise ValueError('このSVG形式には対応していません。')
    svg_size(root)
    nodes = list(root.iter())
    if len(nodes) > 10000:
        raise ValueError('SVGの要素数が上限を超えています。')
    identifiers = {}
    references = []
    for element in nodes:
        tag = element.tag.removeprefix('{'+SVG+'}')
        if tag not in SVG_TAGS:
            raise ValueError('動的なSVGや未対応のSVG要素が含まれています。')
        if tag == 'style':
            css = re.sub(r'/\*.*?\*/','',element.text or '',flags=re.S)
            if re.search(r'[\\<>@]|//|(?:https?|file|data)\s*:|expression\s*\(',css,re.I):
                raise ValueError('SVGのスタイルに未対応の参照が含まれています。')
            end = 0
            for rule in re.finditer(r'([^{}]+)\{([^{}]*)\}',css):
                if css[end:rule.start()].strip():
                    raise ValueError('SVGの入れ子の書式には対応していません。')
                end = rule.end()
            if css[end:].strip():
                raise ValueError('SVGの書式が不正です。')
            for match in re.finditer(r'url\s*\((.*?)\)',css,re.I):
                target = match[1].strip(' "\'')
                if not target.startswith('#'):
                    raise ValueError('SVGの外部書式参照には対応していません。')
                references.append((element,target[1:]))
            element.text = css
        identifier = element.get('id')
        if identifier:
            if identifier in identifiers:
                raise ValueError('SVGの識別子が重複しています。')
            identifiers[identifier] = element
        for key, value in list(element.attrib.items()):
            local = key.removeprefix('{http://www.w3.org/1999/xlink}')
            if local not in SVG_ATTRIBUTES:
                del element.attrib[key]
                continue
            if local == 'href':
                if tag == 'image':
                    element.set(key,raster(value))
                elif value.startswith('#') and len(value) > 1:
                    references.append((element,value[1:]))
                else:
                    raise ValueError('SVGの外部参照には対応していません。')
            else:
                value = re.sub(r'/\*.*?\*/','',value,flags=re.S)
                element.set(key,value)
                if '//' in value:
                    raise ValueError('SVGの外部書式参照には対応していません。')
                if re.search(r'[\\<>@]|expression\s*\(|(?:https?|file|data)\s*:',value,re.I):
                    raise ValueError('SVGの書式に外部参照が含まれています。')
                for match in re.finditer(r'url\s*\((.*?)\)',value,re.I):
                    target = match[1].strip(' "\'')
                    if not target.startswith('#'):
                        raise ValueError('SVGの外部書式参照には対応していません。')
                    references.append((element,target[1:]))
    edges = {}
    for element,target in references:
        if target not in identifiers:
            raise ValueError('SVGの参照先が見つかりません。')
        edges.setdefault(element,[]).append(identifiers[target])
    # Includes child edges so a use pointing at one of its ancestors is a cycle.
    completed = {}
    active = set()
    def expansion(element,depth=0):
        if element in active or depth > 100:
            raise ValueError('SVGの循環参照または参照の深さが上限を超えています。')
        if element in completed:
            return completed[element]
        active.add(element)
        count = 1 + sum(expansion(child,depth+1) for child in list(element)+edges.get(element,[]))
        active.remove(element)
        if count > 20000:
            raise ValueError('SVGの参照展開が上限を超えています。')
        completed[element] = count
        return count
    expansion(root)
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def font_uri(raw):
    if len(raw) < 12 or len(raw) > 8*1024*1024:
        raise ValueError('埋め込みフォントのサイズが対応範囲を超えています。')
    signature = raw[:4]
    mime = {b'OTTO':'font/otf',b'\x00\x01\x00\x00':'font/ttf',b'true':'font/ttf',b'wOFF':'font/woff',b'wOF2':'font/woff2'}.get(signature)
    if not mime:
        raise ValueError('この埋め込みフォント形式には対応していません。')
    if signature in (b'wOFF',b'wOF2'):
        if len(raw) < 48 or struct.unpack_from('>I',raw,16)[0] > 32*1024*1024:
            raise ValueError('埋め込みフォントの展開サイズが上限を超えています。')
    else:
        tables = struct.unpack_from('>H',raw,4)[0]
        if not 0 < tables <= 512 or len(raw) < 12+16*tables:
            raise ValueError('埋め込みフォントのテーブルが不正です。')
        for index in range(tables):
            offset,length = struct.unpack_from('>II',raw,12+16*index+8)
            if offset+length > len(raw):
                raise ValueError('埋め込みフォントが途中で切れています。')
    return data_uri(raw,mime)
