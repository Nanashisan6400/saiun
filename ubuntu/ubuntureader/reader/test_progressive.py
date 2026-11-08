"""Trial windows preserve text and source positions across mixed paragraphs."""
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from .progressive import ProgressiveBook, WINDOW_CHARS
from .epub import EpubBook
from .test_library import fixture

class Text(HTMLParser):
    def __init__(self):super().__init__();self.text=[];self.slots=[]
    def handle_data(self,data):self.text.append(data)
    def handle_starttag(self,tag,attrs):
        path=dict(attrs).get('data-stage0-cfi')
        if path:self.slots.append(path)

class ProgressiveTests(unittest.TestCase):
    def test_mixed_paragraph_windows_keep_every_character_and_cfi(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'book.epub'
            body='<h2 id="chapter">長い章</h2><p id="para">冒頭。'+''.join(f'前{i}。<ruby>図書館<rt>としょかん</rt></ruby>後{i}。' for i in range(900))+'</p><p>末尾。</p>'
            fixture(path,body=body)
            original=path.read_bytes()
            with EpubBook(path) as normal, ProgressiveBook(path) as trial:
                self.assertEqual(normal.part_count(0),1)
                self.assertGreater(trial.part_count(0),3)
                expected=Text();expected.feed(normal.document(0,True)[0])
                actual=[]
                for part in range(trial.part_count(0)):
                    parsed=Text();parsed.feed(trial.document(0,True,part=part)[0]);actual.extend(parsed.text)
                    self.assertLessEqual(len(''.join(parsed.text)),WINDOW_CHARS+50)
                    for slot in parsed.slots:
                        locator=dict(cfi='epubcfi('+trial.cfi_bases[0]+slot+':0)')
                        self.assertEqual(trial.part_for(0,locator),part)
                self.assertEqual(''.join(actual),''.join(expected.text))
            self.assertEqual(path.read_bytes(),original)

    def test_each_small_chapter_remains_its_own_window(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'book.epub';fixture(path,body='<h2 id="one">一章</h2><p>本文</p><h2 id="two">二章</h2><p>続き</p>')
            with ProgressiveBook(path) as book:
                self.assertEqual(book.part_count(0),2)
                self.assertEqual(book.part_for(0,dict(fragment='two')),1)

if __name__=='__main__':unittest.main()
