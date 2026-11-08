"""Regression coverage for mixed EPUB metadata and safe fixed-page styles."""
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from .epub import EpubBook
from .library import Library
from .test_library import fixture


def mixed_fixture(path, viewport='width=600,height=800', style='margin:0'):
    fixture(path, title='固定ページの検証')
    with ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    package = entries['book.opf'].decode().replace('</manifest>', '<item id="f" href="fixed.xhtml" media-type="application/xhtml+xml"/></manifest>')
    package = package.replace('<itemref idref="b"/>', '<itemref idref="f" properties="rendition:layout-pre-paginated"/><itemref idref="b"/>')
    entries['book.opf'] = package.encode()
    entries['fixed.xhtml'] = ('''<html xmlns="http://www.w3.org/1999/xhtml"><head>
<meta name="viewport" content="'''+viewport+'''"/>
<style>.panel{position:absolute;left:40px;top:40px;width:520px;height:720px;background:#e4f0ff;border:2px solid #204b70}
.caption{position:absolute;left:40px;top:100px;font:32px sans-serif;color:#183d60}</style>
</head><body style="'''+style+'''"><div class="panel"><h1 style="margin:30px">固定ページ</h1>
<p class="caption">600 × 800<br/>拡大・ドラッグ・全体表示</p>
<img src="cover.png" style="position:absolute;left:40px;top:550px;width:80px;height:80px"/>
</div></body></html>''').encode()
    with ZipFile(path, 'w') as archive:
        for name, raw in entries.items():
            archive.writestr(name, raw)


class FixedTests(unittest.TestCase):
    def test_mixed_import_and_position(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'mixed.epub'
            mixed_fixture(path)
            with EpubBook(path) as book:
                book.validate()
                self.assertEqual(book.layouts, ['reflowable', 'fixed', 'reflowable'])
                self.assertEqual(book.fixed_size(1), (600, 800))
                article, css = book.document(1, provenance=True)
                self.assertIn('position:absolute', css)
                self.assertIn('style="margin:30px"', article)
                self.assertNotIn('<style', article)
                self.assertNotIn('data-stage0-cfi', article)
            library = Library(Path(directory)/'library')
            row, _ = library.import_book(path)
            locator = dict(version=1, sha256=row['sha'], spine_index=1, href='fixed.xhtml',
                           kind='fixed', spine=1, cfi=None, fragment=None, progression=0)
            library.save_position(row['sha'], locator)
            library.save_options(row['sha'], {'size':18, 'fixedZoom':2})
            self.assertTrue(library.toggle_bookmark(row['sha'], locator))
            self.assertEqual(library.get(row['sha'])['progress'], 1/3)

    def test_default_fixed_with_reflow_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'mixed.epub'
            mixed_fixture(path)
            with ZipFile(path) as archive:
                entries = {name:archive.read(name) for name in archive.namelist()}
            entries['book.opf'] = entries['book.opf'].replace(b'</metadata>', b'<meta property="rendition:layout">pre-paginated</meta></metadata>').replace(b'<itemref idref="a"/>', b'<itemref idref="a" properties="rendition:layout-reflowable"/>').replace(b'<itemref idref="b"/>', b'<itemref idref="b" properties="rendition:layout-reflowable"/>').replace(b'properties="rendition:layout-pre-paginated"', b'properties=""')
            with ZipFile(path, 'w') as archive:
                for name, raw in entries.items():
                    archive.writestr(name,raw)
            with EpubBook(path) as book:
                self.assertEqual(book.layouts, ['reflowable','fixed','reflowable'])
                book.validate()

    def test_invalid_viewport_and_resource_styles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'invalid.epub'
            for viewport, style in [('width=device-width,height=800', ''), ('width=99999,height=800',''),
                                    ('width=600,height=800','background:url(file:///etc/passwd)')]:
                with self.subTest(viewport=viewport,style=style):
                    mixed_fixture(path,viewport,style)
                    with EpubBook(path) as book:
                        with self.assertRaises(ValueError):
                            book.validate()


if __name__ == '__main__':
    unittest.main()
