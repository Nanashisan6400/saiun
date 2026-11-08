"""First local-reader adapter, sharing the validated stage0 text renderer."""
from pathlib import Path
import posixpath
import copy
import math
import re
import struct
import uuid
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree as ET

from ..stage0.sample_epub import SampleBook, NS
from .sections import make_plan, select_body
from .assets import SVG, data_uri, safe_svg, svg_size, font_uri
from .chapters import flow_plan, scoped_styles, FlowLimitError, prepare_info

DC = {'dc': 'http://purl.org/dc/elements/1.1/'}
XHTML = {'x': 'http://www.w3.org/1999/xhtml'}


def raster_size(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) >= 24:
        return struct.unpack('>II', raw[16:24])
    if raw.startswith((b'GIF87a', b'GIF89a')) and len(raw) >= 10:
        return struct.unpack('<HH', raw[6:10])
    if raw.startswith(b'\xff\xd8'):
        offset = 2
        while offset + 4 <= len(raw):
            if raw[offset] != 255:
                return None
            while offset < len(raw) and raw[offset] == 255:
                offset += 1
            if offset >= len(raw):
                return None
            marker = raw[offset]
            offset += 1
            if marker in (0xd9, 0xda):
                return None
            if marker in range(0xd0, 0xd8) or marker == 1:
                continue
            if offset + 2 > len(raw):
                return None
            length = int.from_bytes(raw[offset:offset+2], 'big')
            if length < 2 or offset + length > len(raw):
                return None
            if marker in (0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf) and length >= 7:
                height, width = struct.unpack('>HH', raw[offset+3:offset+7])
                return width, height
            offset += length
    return None


def bounded_raster(raw):
    size = raster_size(raw)
    return size is not None and min(size) > 0 and size[0]*size[1] <= 32_000_000


class CSSCompatibilityError(ValueError):
    pass


class EpubBook(SampleBook):
    def read(self, path):
        item = self.z.getinfo(path)
        limit = 16*1024*1024
        if item.file_size > limit or item.flag_bits & 1:
            raise ValueError('Entry limit or encrypted ZIP')
        with self.z.open(item) as stream:
            raw = stream.read(limit+1)
        if len(raw) > limit:
            raise ValueError('Entry limit')
        return raw

    def xml(self, path):
        raw = self.read(path)
        # XML markup has interleaved NUL bytes in UTF-16/32 encodings.
        if b'<!ENTITY' in raw.replace(b'\x00', b'').upper():
            raise ValueError('Entity declarations forbidden')
        root = ET.fromstring(raw)
        for element in root.iter():
            for key in list(element.attrib):
                if key in {'data-spine', 'data-href', 'data-chapter-start'} or key.startswith(('data-stage0-', 'data-reader-')):
                    del element.attrib[key]
        return root

    def __init__(self, path):
        try:
            super().__init__(path)
            package = self.xml(self.opf_path)
            metadata = package.find('o:metadata', NS)
            self.title = self._text(metadata.find('dc:title', DC)) or 'タイトルなし'
            self.authors = [self._text(element) for element in metadata.findall('dc:creator', DC)]
            self.language = self._text(metadata.find('dc:language', DC)) or None
            self.progression = package.find('o:spine', NS).get('page-progression-direction')
            self.hrefs = [quote(posixpath.relpath(href, posixpath.dirname(self.opf_path) or '.'), safe='/') for href in self.spine]
            self.chapters = self._chapters(package)
            self.layouts = self._layouts(package)
            self.links = {}
            self._link_spines = {}
            for index, href in enumerate(self.spine):
                self._link_spines.setdefault(href, index)
            self._link_xhtml = None
            self.section_plans = {}
            self.active_part = 0
            self.standard_spines = set()
            self.standard_reasons = {}
            self.standard_mode = False
            self.include_styles = True
        except Exception:
            if hasattr(self, 'z'):
                self.z.close()
            raise

    @staticmethod
    def _text(element):
        return ''.join(element.itertext()).strip()[:2048] if element is not None else ''

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.z.close()

    def _chapters(self, package):
        chapters = []
        navigation = next((item for item in self.items.values() if 'nav' in item.get('properties', '').split()), None)
        if navigation:
            path = self.resolve(self.opf_path, navigation['href'])
            root = self.xml(path)
            toc = next((element for element in root.iter() if element.tag.endswith('}nav') and
                        'toc' in element.get('{http://www.idpf.org/2007/ops}type', '').split()), None)
            if toc is not None:
                pending = [(toc,0)]
                while pending:
                    element,depth = pending.pop()
                    if element.tag.endswith('}ol'):
                        depth += 1
                    if element.tag.endswith('}a'):
                        self._chapter(chapters,path,element.get('href',''),self._text(element),max(1,depth))
                    pending.extend((child,depth) for child in reversed(list(element)))
        else:
            spine = package.find('o:spine', NS)
            ncx = self.items.get(spine.get('toc'))
            if ncx:
                path = self.resolve(self.opf_path, ncx['href'])
                pending = [(self.xml(path),0)]
                while pending:
                    point,depth = pending.pop()
                    if point.tag.rsplit('}',1)[-1] == 'navPoint':
                        depth += 1
                        source = next((child.get('src') for child in point if child.tag.endswith('}content')),None)
                        label = next((self._text(child) for child in point if child.tag.endswith('}navLabel')),'')
                        if source:
                            self._chapter(chapters,path,source,label,depth)
                    pending.extend((child,depth) for child in reversed(list(point)))
        if not chapters:
            chapters = [dict(spine=index, fragment=None, label=f'区間 {index + 1}',depth=1,source='fallback') for index in range(len(self.spine))]
        return chapters

    def _chapter(self, chapters, base, target, label, depth=1):
        try:
            path = self.resolve(base, target)
        except ValueError:
            return
        if path in self.spine:
            chapters.append(dict(spine=self.spine.index(path), fragment=unquote(urlsplit(target).fragment) or None,
                                 label=label or f'区間 {self.spine.index(path) + 1}',depth=depth,source='toc'))

    def _layouts(self, package):
        default = next((self._text(meta) for meta in package.findall('o:metadata/o:meta', NS)
                        if meta.get('property') == 'rendition:layout' and not meta.get('refines')), 'reflowable')
        layouts = []
        for reference in package.findall('o:spine/o:itemref', NS):
            properties = reference.get('properties', '').split()
            layout = default
            if 'rendition:layout-pre-paginated' in properties:
                layout = 'pre-paginated'
            elif 'rendition:layout-reflowable' in properties:
                layout = 'reflowable'
            if layout not in ('pre-paginated', 'reflowable'):
                raise ValueError('EPUBのレイアウト指定に対応していません。')
            layouts.append('fixed' if layout == 'pre-paginated' else 'reflowable')
        return layouts

    def fixed_size(self, index):
        root = self.xml(self.spine[index])
        if root.tag == '{'+SVG+'}svg':
            return svg_size(root)
        viewport = next((element.get('content', '') for element in root.iter()
                         if element.tag.rsplit('}', 1)[-1] == 'meta' and element.get('name', '').lower() == 'viewport'), '')
        values = {key.lower(): value for key, value in re.findall(r'(?:^|[,;\s])(width|height)\s*=\s*([0-9]+(?:\.[0-9]+)?)(?=$|[,;\s])', viewport, re.I)}
        try:
            width, height = float(values['width']), float(values['height'])
        except (KeyError, ValueError):
            raise ValueError('固定ページには数値のviewport幅・高さが必要です。') from None
        if not all(math.isfinite(value) and 0 < value <= 20000 for value in (width, height)) or width*height > 64_000_000:
            raise ValueError('固定ページの寸法が対応範囲を超えています。')
        return width, height

    def css(self, path, stack=()):
        if self.standard_mode:
            return ''
        self.css_count += 1
        if self.css_count > 64:
            raise CSSCompatibilityError('CSSの読み込み数が上限を超えています。')
        try:
            return super().css(path, stack)
        except (UnicodeError, KeyError) as error:
            raise CSSCompatibilityError('CSSを読み込めません。') from error

    def css_text(self, text, path, stack=()):
        if self.standard_mode:
            return ''
        # Normalize quoted imports into the existing bounded local-import path.
        text = re.sub(r'@import\s+([\"\'])([^\"\']+)\1\s*;',
                      lambda m: '@import url("'+m[2]+'");', text, flags=re.I)
        # XHTML default namespace is already represented by our sanitized HTML.
        text = re.sub(r'@namespace\s+(?:url\(\s*)?([\"\'])http://www\.w3\.org/1999/xhtml\1\s*\)?\s*;', '', text, flags=re.I)
        # Page margins belong to the reader viewport, not the EPUB page rule.
        text = re.sub(r'@page\s*\{\s*(?:margin(?:-(?:top|bottom|left|right))?\s*:[^{};]+;?\s*)*\}', '', text, flags=re.I)
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
        def face(match):
            declarations = {}
            for declaration in match[1].split(';'):
                if not declaration.strip():
                    continue
                key, separator, value = declaration.partition(':')
                key = key.strip().lower()
                if not separator or key not in {'font-family','src','font-style','font-weight','font-stretch','font-display','unicode-range'}:
                    raise CSSCompatibilityError('埋め込みフォントの指定に対応していません。')
                declarations[key] = value.strip()
            family = declarations.get('font-family', '')
            if not re.fullmatch(r'[\w \"\'-]+',family) or re.search(r'[\\<>@{}]',match[1]):
                raise CSSCompatibilityError('埋め込みフォントの書体名または書式が不正です。')
            source = re.fullmatch(r'url\(\s*([\"\']?)([^)\"\']+)\1\s*\)(?:\s+format\(\s*[\"\'](?:opentype|truetype|woff2?)[\"\']\s*\))?', declarations.get('src',''),re.I)
            if not source:
                raise CSSCompatibilityError('埋め込みフォントはEPUB内の単一ファイル参照に対応しています。')
            target = self.resolve(path,source[2].strip())
            if target not in self.font_files:
                raw = self.read(target)
                if len(self.font_files) >= 8 or self.font_bytes+len(raw) > 16*1024*1024:
                    raise ValueError('本文内の埋め込みフォント量が上限を超えています。')
                self.font_files[target] = font_uri(raw)
                self.font_bytes += len(raw)
            declarations['src'] = 'url("'+self.font_files[target]+'")'
            # Validate non-resource declarations using the same bounded flat CSS parser.
            other = ';'.join(key+':'+value for key,value in declarations.items() if key != 'src')
            super(EpubBook,self).css_text('body{'+other+'}',path)
            self.font_rules.append('@font-face{'+other+';src:'+declarations['src']+'}')
            return ''
        text = re.sub(r'@font-face\s*\{([^{}]*)\}',face,text,flags=re.I)
        try:
            return super().css_text(text,path,stack)
        except ValueError as error:
            if str(error).startswith(('Unsupported CSS', 'Unsupported nested CSS')):
                raise CSSCompatibilityError(str(error)) from error
            raise

    def raster_data(self, path):
        raw = self.read(path)
        if not bounded_raster(raw):
            raise ValueError('画像形式または画像寸法が対応範囲を超えています。')
        width,height = raster_size(raw)
        self.image_bytes += len(raw)
        self.image_pixels += width*height
        if self.image_bytes > 32*1024*1024 or self.image_pixels > 64_000_000:
            raise ValueError('本文内の画像量が上限を超えています。')
        mime = 'image/png' if raw.startswith(b'\x89PNG') else 'image/gif' if raw.startswith(b'GIF') else 'image/jpeg'
        return data_uri(raw,mime)

    def svg_data(self, root, path):
        width,height = svg_size(root)
        self.image_pixels += width*height
        if self.image_pixels > 64_000_000:
            raise ValueError('本文内のSVG画像量が上限を超えています。')
        raw = safe_svg(copy.deepcopy(root),lambda href:self.raster_data(self.resolve(path,href)))
        self.image_bytes += len(raw)
        if self.image_bytes > 32*1024*1024:
            raise ValueError('本文内のSVG画像量が上限を超えています。')
        return data_uri(raw,'image/svg+xml')

    def image_data(self, path):
        if path.lower().endswith('.svg'):
            return self.svg_data(self.xml(path),path)
        return self.raster_data(path)

    def svg_image(self, element, path):
        width,height = svg_size(element)
        attributes = {key:element.get(key) for key in ('id','class') if element.get(key)}
        attributes.update(src=self.svg_data(element,path),width=str(width),height=str(height),alt='SVG図版')
        return ET.Element('img',attributes)

    def section_plan(self, index):
        if index not in self.section_plans:
            root = self.xml(self.spine[index])
            if self.layouts[index] == 'fixed':
                self.section_plans[index] = None
            else:
                if sum(1 for _ in root.iter()) > 100_000:
                    raise ValueError('本文全体の構造が解析上限を超えています。')
                self.section_plans[index] = make_plan(root, {c['fragment'] for c in self.chapters if c['spine'] == index and c['fragment']})
        return self.section_plans[index]

    def part_for(self, index, locator=None):
        plan = self.section_plan(index)
        return plan.choose(locator) if plan else 0

    def part_count(self, index):
        plan = self.section_plan(index)
        return len(plan.ranges) if plan else 1

    def prepare_body(self, root, body, index):
        plan = self.section_plan(index)
        return select_body(root, body, plan, self.active_part) if plan and len(plan.ranges) > 1 else lambda: None

    def document(self, index, provenance=False, chapter_paths=(), part=0):
        self.active_part = part
        self.standard_mode = index in self.standard_spines
        try:
            return self._document(index, provenance, chapter_paths)
        except CSSCompatibilityError as error:
            if self.layouts[index] == 'fixed' or self.standard_mode:
                raise
            self.standard_spines.add(index)
            self.standard_reasons[index] = str(error)
            self.standard_mode = True
            return self._document(index, provenance, chapter_paths)

    def _document(self, index, provenance=False, chapter_paths=()):
        self.css_count = 0
        self.links = {}
        self.font_rules, self.font_files, self.font_bytes = [], {}, 0
        self.image_bytes = self.image_pixels = 0
        fixed = self.layouts[index] == 'fixed'
        root = self.xml(self.spine[index])
        if root.tag == '{'+SVG+'}svg':
            if not fixed:
                raise ValueError('SVG本文は固定レイアウト指定で取り込んでください。')
            source = self.svg_data(root,self.spine[index])
            article = '<article id="source"><div class="reader-fixed-page"><img width="100%" height="100%" src="'+source+'" alt="SVG固定ページ"></div></article>'
            css = ''
        else:
            article, css = super().document(index, provenance=provenance and not fixed, preserve_styles=fixed, chapter_paths=chapter_paths, preserve_breaks=True)
        if fixed:
            css = re.sub(r'#source\b', '#source .reader-fixed-page', css)
        if self.standard_mode:
            css = super().css_text(Path(__file__).with_name('default.css').read_text(), self.spine[index])
        plan = self.section_plan(index)
        if plan and len(plan.ranges) > 1:
            begin, end, image_offset = plan.bounds(self.active_part)
            article = article.replace('<article ', f'<article data-reader-start="{begin}" data-reader-end="{end}" data-reader-image-offset="{image_offset}" ', 1)
        return article, '\n'.join(self.font_rules)+ '\n'+css

    def navigation_href(self, base, href, element):
        if len(href) > 8192 or any(ord(c) < 32 for c in href):
            return None
        try:
            url = urlsplit(href)
            if url.scheme in ('http', 'https') or not url.scheme and url.netloc:
                if not url.hostname:
                    return None
                target = dict(kind='external', url=href if url.scheme else 'https:'+href)
            else:
                if url.scheme or url.netloc:
                    return None
                path = self.resolve(base, href) if url.path else base
                fragment = unquote(url.fragment) or None
                if path in self._link_spines:
                    target = dict(kind='internal', spine=self._link_spines[path], fragment=fragment)
                else:
                    if self._link_xhtml is None:
                        self._link_xhtml = set()
                        for item in self.items.values():
                            if item.get('media-type') != 'application/xhtml+xml':
                                continue
                            try:
                                self._link_xhtml.add(self.resolve(self.opf_path, item['href']))
                            except ValueError:
                                continue
                    if path not in self._link_xhtml:
                        return None
                    target = dict(kind='preview', path=path, fragment=fragment)
            target['noteref'] = 'noteref' in element.get('{http://www.idpf.org/2007/ops}type', '').split() or element.get('role') == 'doc-noteref'
            uri = 'reader-link://publication/'+uuid.uuid4().hex
            self.links[uri] = target
            return uri
        except (ValueError, KeyError):
            return None

    def link_target(self, reference):
        """Validate the destination on activation, without scanning it on turns."""
        if reference['kind'] == 'external':
            return reference
        path = self.spine[reference['spine']] if reference['kind'] == 'internal' else reference['path']
        root = self.xml(path)
        if sum(1 for _ in root.iter()) > 100000:
            raise ValueError('リンク先の本文が対応範囲を超えています。')
        fragment = reference['fragment']
        if fragment:
            targets = [node for node in root.iter() if node.get('id') == fragment or
                       node.tag.rsplit('}', 1)[-1].lower() == 'a' and node.get('name') == fragment]
            if len(targets) != 1:
                raise ValueError('リンク先の場所が見つからないか、識別子が重複しています。')
            target = targets[0]
        else:
            target = next((node for node in root.iter() if node.tag.rsplit('}', 1)[-1] == 'body'), root)
        parents = {child: parent for parent in root.iter() for child in parent}
        note = reference.get('noteref')
        ancestor = target
        while ancestor is not None:
            if ancestor.tag.rsplit('}', 1)[-1].lower() in {'head','script','style','iframe','object','embed','form','template','noscript','rp'}:
                raise ValueError('このリンク先は本文に表示できません。')
            semantics = ancestor.get('{http://www.idpf.org/2007/ops}type', '').split()
            if any(value in semantics for value in ('footnote', 'endnote')) or ancestor.get('role') in ('doc-footnote', 'doc-endnote'):
                note = True
                target = ancestor
                break
            ancestor = parents.get(ancestor)
        if reference['kind'] == 'internal' and not note:
            return reference
        if note and target.tag.rsplit('}', 1)[-1].lower() == 'a' and not ''.join(target.itertext()).strip():
            parent = parents.get(target)
            if parent is not None:
                if parent.tag.rsplit('}', 1)[-1].lower() in ('p', 'li'):
                    target = parent
                elif target.tail and target.tail.strip():
                    text_target = ET.Element('p')
                    text_target.text = target.tail
                    target = text_target
                else:
                    following = list(parent)[list(parent).index(target)+1:]
                    target = next((node for node in following if ''.join(node.itertext()).strip()), target)
        blocked = {'script','style','iframe','object','embed','form','rp'}
        blocks = {'p','div','aside','section','li','dt','dd','blockquote','h1','h2','h3','h4','h5','h6','br'}
        chunks, stack, length = [], [('node', target)], 0
        while stack and length < 12000:
            kind, value = stack.pop()
            if kind == 'text':
                chunks.append(value or '')
                length += len(value or '')
                continue
            tag = value.tag.rsplit('}', 1)[-1].lower()
            if tag in blocked:
                continue
            if tag in blocks:
                chunks.append('\n')
            if tag == 'rt':
                chunks.append('（')
                stack.append(('text', '）'))
            if value.text:
                chunks.append(value.text)
                length += len(value.text)
            for child in reversed(list(value)):
                stack.append(('text', child.tail))
                stack.append(('node', child))
        text = '\n'.join(line.strip() for line in ''.join(chunks).splitlines() if line.strip())
        if not text:
            raise ValueError('リンク先に表示できる文章がありません。')
        return dict(reference, kind='preview', text=text[:12000], truncated=bool(stack) or len(text) > 12000)

    def flow_plan(self, mode='chapter', direction='original', combine=True, around=None):
        return flow_plan(self,mode,direction,combine,around)

    def sequence_document(self, index, mode='chapter', direction='original', combine=True):
        if not combine:
            prepare_info(self,[index])
            paths = self.flow_info[index]['paths'] if mode == 'chapter' else ()
            article,css = self.document(index,provenance=True,chapter_paths=paths)
            return article,css,[index]
        plan = self.flow_plan(mode,direction,around=index)
        group = next(group for group in plan if index in group)
        if self.layouts[index] == 'fixed':
            article,css = self.document(index,provenance=True)
            return article,css,group
        sections, styles = [],[]
        image_bytes = image_pixels = total_size = faces = 0
        fonts = set()
        for part in group:
            article,css = self.document(part,provenance=True,chapter_paths=self.flow_info[part]['paths'])
            image_bytes += self.image_bytes
            image_pixels += self.image_pixels
            fonts.update(self.font_files)
            faces += len(self.font_rules)
            total_size += len(article.encode('utf-8'))+len(css.encode('utf-8'))
            if image_bytes > 32*1024*1024 or image_pixels > 64_000_000 or len(fonts) > 8 or faces > 32 or total_size > 64*1024*1024:
                raise FlowLimitError('連続表示する画像・フォント量が上限を超えています。')
            if len(group) > 1:
                article = article.replace('<article id="source"','<section data-reader-part="'+str(part)+'" style="display:contents!important"',1)
                article = article.removesuffix('</article>')+'</section>'
                css = scoped_styles(css,part)
            sections.append(article)
            styles.append(css)
        if len(group) == 1:
            return sections[0],styles[0],group
        return '<article id="source">'+''.join(sections)+'</article>', '\n'.join(styles),group

    def validate(self):
        if not self.spine:
            raise ValueError('本文が見つかりません。')
        for index, path in enumerate(self.spine):
            root = self.xml(path)
            fixed = self.layouts[index] == 'fixed'
            if fixed:
                self.fixed_size(index)
            if fixed and sum(len(text) for text in root.itertext()) > 250000:
                raise ValueError('長い本文区間を持つEPUBにはまだ対応していません。対応形式を拡張中です。')
            if fixed and sum(1 for element in root.iter()) > 20000:
                raise ValueError('複雑な本文構造を持つEPUBにはまだ対応していません。対応形式を拡張中です。')
            if any(element.tag.rsplit('}', 1)[-1].lower() in {'plaintext', 'xmp', 'frameset', 'frame', 'listing'} for element in root.iter()):
                raise ValueError('この本文の書式にはまだ対応していません。')
            if any(element.tag.rsplit('}', 1)[-1].lower() in {'math', 'audio', 'video'} for element in root.iter()):
                raise ValueError('数式・音声・動画を含む本文は後続の実装で対応します。')
            for element in root.iter():
                if element.get('id') in {'source', 'viewport'}:
                    raise ValueError('本文の識別子がアプリの識別子と重なっています。後続の取り込み拡張で対応します。')
            try:
                for part in range(self.part_count(index)):
                    self.document(index, provenance=True, part=part)
            except (ValueError, KeyError) as error:
                if isinstance(error, ValueError) and not str(error).isascii():
                    raise
                raise ValueError('本文・CSS・画像の形式に未対応の指定があります。対応範囲はREADMEを確認してください。') from error


    def cover(self):
        item = next((item for item in self.items.values() if 'cover-image' in item.get('properties', '').split()), None)
        if item is None:
            package = self.xml(self.opf_path)
            reference = next((element.get('content') for element in package.findall('o:metadata/o:meta', NS)
                              if element.get('name') == 'cover'), None)
            item = self.items.get(reference)
        if item and item.get('media-type') == 'image/svg+xml':
            path = self.resolve(self.opf_path,item['href'])
            root = self.xml(path)
            svg_size(root)
            self.image_bytes = self.image_pixels = 0
            raw = safe_svg(root,lambda href:self.raster_data(self.resolve(path,href)))
            if len(raw) <= 4*1024*1024:
                return raw,'.svg'
        if item and item.get('media-type') in {'image/png', 'image/jpeg'}:
            raw = self.read(self.resolve(self.opf_path, item['href']))
            # Covers are decoded at a bounded display size by GTK.
            if len(raw) <= 4 * 1024 * 1024 and bounded_raster(raw):
                return raw, '.png' if item['media-type'] == 'image/png' else '.jpg'
        return None
