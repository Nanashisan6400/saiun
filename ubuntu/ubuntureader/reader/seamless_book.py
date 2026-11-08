"""Trial-only source identities for merging adjacent windows without page breaks."""
import re
from .progressive import ProgressiveBook
from .sections import paths, local
from .chapters import EPUB_TYPE, EXCLUDED
from .chapters import scoped_styles


class SeamlessBook(ProgressiveBook):
    def prepare_body(self,root,body,index):
        self.reader_body_style=body.get('style','')
        chapter_ids={c['fragment'] for c in self.chapters
                     if c['spine']==index and c.get('fragment')}
        def mark(node, excluded=False, inside_chapter=False):
            types=set(node.get(EPUB_TYPE,'').split())
            roles=set(node.get('role','').split())
            excluded=excluded or bool(types & EXCLUDED) or bool(roles & {'doc-footnote','doc-endnote','doc-toc'}) or local(node) in {'nav','aside','script','style'}
            chapter=not excluded and (bool(types & {'chapter'}) or 'doc-chapter' in roles or node.get('id') in chapter_ids or local(node) in {'h1','h2'})
            # A chapter container and its heading describe one boundary.
            if chapter and not inside_chapter:
                node.set('data-reader-chapter-break','true')
                node.set('style',node.get('style','')+';break-before:column!important')
            for child in node:
                mark(child,excluded,inside_chapter or chapter)
        mark(body)
        for node,path in paths(root).items():
            node.set('data-reader-node','/'.join(map(str,path)))
        return super().prepare_body(root,body,index)

    def svg_image(self,element,path):
        image=super().svg_image(element,path)
        image.set('data-reader-node',element.get('data-reader-node',''))
        return image

    def document(self,index,provenance=False,chapter_paths=(),part=0):
        article,css=super().document(index,provenance,chapter_paths,part)
        if self.layouts[index]=='fixed':
            return article,css
        head,content=article.split('>',1)
        head=head.replace('<article','<section',1)
        head=re.sub(r'\bid="source"',f'data-reader-part="{index}" style="display:contents!important"',head)
        before=re.search(r'(?:^|;)\s*(?:page-)?break-before\s*:\s*(always|page|column|left|right|recto|verso)\b',getattr(self,'reader_body_style',''),re.I)
        after=re.search(r'(?:^|;)\s*(?:page-)?break-after\s*:\s*(always|page|column|left|right|recto|verso)\b',getattr(self,'reader_body_style',''),re.I)
        head+=f' data-reader-doc-start="{int(part==0)}" data-reader-doc-end="{int(part==self.part_count(index)-1)}"'
        if before:head+=' data-reader-break-before="column"'
        if after:head+=' data-reader-break-after="column"'
        section=head+'>'+content[:-len('</article>')]+'</section>' 
        return '<article id="source">'+section+'</article>',scoped_styles(css,index)
