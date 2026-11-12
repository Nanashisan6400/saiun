import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import struct
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from .library import Library

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')


def fixture(path, title='ＡＢＣ 検証書籍', css='body{writing-mode:vertical-rl} .tcy{text-combine-upright:all}', body=None, root_class=''):
    with ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip')
        archive.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        archive.writestr('book.opf', '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>'+title+'</dc:title><dc:creator>検証著者</dc:creator><dc:language>ja</dc:language></metadata><manifest><item id="a" href="a.xhtml"/><item id="b" href="b.xhtml"/><item id="nav" href="nav.xhtml" properties="nav"/><item id="cover" href="cover.png" properties="cover-image" media-type="image/png"/></manifest><spine page-progression-direction="rtl"><itemref idref="a"/><itemref idref="b"/></spine></package>')
        archive.writestr('style.css', css)
        archive.writestr('cover.png', PNG)
        for index, name in enumerate(['a', 'b']):
            content = body if body is not None else '<h1 id="chapter">第'+str(index+1)+'章</h1>'+''.join(
                '<p id="p'+str(i)+'">【'+str(i)+'】春の風が吹く。「読書を続けよう」ルークとストーン。小さいィとッ。＜＞……。<ruby>図書館<rt>としょかん</rt></ruby>と<span class="tcy">12</span>月。</p>' for i in range(36))
            archive.writestr(name+'.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml" class="'+root_class+'"><head><title>合成本文</title><link rel="stylesheet" href="style.css"/></head><body>'+content+'</body></html>')
        archive.writestr('nav.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol><li><a href="a.xhtml#chapter">第一章</a></li><li><a href="b.xhtml#chapter">第二章</a></li></ol></nav></body></html>')


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root/'original.epub'
        fixture(self.source)
        self.library = Library(self.root/'library')

    def locator(self, row, index=0, progression=.3):
        return dict(version=1, sha256=row['sha'], spine_index=index,
                    href=json.loads(row['spine_hrefs'])[index], cfi=None, fragment='chapter', progression=progression)

    def test_copy_metadata_cover_and_unicode_search(self):
        original = self.source.read_bytes()
        row, added = self.library.import_book(self.source)
        self.assertTrue(added)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.library.path(row['sha']).read_bytes(), original)
        self.assertEqual(row['sha'], hashlib.sha256(original).hexdigest())
        self.assertTrue(row['cover'])
        self.assertEqual(len(self.library.listing('abc')), 1)
        self.assertEqual(json.loads(row['authors']), ['検証著者'])

    def test_invalid_epub_page_progression_uses_writing_mode_default(self):
        fixture(self.source, css='body{writing-mode:horizontal-tb}')
        with ZipFile(self.source) as archive:
            entries = {name:archive.read(name) for name in archive.namelist()}
        entries['book.opf'] = entries['book.opf'].replace(b'page-progression-direction="rtl"', b'page-progression-direction="sideways"')
        with ZipFile(self.source, 'w') as archive:
            for name, raw in entries.items():
                archive.writestr(name, raw)
        from .epub import EpubBook
        with EpubBook(self.source) as book:
            self.assertIsNone(book.progression)

    def test_horizontal_ltr_page_progression_is_preserved(self):
        fixture(self.source, css='body{writing-mode:horizontal-tb}')
        with ZipFile(self.source) as archive:
            entries = {name:archive.read(name) for name in archive.namelist()}
        entries['book.opf'] = entries['book.opf'].replace(b'page-progression-direction="rtl"', b'page-progression-direction="ltr"')
        with ZipFile(self.source, 'w') as archive:
            for name, raw in entries.items():
                archive.writestr(name, raw)
        from .epub import EpubBook
        with EpubBook(self.source) as book:
            self.assertEqual(book.progression, 'ltr')

    def test_duplicate_keeps_position_options_and_preferences_after_restart(self):
        row, _ = self.library.import_book(self.source)
        locator = self.locator(row)
        self.library.save_position(row['sha'], locator)
        self.library.save_options(row['sha'], {'size':24})
        self.library.set_setting('theme', 'dark')
        self.library.toggle_bookmark(row['sha'], locator)
        restarted = Library(self.library.root)
        same, added = restarted.import_book(self.source)
        self.assertFalse(added)
        self.assertEqual(json.loads(same['position']), locator)
        self.assertEqual(json.loads(same['options']), {'size':24})
        self.assertEqual(restarted.setting('theme'), 'dark')
        self.assertEqual(len(restarted.bookmarks(row['sha'])), 1)

    def test_concurrent_imports_share_one_row(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.library.import_book, [self.source, self.source]))
        self.assertEqual(sum(added for row, added in results), 1)
        self.assertEqual(len(self.library.listing()), 1)
        self.assertEqual(len(list(self.library.books.glob('*.epub'))), 1)
        self.assertEqual(list(self.library.books.glob('.import-*')), [])

    def test_invalid_resources_leave_original_and_no_registration(self):
        fixture(self.source, css='@import url(https://example.org/style.css);')
        original = self.source.read_bytes()
        with self.assertRaises(ValueError):
            self.library.import_book(self.source)
        self.assertEqual(self.library.listing(), [])
        self.assertEqual(list(self.library.books.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), original)

    def test_child_selector_import_and_unsafe_css_discarded(self):
        fixture(self.source, css='body > p, div  > p {margin:0}')
        row, added = self.library.import_book(self.source)
        self.assertTrue(added)
        from .epub import EpubBook
        with EpubBook(self.library.path(row['sha'])) as book:
            self.assertIn('#source > p', book.document(0)[1])
        for css in ['body{content:"</style>"}', 'body{background:url(https://example.org/a)}', 'body{content:">"}']:
            fixture(self.source, css=css)
            row, _ = self.library.import_book(self.source)
            self.assertTrue(json.loads(row['standard_style']))
            with EpubBook(self.source) as book:
                self.assertNotIn(css, book.document(0)[1])

    def test_imported_css_is_scoped_once_and_root_class_matches(self):
        fixture(self.source, css='@import url(base.css); .vrtl h1{margin:0}')
        with ZipFile(self.source, 'a') as archive:
            archive.writestr('base.css', '.vrtl{-webkit-writing-mode:vertical-rl} body > p{margin:0}')
        from .epub import EpubBook
        with EpubBook(self.source) as book:
            book.validate()
            css = book.document(0)[1]
            self.assertNotIn('#source #source', css)
            self.assertIn('#source.vrtl', css)
            self.assertIn('#source > p', css)
            self.assertIn('#source.vrtl h1', css)

    def test_extracted_epub_folder_import_duplicate_and_original_unchanged(self):
        folder = self.root/'expanded.epub'
        folder.mkdir()
        with ZipFile(self.source) as archive:
            archive.extractall(folder)
        before = {p.relative_to(folder): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        row, added = self.library.import_book(folder)
        self.assertTrue(added)
        self.library.save_options(row['sha'], {'size':24})
        self.library.toggle_bookmark(row['sha'], self.locator(row))
        same, added = self.library.import_book(folder)
        self.assertFalse(added)
        self.assertEqual(same['sha'], row['sha'])
        self.assertEqual(json.loads(same['options']), {'size':24})
        self.assertEqual(len(self.library.bookmarks(row['sha'])), 1)
        self.assertEqual(before, {p.relative_to(folder): p.read_bytes() for p in folder.rglob('*') if p.is_file()})
        with ZipFile(self.library.path(row['sha'])) as archive:
            self.assertEqual(archive.infolist()[0].filename, 'mimetype')
            self.assertEqual(archive.infolist()[0].compress_type, 0)

    def test_folder_rejects_links_and_incomplete_input(self):
        folder = self.root/'expanded'
        folder.mkdir()
        with self.assertRaises(ValueError):
            self.library.import_book(folder)
        with ZipFile(self.source) as archive:
            archive.extractall(folder)
        (folder/'escape').symlink_to(self.source)
        with self.assertRaises(ValueError):
            self.library.import_book(folder)
        self.assertEqual(self.library.listing(), [])
        self.assertEqual(list(self.library.root.glob('.folder-*')), [])

    def test_remove_cleans_managed_data_only_and_reimport_is_fresh(self):
        original = self.source.read_bytes()
        row, _ = self.library.import_book(self.source)
        other = self.root/'other.epub'
        fixture(other, title='別の本')
        second, _ = self.library.import_book(other)
        self.library.save_position(row['sha'], self.locator(row))
        self.library.toggle_bookmark(row['sha'], self.locator(row))
        self.library.remove_book(row['sha'])
        self.assertFalse(self.library.path(row['sha']).exists())
        self.assertFalse((self.library.books/row['cover']).exists())
        self.assertEqual(self.library.bookmarks(row['sha']), [])
        self.assertTrue(self.library.path(second['sha']).exists())
        self.assertEqual(self.source.read_bytes(), original)
        fresh, added = self.library.import_book(self.source)
        self.assertTrue(added)
        self.assertIsNone(fresh['position'])

    def test_removal_retries_file_cleanup_after_restart(self):
        from unittest.mock import patch
        row, _ = self.library.import_book(self.source)
        with patch.object(Path, 'unlink', side_effect=PermissionError):
            self.library.remove_book(row['sha'])
        self.assertEqual(self.library.listing(), [])
        self.assertTrue(self.library.path(row['sha']).exists())
        Library(self.library.root)
        self.assertFalse(self.library.path(row['sha']).exists())

    def test_position_identity_bookmark_toggle_and_backwards_progress(self):
        row, _ = self.library.import_book(self.source)
        locator = self.locator(row, 1, .8)
        self.library.save_position(row['sha'], locator)
        self.library.save_position(row['sha'], self.locator(row, 0, 0))
        self.assertEqual(self.library.get(row['sha'])['progress'], 0)
        with self.assertRaises(ValueError):
            self.library.save_position(row['sha'], locator | {'sha256':'0'*64})
        with self.assertRaises(ValueError):
            self.library.save_position(row['sha'], locator | {'href':'wrong.xhtml'})
        self.assertTrue(self.library.toggle_bookmark(row['sha'], locator))
        self.assertFalse(self.library.toggle_bookmark(row['sha'], locator))
        self.assertEqual(self.library.bookmarks(row['sha']), [])

    def test_reimport_repairs_missing_managed_copy_and_keeps_position(self):
        row, _ = self.library.import_book(self.source)
        locator = self.locator(row)
        self.library.save_position(row['sha'], locator)
        self.library.path(row['sha']).unlink()
        same, added = self.library.import_book(self.source)
        self.assertFalse(added)
        self.assertEqual(json.loads(same['position']), locator)
        self.assertEqual(hashlib.sha256(self.library.path(row['sha']).read_bytes()).hexdigest(), row['sha'])

    def test_rejects_unsupported_svg_and_reserved_ids(self):
        for body in ['<svg xmlns="http://www.w3.org/2000/svg"/>', '<p id="viewport">text</p>', '<plaintext>text</plaintext>']:
            fixture(self.source, body=body)
            with self.assertRaises(ValueError):
                self.library.import_book(self.source)
        self.assertEqual(self.library.listing(), [])

    def test_locator_href_is_an_opf_relative_uri(self):
        with ZipFile(self.source, 'w') as archive:
            archive.writestr('META-INF/container.xml', '<container><rootfile full-path="OEBPS/book.opf"/></container>')
            archive.writestr('OEBPS/book.opf', '<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>URI検証</dc:title></metadata><manifest><item id="body" href="Text/章%20一.xhtml"/></manifest><spine><itemref idref="body"/></spine></package>')
            archive.writestr('OEBPS/Text/章 一.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>原本</p></body></html>')
        row, _ = self.library.import_book(self.source)
        self.assertEqual(json.loads(row['spine_hrefs']), ['Text/%E7%AB%A0%20%E4%B8%80.xhtml'])
        self.library.save_position(row['sha'], self.locator(row))

    def test_utf16_entity_declarations_are_rejected_before_parsing(self):
        with ZipFile(self.source) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries['a.xhtml'] = ('<?xml version="1.0" encoding="UTF-16"?>'
                              '<!DOCTYPE html [<!ENTITY sample "expanded">]>'
                              '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>&sample;</p></body></html>').encode('utf-16')
        with ZipFile(self.source, 'w') as archive:
            for name, raw in entries.items():
                archive.writestr(name, raw)
        with self.assertRaisesRegex(ValueError, 'Entity declarations'):
            self.library.import_book(self.source)
        self.assertEqual(self.library.listing(), [])

    def test_oversized_image_is_rejected_before_native_decoding(self):
        fixture(self.source, body='<img src="cover.png"/>')
        with ZipFile(self.source) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries['cover.png'] = PNG[:16]+struct.pack('>II',100000,100000)+PNG[24:]
        with ZipFile(self.source, 'w') as archive:
            for name, raw in entries.items():
                archive.writestr(name, raw)
        with self.assertRaisesRegex(ValueError, '画像寸法'):
            self.library.import_book(self.source)
        self.assertEqual(self.library.listing(), [])

    def test_recent_read_sort_precedes_new_unread_books(self):
        first, _ = self.library.import_book(self.source)
        self.library.save_position(first['sha'], self.locator(first))
        second_source = self.root/'second.epub'
        fixture(second_source, title='後から追加した本')
        second, _ = self.library.import_book(second_source)
        self.assertEqual(self.library.listing(sort='recent')[0]['sha'], first['sha'])
        self.assertEqual(self.library.listing(sort='added')[0]['sha'], second['sha'])

    def test_book_markup_cannot_supply_reader_source_coordinates(self):
        from .epub import EpubBook
        fixture(self.source, body='<p data-spine="99" data-href="wrong.xhtml" data-stage0-cfi="/999">本文</p>')
        with EpubBook(self.source) as book:
            article, _ = book.document(0, provenance=True)
        self.assertNotIn('data-spine="99"', article)
        self.assertNotIn('wrong.xhtml', article)
        self.assertNotIn('/999', article)
        self.assertIn('data-spine="0"', article)

    def test_oversized_zip_entry_is_rejected_before_xml_parsing(self):
        with ZipFile(self.source) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries['a.xhtml'] = b'x'*(16*1024*1024+1)
        with ZipFile(self.source, 'w') as archive:
            for name, raw in entries.items():
                archive.writestr(name, raw)
        with self.assertRaisesRegex(ValueError, 'Entry limit'):
            self.library.import_book(self.source)
        self.assertEqual(self.library.listing(), [])


if __name__ == '__main__':
    unittest.main()
