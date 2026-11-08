"""Source identities and CSS isolation for the trial append adapter."""
from pathlib import Path
from html.parser import HTMLParser
import tempfile,unittest
from .test_library import fixture
from .test_fixed import mixed_fixture
from .progressive import ProgressiveBook
from .seamless_book import SeamlessBook

class Markup(HTMLParser):
    def __init__(self):super().__init__();self.text=[];self.parts=[];self.keys=[]
    def handle_data(self,data):self.text.append(data)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if 'data-reader-part' in attrs:self.parts.append(attrs)
        if 'data-reader-node' in attrs:self.keys.append(attrs['data-reader-node'])

class SeamlessTests(unittest.TestCase):
    def test_windows_keep_text_identity_and_document_scoped_styles(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'owned.epub'
            fixture(p,body='<h2 id="chapter">章</h2><p data-reader-node="forged">冒頭。'+''.join('前<ruby>猫<rt>ねこ</rt></ruby>後。' for _ in range(900))+'</p>')
            with ProgressiveBook(p) as previous,SeamlessBook(p) as book:
                expected=[];actual=[]
                for part in range(book.part_count(0)):
                    a=Markup();a.feed(previous.document(0,True,part=part)[0]);expected.extend(a.text)
                    html,css=book.document(0,True,part=part);b=Markup();b.feed(html);actual.extend(b.text)
                    self.assertEqual(len(b.parts),1)
                    self.assertEqual(b.parts[0]['data-spine'],'0')
                    self.assertTrue(b.keys)
                    self.assertNotIn('forged',b.keys)
                    self.assertIn('[data-reader-part="0"]',css)
                    self.assertIn('display:contents',html)
                self.assertEqual(''.join(actual),''.join(expected))
                self.assertIn('[data-reader-part="1"]',book.document(1,True)[1])
    def test_chapter_breaks_do_not_repeat_on_small_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'owned.epub'
            fixture(p,body='<h2>第一章</h2>'+'<p>続きの本文。</p>'*1000+'<h2>第二章</h2><p>末尾。</p>')
            with SeamlessBook(p) as book:
                documents=[book.document(0,True,part=i)[0] for i in range(book.part_count(0))]
                self.assertGreater(len(documents),2)
                self.assertEqual(sum(s.count('data-reader-chapter-break="true"') for s in documents),2)
                self.assertIn('break-before:column',documents[0])
                self.assertTrue(any('data-reader-chapter-break' not in s for s in documents))

    def test_fixed_pages_are_not_wrapped_or_combined(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'owned.epub';mixed_fixture(p)
            with SeamlessBook(p) as book:
                html,css=book.document(1,True)
                self.assertNotIn('data-reader-part=',html)
                self.assertIn('reader-fixed-page',html)
                self.assertIn('position:absolute',css)

if __name__=='__main__':unittest.main()
