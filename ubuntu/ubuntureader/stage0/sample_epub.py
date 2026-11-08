"""Bounded, read-only preprocessing for the supplied EPUB in the stage0 harness.

CSS support is intentionally limited to flat rules and local @import; unknown
nested rules are rejected. This is a diagnostic adapter, not a product importer.
"""
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from urllib.parse import urlsplit, unquote
import base64
import html
import posixpath
import re

NS={'o':'http://www.idpf.org/2007/opf'}
BLOCKED={'script','iframe','object','embed','form','input','button','textarea','select','link','style','audio','video','foreignobject','svg','math','base','meta','template','noscript'}

class SampleBook:
 def __init__(self,path):
  self.path=Path(path)
  self.z=ZipFile(path)
  entries=self.z.infolist()
  if len(entries)>20000 or sum(i.file_size for i in entries)>512*1024*1024:raise ValueError('EPUB container limit')
  if len(set(i.filename for i in entries))!=len(entries):raise ValueError('Duplicate ZIP names')
  for item in entries:
   if item.filename.startswith('/') or '\\' in item.filename or '..' in item.filename.split('/'):raise ValueError('Unsafe ZIP name')
  container=self.xml('META-INF/container.xml')
  self.opf_path=next(e.attrib['full-path'] for e in container.iter() if e.tag.endswith('rootfile'))
  opf=self.xml(self.opf_path)
  self.items={e.attrib['id']:e.attrib for e in opf.findall('o:manifest/o:item',NS)}
  self.spine=[self.resolve(self.opf_path,self.items[e.attrib['idref']]['href']) for e in opf.findall('o:spine/o:itemref',NS)]
  spine=opf.find('o:spine',NS)
  self.cfi_bases=[f'/{2*(list(opf).index(spine)+1)}/{2*(i+1)}!' for i in range(len(self.spine))]
  self.progression=spine.attrib.get('page-progression-direction','rtl')
 def read(self,path):
  item=self.z.getinfo(path)
  if item.file_size>16*1024*1024 or item.flag_bits&1:raise ValueError('Entry limit or encrypted ZIP')
  return self.z.read(path)
 def xml(self,path):
  raw=self.read(path)
  if b'<!ENTITY' in raw.upper():raise ValueError('Entity declarations forbidden')
  return ET.fromstring(raw)
 def resolve(self,base,href):
  url=urlsplit(href)
  if url.scheme or url.netloc or href.startswith(('/','\\')):raise ValueError('External resource forbidden')
  path=posixpath.normpath(posixpath.join(posixpath.dirname(base),unquote(url.path)))
  if path.startswith('../') or path=='..' or '\\' in path:raise ValueError('Resource path escape')
  return path
 def css(self,path,stack=()):
  if path in stack or len(stack)>8:raise ValueError('CSS import cycle/depth')
  return self.css_text(self.read(path).decode('utf-8-sig'),path,stack)
 def css_text(self,text,path,stack=()):
  text=re.sub(r'/\*.*?\*/','',text,flags=re.S)
  text=re.sub(r'@charset\s+[^;]+;','',text,flags=re.I)
  imports=[]
  def imported(m):
   imports.append(self.css(self.resolve(path,m.group(1).strip(' \"\'')),stack+(path,)))
   return ''
  text=re.sub(r'@import\s+url\(([^)]+)\)\s*;',imported,text,flags=re.I)
  # No resource loads, CSS escapes or executable legacy properties in this adapter.
  masked=re.sub(r"\"[^\"\\]*\"|'[^'\\]*'", '', text)
  if re.search(r'url\s*\(|@|\\|<|expression\s*\(',masked,re.I) or re.search(r'[\\<]',text):raise ValueError('Unsupported CSS resource or syntax')
  # Imported sheets have already been scoped. Do not scope their selectors a
  # second time (which would turn #source into #source #source).
  result=list(imports);end=0
  for m in re.finditer(r'([^{}]+)\{([^{}]*)\}',text):
   if text[end:m.start()].strip():raise ValueError('Unsupported nested CSS')
   if '>' in m.group(2):raise ValueError('Unsupported CSS declaration')
   selectors=[]
   for selector in m.group(1).split(','):
    selector=selector.strip()
    if re.search(r'\b(html|body)\b',selector):selector=re.sub(r'\b(html|body)\b','#source',selector)
    else:
     # html/body classes are preserved on #source. A class selector must be
     # allowed to match that root as well as descendants of the root.
     match=re.match(r'^(\.[\w-]+)(.*)$',selector,re.S)
     if match and not match[2].lstrip().startswith(('+','~')):selectors.append('#source'+selector)
     selector='#source '+selector
    selectors.append(selector)
   result.append(','.join(selectors)+'{'+m.group(2)+'}')
   end=m.end()
  if text[end:].strip():raise ValueError('Unsupported CSS remainder')
  return '\n'.join(result)
 def image_data(self,path):
  ext=Path(path).suffix.lower()
  mime={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp'}.get(ext)
  if not mime:raise ValueError('Unsupported image format')
  return 'data:'+mime+';base64,'+base64.b64encode(self.read(path)).decode()
 def svg_image(self,element,path):
  return None
 def navigation_href(self,base,href,element):
  return None
 def prepare_body(self,root,body,index):
  return lambda:None
 def document(self,index,provenance=False,preserve_styles=False,chapter_paths=(),preserve_breaks=False):
  path=self.spine[index];root=self.xml(path)
  css=[]
  for e in root.iter():
   if e.tag.rsplit('}',1)[-1]=='link' and 'stylesheet' in e.attrib.get('rel','').split():css.append(self.css(self.resolve(path,e.attrib['href'])))
  body=next(e for e in root.iter() if e.tag.rsplit('}',1)[-1]=='body')
  if preserve_styles or getattr(self,'include_styles',False):
   for element in root.iter():
    if element.tag.startswith('{http://www.w3.org/2000/svg}'):continue
    if element.tag.rsplit('}',1)[-1]=='style':css.append(self.css_text(element.text or '',path))
    if preserve_styles and 'style' in element.attrib:
     style=element.attrib['style']
     if '{' in style or '}' in style:raise ValueError('Invalid inline CSS')
     self.css_text('body{'+style+'}',path)

  finish_body=self.prepare_body(root,body,index)
  chapter_targets=[]
  for positions in chapter_paths:
   target=body
   for position in positions:target=list(target)[position]
   chapter_targets.append(target)
  root_classes=root.attrib.get('class','')
  # Remove imported provenance attributes before creating app-owned metadata.
  for e in root.iter():
   for key in list(e.attrib):
    if key.startswith('data-stage0-'):del e.attrib[key]
  if provenance:
   def annotate(element,path):
    if element.tag=='{http://www.w3.org/2000/svg}svg':return
    children=list(element)
    for i,child in enumerate(children):annotate(child,path+f'/{2*(i+1)}')
    if element.text:
     span=ET.Element('span',{'data-stage0-cfi':path+'/1'})
     span.text=element.text;element.text=None;element.insert(0,span)
    for i,child in enumerate(children):
     if child.tail:
      span=ET.Element('span',{'data-stage0-cfi':path+f'/{2*(i+1)+1}'})
      span.text=child.tail;child.tail=None;element.insert(list(element).index(child)+1,span)
   # Compute paths in the untouched XML tree, before sanitizer/display spans.
   def body_path(element,path=''):
    if element is body:return path
    for i,child in enumerate(element):
     found=body_path(child,path+f'/{2*(i+1)}')
     if found is not None:return found
   annotate(body,body_path(root))
  finish_body()
  for target in chapter_targets:
   marker=ET.Element('div',{'data-chapter-start':'true','data-reader-chapter':'true','style':'display:block!important;height:0!important;width:0!important;margin:0!important;padding:0!important'})
   if target is body:body.insert(0,marker)
   else:
    parent=next(element for element in body.iter() if target in list(element))
    parent.insert(list(parent).index(target),marker)
  def clean(parent):
   for child in list(parent):
    tag=child.tag.rsplit('}',1)[-1]
    if tag.lower()=='svg':
     image=self.svg_image(child,path)
     if image is not None:
      image.tail=child.tail
      parent.insert(list(parent).index(child),image);parent.remove(child)
      continue
    if tag.lower() in BLOCKED:
     tail=child.tail or '';previous=child.getprevious() if hasattr(child,'getprevious') else None
     siblings=list(parent);position=siblings.index(child);previous=siblings[position-1] if position else None
     parent.remove(child)
     if tail:
      if previous is None:parent.text=(parent.text or '')+tail
      else:previous.tail=(previous.tail or '')+tail
     continue
    child.tag=tag
    navigation=self.navigation_href(path,child.get('href'),child) if tag.lower()=='a' and 'href' in child.attrib else None
    keep_style=preserve_styles or bool(child.get('data-reader-chapter'))
    if preserve_breaks and not keep_style:
     declarations=re.findall(r'(?:^|;)\s*((?:page-)?break-(?:before|after))\s*:\s*(auto|always|page|column|left|right|recto|verso|avoid)(?:\s*!important)?\s*(?=;|$)',child.get('style',''),re.I)
     if declarations:
      child.set('style',';'.join(name.lower()+':'+value.lower() for name,value in declarations))
      keep_style=True
    for key,value in list(child.attrib.items()):
     if key.lower().startswith('on') or key in (['srcset'] if keep_style else ['style','srcset']) or ':' in key or key.startswith('{'):del child.attrib[key]
     elif key in ['href','src']:
      if key=='href':
       if navigation:child.attrib[key]=navigation
       else:del child.attrib[key]
      elif tag=='img':
       child.attrib[key]=self.image_data(self.resolve(path,value))
      else:del child.attrib[key]
    clean(child)
  clean(body)
  classes=html.escape(root_classes+' '+body.attrib.get('class',''),quote=True)
  content=html.escape(body.text or '')+''.join(ET.tostring(c,encoding='unicode',method='html') for c in body)
  if preserve_styles:
   style=html.escape(body.attrib.get('style',''),quote=True)
   content='<div class="reader-fixed-page '+classes+'" style="'+style+'">'+content+'</div>'
  provenance_attrs=''
  if provenance:
   provenance_attrs=f' data-spine="{index}" data-href="{html.escape(path,quote=True)}" data-stage0-base="{self.cfi_bases[index]}"'
  return '<article id="source" class="'+classes+'"'+provenance_attrs+'>'+content+'</article>', '\n'.join(css)
