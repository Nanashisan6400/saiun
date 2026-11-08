"""Local, account-independent library with atomic copies and SQLite checkpoints."""
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
import os
import stat
from pathlib import Path
import sqlite3
import tempfile
import unicodedata
import uuid
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED, ZIP_STORED

from .epub import EpubBook

MAX_FILE_BYTES = 128 * 1024 * 1024


def normalized(text):
    return unicodedata.normalize('NFKC', text).casefold()


def now():
    return datetime.now(timezone.utc).isoformat()


class Library:
    def __init__(self, root=None):
        base = Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))
        self.root = Path(root) if root else base/'ebookproject-reader'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.books = self.root/'books'
        self.books.mkdir(exist_ok=True, mode=0o700)
        self.database = self.root/'library.sqlite3'
        database_existed = self.database.exists()
        with self.connect() as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise ValueError('このライブラリは新しい形式です。対応するアプリで開いてください。')
            connection.execute('PRAGMA journal_mode=WAL')
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS books (
                    sha TEXT PRIMARY KEY, title TEXT NOT NULL, authors TEXT NOT NULL,
                    search_text TEXT NOT NULL, language TEXT, spine_hrefs TEXT NOT NULL,
                    created TEXT NOT NULL, last_read TEXT, status TEXT NOT NULL DEFAULT 'unread',
                    position TEXT, progress REAL NOT NULL DEFAULT 0, options TEXT NOT NULL DEFAULT '{}',
                    cover TEXT
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS pending_deletions (name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS bookmarks (
                    id TEXT PRIMARY KEY, sha TEXT NOT NULL REFERENCES books(sha),
                    location_key TEXT NOT NULL, locator TEXT NOT NULL, label TEXT NOT NULL,
                    created TEXT NOT NULL, UNIQUE(sha, location_key)
                );
                PRAGMA user_version=1;
            ''')
            columns = {row[1] for row in connection.execute('PRAGMA table_info(books)')}
            if 'standard_style' not in columns:
                backup = self.root/'library.pre-standard-style.sqlite3'
                if database_existed and not backup.exists():
                    with closing(sqlite3.connect(backup)) as snapshot:
                        connection.backup(snapshot)
                    os.chmod(backup, 0o600)
                connection.execute("ALTER TABLE books ADD COLUMN standard_style TEXT NOT NULL DEFAULT '[]'")
        os.chmod(self.database, 0o600)
        self.cleanup_deletions()

    def cleanup_deletions(self):
        with self.connect() as connection:
            for row in connection.execute('SELECT name FROM pending_deletions').fetchall():
                name = row['name']
                # Only generated content names, never source paths or directories.
                stem, suffix = os.path.splitext(name)
                self.path(stem)
                if suffix not in ('.epub', '.png', '.jpg', '.jpeg', '.gif', '.svg'):
                    raise ValueError('削除対象が不正です。')
                try:
                    (self.books/name).unlink(missing_ok=True)
                except OSError:
                    continue
                connection.execute('DELETE FROM pending_deletions WHERE name=?', (name,))

    def remove_book(self, sha):
        self.remove_books([sha])

    def remove_books(self, shas):
        shas = list(dict.fromkeys(shas))
        for sha in shas:
            self.path(sha)
        with self.connect() as connection:
            for sha in shas:
                row = connection.execute('SELECT cover FROM books WHERE sha=?', (sha,)).fetchone()
                if row is None:
                    continue
                for name in [sha+'.epub', row['cover']]:
                    if name:
                        connection.execute('INSERT OR IGNORE INTO pending_deletions VALUES (?)', (name,))
                connection.execute('DELETE FROM bookmarks WHERE sha=?', (sha,))
                connection.execute('DELETE FROM books WHERE sha=?', (sha,))
        self.cleanup_deletions()

    def import_folder(self, source):
        # Build a bounded, reproducible archive in private temporary storage.
        # The source tree is never changed; symlinks and special files are refused.
        if source.is_symlink() or not (source/'META-INF/container.xml').is_file() or not (source/'mimetype').is_file():
            raise ValueError('EPUBのルートフォルダ（mimetypeとMETA-INFがある場所）を選んでください。')
        entries = []
        total = 0
        nodes = 0
        def visit(directory, depth=0):
            nonlocal total, nodes
            if depth > 64:
                raise ValueError('EPUBフォルダの階層が深すぎます。')
            for entry in directory.iterdir():
                nodes += 1
                if nodes > 20000:
                    raise ValueError('EPUBフォルダの項目数が上限を超えています。')
                mode = entry.lstat().st_mode
                if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    raise ValueError('リンクや特殊ファイルを含むフォルダは取り込めません。')
                if stat.S_ISDIR(mode):
                    visit(entry, depth+1)
                else:
                    size = entry.stat().st_size
                    total += size
                    if size > 16*1024*1024 or total > 512*1024*1024 or len(entries) >= 20000:
                        raise ValueError('EPUBフォルダの容量または項目数が上限を超えています。')
                    entries.append(entry)
        visit(source)
        if (source/'mimetype').read_bytes() != b'application/epub+zip':
            raise ValueError('EPUBのmimetype指定が不正です。')
        entries.sort(key=lambda p: (p.name != 'mimetype' or p.parent != source, p.relative_to(source).as_posix()))
        with tempfile.TemporaryDirectory(prefix='.folder-', dir=self.root) as directory:
            archive_path = Path(directory)/'book.epub'
            with ZipFile(archive_path, 'w') as archive:
                read_total = 0
                for entry in entries:
                    info = ZipInfo(entry.relative_to(source).as_posix())
                    info.compress_type = ZIP_STORED if info.filename == 'mimetype' else ZIP_DEFLATED
                    info.external_attr = 0o100600 << 16
                    # O_NOFOLLOW also protects the final open against link swaps.
                    descriptor = os.open(entry, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(descriptor, 'rb') as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            raise ValueError('通常のファイルのみ取り込めます。')
                        raw = stream.read(16*1024*1024+1)
                    read_total += len(raw)
                    if len(raw) > 16*1024*1024 or read_total > 512*1024*1024:
                        raise ValueError('EPUBフォルダ内のファイルが上限を超えています。')
                    archive.writestr(info, raw)
                    if archive.fp.tell() > MAX_FILE_BYTES:
                        raise ValueError('EPUBのサイズ上限を超えました。')
            return self.import_book(archive_path)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def path(self, sha):
        if len(sha) != 64 or any(char not in '0123456789abcdef' for char in sha):
            raise ValueError('書籍IDが不正です。')
        return self.books/(sha+'.epub')

    def get(self, sha):
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM books WHERE sha=?', (sha,)).fetchone()
        if row is None:
            raise ValueError('書籍が見つかりません。')
        return dict(row)

    def listing(self, query='', status='all', sort='recent'):
        columns = {'recent': 'last_read IS NULL, last_read DESC, created DESC', 'added': 'created DESC',
                   'title': 'title COLLATE NOCASE', 'author': 'authors COLLATE NOCASE'}
        with self.connect() as connection:
            rows = connection.execute('SELECT * FROM books ORDER BY '+columns.get(sort, columns['recent'])).fetchall()
        query = normalized(query)
        return [dict(row) for row in rows if query in row['search_text'] and (status == 'all' or row['status'] == status)]

    def setting(self, key, default=None):
        with self.connect() as connection:
            row = connection.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.connect() as connection:
            connection.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                               (key, json.dumps(value)))

    def import_book(self, source):
        source = Path(source)
        if source.is_dir():
            return self.import_folder(source)
        if not source.is_file() or source.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('128MB以下のEPUBファイルを選んでください。')
        temporary = None
        try:
            digest = hashlib.sha256()
            total = 0
            with source.open('rb') as original, tempfile.NamedTemporaryFile(dir=self.books, prefix='.import-', delete=False) as target:
                temporary = Path(target.name)
                while chunk := original.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_FILE_BYTES:
                        raise ValueError('EPUBのサイズ上限を超えました。')
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            sha = digest.hexdigest()
            destination = self.path(sha)
            with self.connect() as connection:
                existing = connection.execute('SELECT sha FROM books WHERE sha=?', (sha,)).fetchone()
            if existing and destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == sha:
                return self.get(sha), False
            with EpubBook(temporary) as book:
                book.validate()
                metadata = (sha, book.title, json.dumps(book.authors, ensure_ascii=False),
                            normalized(book.title+' '+' '.join(book.authors)), book.language,
                            json.dumps(book.hrefs), now())
                standard_spines = book.standard_spines
                cover = book.cover()
            # A failed DB commit keeps this verified content for a later retry.
            os.replace(temporary, destination)
            directory = os.open(self.books, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            cover_name = None
            if cover:
                raw, extension = cover
                cover_name = sha+extension
                with tempfile.NamedTemporaryFile(dir=self.books, prefix='.cover-', delete=False) as file:
                    cover_temp = Path(file.name)
                    try:
                        file.write(raw)
                        file.flush()
                        os.fsync(file.fileno())
                    except Exception:
                        cover_temp.unlink(missing_ok=True)
                        raise
                try:
                    os.replace(cover_temp, self.books/cover_name)
                finally:
                    cover_temp.unlink(missing_ok=True)
            with self.connect() as connection:
                connection.execute('DELETE FROM pending_deletions WHERE name IN (?,?)', (sha+'.epub', cover_name))
                cursor = connection.execute('INSERT OR IGNORE INTO books (sha,title,authors,search_text,language,spine_hrefs,created,cover) VALUES (?,?,?,?,?,?,?,?)',
                                            metadata+(cover_name,))
                added = cursor.rowcount == 1
            self.mark_standard_style(sha, standard_spines)
            return self.get(sha), added
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def mark_standard_style(self, sha, spines):
        with self.connect() as connection:
            row = connection.execute('SELECT standard_style FROM books WHERE sha=?', (sha,)).fetchone()
            if row:
                current = set(json.loads(row[0]))
                combined = current | set(spines)
                if current != combined:
                    connection.execute('UPDATE books SET standard_style=? WHERE sha=?', (json.dumps(sorted(combined)),sha))

    def validate_locator(self, sha, locator):
        row = self.get(sha)
        hrefs = json.loads(row['spine_hrefs'])
        index = locator.get('spine_index')
        if locator.get('sha256') != sha or locator.get('version') != 1:
            raise ValueError('書籍と読書位置が一致しません。')
        if type(index) is not int or not 0 <= index < len(hrefs) or locator.get('href') != hrefs[index]:
            raise ValueError('読書位置の参照先が不正です。')
        progression = locator.get('progression', 0) or 0
        if not isinstance(progression, (int, float)) or not 0 <= progression <= 1:
            raise ValueError('読書位置の進捗が不正です。')
        return row, (index+progression)/len(hrefs)

    def save_position(self, sha, locator, finished=False):
        _, progress = self.validate_locator(sha, locator)
        with self.connect() as connection:
            connection.execute('UPDATE books SET position=?,progress=?,status=?,last_read=? WHERE sha=?',
                               (json.dumps(locator), 1.0 if finished else progress,
                                'finished' if finished else 'unread' if progress == 0 else 'reading', now(), sha))

    def save_options(self, sha, options):
        with self.connect() as connection:
            connection.execute('UPDATE books SET options=? WHERE sha=?', (json.dumps(options), sha))

    def bookmarks(self, sha):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute('SELECT * FROM bookmarks WHERE sha=? ORDER BY created', (sha,))]

    def toggle_bookmark(self, sha, locator):
        self.validate_locator(sha, locator)
        key = locator.get('cfi') or json.dumps([locator['spine_index'], locator.get('kind'), locator.get('objectIndex'), locator.get('fragment')])
        with self.connect() as connection:
            row = connection.execute('SELECT id FROM bookmarks WHERE sha=? AND location_key=?', (sha, key)).fetchone()
            if row:
                connection.execute('DELETE FROM bookmarks WHERE id=?', (row[0],))
                return False
            label = f'区間 {locator["spine_index"] + 1}・{(locator.get("progression") or 0):.0%}'
            connection.execute('INSERT INTO bookmarks VALUES (?,?,?,?,?,?)',
                               (str(uuid.uuid4()), sha, key, json.dumps(locator), label, now()))
        return True
