"""Chapter candidates and bounded runs of source documents."""
import re
import hashlib

MAX_FLOW_TEXT = 50000
MAX_FLOW_ELEMENTS = 8000
MAX_FLOW_DOCUMENTS = 8


class FlowLimitError(ValueError):
    pass


EPUB_TYPE = '{http://www.idpf.org/2007/ops}type'
EXCLUDED = {'footnote','endnote','endnotes','rearnotes','toc','landmarks','page-list'}


def local_tag(element):
    return element.tag.rsplit('}',1)[-1].lower()


def body_entries(root):
    body = next((element for element in root.iter() if local_tag(element) == 'body'),None)
    if body is None:
        return None,[]
    entries = []
    stack = [(body,(),False)]
    while stack:
        element,path,excluded = stack.pop()
        types = set(element.get(EPUB_TYPE,'').split())
        roles = set(element.get('role','').split())
        excluded = excluded or bool(types & EXCLUDED) or bool(roles & {'doc-footnote','doc-endnote','doc-endnotes','doc-toc'}) or local_tag(element) in {'nav','aside','script','style'}
        entries.append((element,path,excluded))
        for index,child in reversed(list(enumerate(element))):
            stack.append((child,path+(index,),excluded))
    return body,entries


def native_direction(css, classes):
    candidates = []
    for order,(selector,declarations) in enumerate(re.findall(r'([^{}]+)\{([^{}]*)\}',css)):
        values = re.findall(r'(?:^|;)\s*(?:-epub-)?writing-mode\s*:\s*([^;]+)',declarations,re.I)
        if not values:
            continue
        for part in selector.split(','):
            part = part.strip()
            if not part.startswith('#source') or part.startswith('#source '):
                continue
            if not re.fullmatch(r'#source(?:\.[\w-]+)*',part):
                return None
            required = re.findall(r'\.([\w-]+)',part)
            if set(required) <= classes:
                for declaration_order,raw_value in enumerate(values):
                    value = raw_value.lower()
                    priority = ('!important' in re.sub(r'\s+','',value),len(required),order,declaration_order)
                    candidates.append((priority,'vertical' if 'vertical' in value or 'tb-rl' in value else 'horizontal'))
    return max(candidates)[1] if candidates else 'horizontal'


def inspect_book(book, indices=None):
    documents = [None]*len(book.spine)
    candidates = []
    for index in (range(len(book.spine)) if indices is None else indices):
        path = book.spine[index]
        root = book.xml(path)
        body,entries = body_entries(root)
        info = dict(paths=[],start=False,text=0,elements=0,direction='horizontal')
        documents[index] = info
        if book.layouts[index] == 'fixed' or body is None:
            continue
        info['text'] = sum(len(text) for text in body.itertext())
        info['elements'] = len(entries)
        article,css = book.document(index)
        info.update(image_bytes=book.image_bytes,image_pixels=book.image_pixels,fonts=set(book.font_files),faces=len(book.font_rules),asset_size=len(article.encode('utf-8'))+len(css.encode('utf-8')))
        info['direction'] = native_direction(css,set((root.get('class','')+' '+body.get('class','')).split())) or 'isolated-'+str(index)
        identifiers = {}
        for element,element_path,excluded in entries:
            if element.get('id'):
                identifiers.setdefault(element.get('id'),[]).append((element_path,excluded))
            types = set(element.get(EPUB_TYPE,'').split())
            if not excluded and ('chapter' in types or 'doc-chapter' in element.get('role','').split()):
                info['paths'].append(element_path)
        excluded_paths = {path:excluded for _,path,excluded in entries}
        main_content = any(
            (not excluded and ((element.text or '').strip() or local_tag(element) in {'img','svg'})) or
            (path and not excluded_paths.get(path[:-1],False) and (element.tail or '').strip())
            for element,path,excluded in entries)

        for chapter in book.chapters:
            if not main_content:
                continue
            if chapter['spine'] != index or chapter.get('source') == 'fallback':
                continue
            fragment = chapter['fragment']
            matches = identifiers.get(fragment,[]) if fragment else [((),False)]
            if len(matches) == 1 and not matches[0][1]:
                candidates.append((index,matches[0][0],chapter.get('depth',1)))
        info['entries'] = entries
    major_depth = min((chapter.get('depth',1) for chapter in book.chapters if chapter.get('source') != 'fallback'),default=1)
    for index,info in enumerate(documents):
        if info is None:
            continue
        entries = info.pop('entries',[])
        if not entries:
            continue
        if not info['paths']:
            info['paths'] = [path for spine,path,depth in candidates if spine == index and depth == major_depth]
        if not info['paths']:
            info['paths'] = [path for element,path,excluded in entries if not excluded and local_tag(element) == 'h1']
        info['paths'] = sorted(set(info['paths']))
        if info['paths']:
            first = info['paths'][0]
            meaningful = False
            excluded_paths = {path:excluded for _,path,excluded in entries}
            for element,path,excluded in entries:
                if path == first:
                    info['start'] = not meaningful
                    break
                if not excluded:
                    meaningful = meaningful or bool((element.text or '').strip()) or local_tag(element) in {'img','svg'}
                ancestor = first[:len(path)] == path
                if path and not ancestor and not excluded_paths.get(path[:-1],False) and (element.tail or '').strip():
                    meaningful = True
    return documents


def within_budget(book, group):
    infos = [book.flow_info[i] for i in group]
    fonts = set().union(*(info.get('fonts',set()) for info in infos))
    return (
        len(group) <= MAX_FLOW_DOCUMENTS and
        sum(info['text'] for info in infos) <= MAX_FLOW_TEXT and
        sum(info['elements'] for info in infos) <= MAX_FLOW_ELEMENTS and
        sum(info.get('image_bytes',0) for info in infos) <= 32*1024*1024 and
        sum(info.get('image_pixels',0) for info in infos) <= 64_000_000 and
        len(fonts) <= 8 and sum(book.z.getinfo(path).file_size for path in fonts) <= 16*1024*1024 and
        sum(info.get('faces',0) for info in infos) <= 32 and
        sum(info.get('asset_size',0) for info in infos) <= 64*1024*1024)


def prepare_info(book, indices):
    if not hasattr(book,'flow_info'):
        book.flow_info = [None]*len(book.spine)
    missing = [index for index in indices if book.flow_info[index] is None]
    if missing:
        inspected = inspect_book(book,missing)
        for index in missing:
            book.flow_info[index] = inspected[index]


def flow_plan(book, mode='chapter', direction='original', combine=True, around=None):
    if not combine:
        return [[index] for index in range(len(book.spine))]
    if around is None:
        indices = list(range(len(book.spine)))
    else:
        start = around//MAX_FLOW_DOCUMENTS*MAX_FLOW_DOCUMENTS
        indices = list(range(start,min(start+MAX_FLOW_DOCUMENTS,len(book.spine))))
    prepare_info(book,indices)
    groups = []
    for index in indices:
        info = book.flow_info[index]
        previous = groups[-1] if groups else None
        merge = (previous is not None and index % MAX_FLOW_DOCUMENTS != 0 and
                 book.layouts[index] != 'fixed' and book.layouts[previous[-1]] != 'fixed')
        if merge and direction == 'original':
            merge = info['direction'] == book.flow_info[previous[-1]]['direction']
        if merge and within_budget(book,previous+[index]):
            previous.append(index)
        else:
            groups.append([index])
    return groups


def scoped_fonts(css, index):
    names = re.findall(r'@font-face\s*\{[^{}]*?font-family\s*:\s*([^;{}]+)',css,re.I)
    aliases = {}
    for raw_name in names:
        name = raw_name.strip().strip('"\'')
        digest = hashlib.sha256(name.casefold().encode('utf-8')).hexdigest()[:12]
        aliases[name] = 'ReaderPart'+str(index)+'_'+re.sub(r'\W','_',name)+'_'+digest
    def declaration(match):
        value = match[2]
        for name,alias in aliases.items():
            pattern = r'(?<![\w-])(?:"'+re.escape(name)+r'"|\''+re.escape(name)+r'\'|'+re.escape(name)+r')(?![\w-])'
            value = re.sub(pattern,'"'+alias+'"',value,flags=re.I)
        return match[1]+value
    return re.sub(r'((?:font-family|font)\s*:\s*)([^;{}]+)',declaration,css,flags=re.I)


def scoped_styles(css, index):
    """Constrain the selected element as well as the rule's source root."""
    scope = '[data-reader-part="'+str(index)+'"]'
    containment = ':is('+scope+','+scope+' *)'
    result = []
    for match in re.finditer(r'([^{}]+)\{([^{}]*)\}',scoped_fonts(css,index)):
        selectors,body = match.groups()
        if selectors.strip().lower() == '@font-face':
            result.append('@font-face{'+body+'}')
            continue
        scoped = []
        for selector in selectors.split(','):
            selector = re.sub(r'#source\b',lambda _:scope,selector.strip())
            pseudo = re.search(r'(::?(?:before|after|first-letter|first-line|marker|selection))$',selector,re.I)
            if pseudo:
                selector = selector[:pseudo.start()]+containment+pseudo[0]
            else:
                selector += containment
            scoped.append(selector)
        result.append(','.join(scoped)+'{'+body+'}')
    return '\n'.join(result)
