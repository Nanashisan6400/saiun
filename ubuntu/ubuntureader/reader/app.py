"""First offline-library application for Surface / GNOME."""
from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Adw, Gtk, Gdk, Gio, GLib, GdkPixbuf, Pango

from .library import Library
from .branding import display_name
from .contents import current_entry
from .simple_cover import simple_cover
from .bundled_fonts import register_native_fonts
from .cache_budget import cache_budget_mb, MIN_CACHE_MB, MAX_CACHE_MB
from .seamless_view import SeamlessView as ReadingView

THEMES = ['system', 'light', 'dark']
SCHEMES = [Adw.ColorScheme.DEFAULT, Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK]
STATUSES = ['all', 'unread', 'reading', 'finished']
SORTS = ['recent', 'added', 'title', 'author']
LABELS = {'unread': '未読', 'reading': '読書中', 'finished': '読了'}


class ReaderApplication(Adw.Application):
    def __init__(self, data_dir=None):
        register_native_fonts()
        GLib.set_application_name(display_name())
        application_id = 'local.ebookproject.Reader'
        if data_dir:
            import hashlib
            application_id += '.T'+hashlib.sha256(str(Path(data_dir).resolve()).encode()).hexdigest()[:12]
        super().__init__(application_id=application_id, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.library = Library(data_dir)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.reader = None
        self.window = None
        self.dialog = None
        self.pending_imports = 0
        self.selected_books = set()
        self.delete_mode = False
        self.book_buttons = {}
        self.removing_books = False
        self.current_toast = None
        self.controls_timer = 0
        self.reader_popovers = []
        self.connect('activate', self.activate)
        self.connect('open', self.open_files)
        self.connect('shutdown', self.shutdown)
        self.style = Adw.StyleManager.get_default()
        self.style.connect('notify::dark', self.dark_changed)
        self.set_theme(self.library.setting('theme', 'system'))

    def activate(self, application):
        if self.window:
            self.window.present()
            return
        self.window = Adw.ApplicationWindow(application=self, title=display_name())
        self.window.set_default_size(960, 640)
        self.window.connect('close-request', self.closing)
        self.window.connect('notify::fullscreened', self.fullscreen_changed)
        self.overlay = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.shelf_header = header
        self.heading = Adw.WindowTitle(title='本棚', subtitle='端末内のライブラリ')
        header.set_title_widget(self.heading)
        self.import_button = Gtk.Button(label='取り込む')
        self.import_button.connect('clicked', self.choose_files)
        header.pack_start(self.import_button)
        settings = Gtk.Button(label='設定')
        settings.connect('clicked', self.preferences)
        header.pack_end(settings)
        toolbar.add_top_bar(header)
        self.pages = Gtk.Stack()
        toolbar.set_content(self.pages)
        self.overlay.set_child(toolbar)
        self.window.set_content(self.overlay)
        self.controls_css = Gtk.CssProvider()
        self.controls_css.load_from_string('''.reader-controls { background-color: alpha(@window_bg_color, 0.97); } .standard-style { background-color: alpha(#e5a50a, 0.18); border: 2px solid #b58300; } .delete-selected { outline: 3px solid @accent_color; outline-offset: -3px; background-color: alpha(@accent_color, 0.20); }
            .reader-toast-message { font-family: "IPAexGothic", sans-serif; }
        ''')
        Gtk.StyleContext.add_provider_for_display(self.window.get_display(), self.controls_css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        activity = Gtk.GestureClick()
        activity.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        activity.connect('pressed',self.dismiss_reading_settings)
        activity.connect('released', lambda *_: self.reset_controls_timer())
        self.window.add_controller(activity)
        self.build_shelf()
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect('drop', self.drop)
        self.window.add_controller(drop)
        key = Gtk.EventControllerKey()
        key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key.connect('key-pressed', self.key)
        self.window.add_controller(key)
        self.window.present()
        self.refresh_shelf()

    def build_shelf(self):
        shelf = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        shelf.set_margin_start(12)
        shelf.set_margin_end(12)
        shelf.set_margin_bottom(12)
        self.search = Gtk.SearchEntry(placeholder_text='タイトル・著者を検索')
        self.search.connect('search-changed', lambda *_: self.refresh_shelf())
        shelf.append(self.search)
        bar = Gtk.Box(spacing=8)
        self.filter = Gtk.DropDown.new_from_strings(['すべて', '未読', '読書中', '読了'])
        self.sort = Gtk.DropDown.new_from_strings(['最近読んだ本', '追加した順', 'タイトル順', '著者順'])
        self.mode = Gtk.DropDown.new_from_strings(['表紙', '一覧'])
        self.mode.set_selected(self.library.setting('shelf_mode', 0))
        for selector in [self.filter, self.sort, self.mode]:
            selector.connect('notify::selected', lambda *_: self.refresh_shelf())
            bar.append(selector)
        bar.append(Gtk.Box(hexpand=True))
        self.delete_button = Gtk.Button(label='削除')
        self.delete_button.add_css_class('destructive-action')
        self.delete_button.connect('clicked', lambda *_: self.toggle_delete_mode())
        bar.append(self.delete_button)
        self.execute_delete_button = Gtk.Button(label='実行')
        self.execute_delete_button.add_css_class('destructive-action')
        self.execute_delete_button.connect('clicked', lambda *_: self.confirm_remove())
        bar.append(self.execute_delete_button)
        shelf.append(bar)
        self.shelf_status = Gtk.Label(xalign=0)
        shelf.append(self.shelf_status)
        self.shelf_scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.shelf_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        shelf.append(self.shelf_scroll)
        self.pages.add_named(shelf, 'shelf')

    def refresh_shelf(self):
        if not self.window:
            return
        mode = self.mode.get_selected()
        self.library.set_setting('shelf_mode', mode)
        rows = self.library.listing(self.search.get_text(), STATUSES[self.filter.get_selected()], SORTS[self.sort.get_selected()])
        self.selected_books.intersection_update(row['sha'] for row in rows)
        self.update_delete_button()
        self.book_buttons = {}
        self.shelf_status.set_label(f'{len(rows)}冊' + ('　取り込み中…' if self.pending_imports else ''))
        if not rows:
            filtered = bool(self.search.get_text()) or self.filter.get_selected() != 0
            empty = Adw.StatusPage(title='条件に合う本がありません' if filtered else '本を取り込む',
                                   description='検索条件や分類を変えてください。' if filtered else '「取り込む」またはファイルのドラッグでEPUBを追加できます。',
                                   icon_name='x-office-document-symbolic')
            self.shelf_scroll.set_child(empty)
            return
        if mode == 0:
            container = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=12, row_spacing=12)
            container.set_max_children_per_line(6)
            container.set_min_children_per_line(1)
        else:
            container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for row in rows:
            button = Gtk.Button()
            button.add_css_class('card')
            standard = bool(json.loads(row.get('standard_style', '[]')))
            if standard:
                button.add_css_class('standard-style')
            if row['sha'] in self.selected_books:
                button.add_css_class('delete-selected')
            button.set_sensitive(not self.removing_books)
            button.set_tooltip_text(row['title']+('を選択・解除' if self.delete_mode else 'を開く'))
            self.book_buttons[row['sha']] = button
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if mode == 0 else Gtk.Orientation.HORIZONTAL, spacing=8)
            content.set_margin_top(8)
            content.set_margin_bottom(8)
            content.set_margin_start(8)
            content.set_margin_end(8)
            content.append(self.cover_widget(row, mode))
            details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            details.set_hexpand(True)
            title = Gtk.Label(label=row['title'], wrap=True, max_width_chars=24, xalign=0)
            title.set_lines(2)
            title.set_ellipsize(Pango.EllipsizeMode.END)
            title.add_css_class('heading')
            details.append(title)
            authors = Gtk.Label(label='・'.join(json.loads(row['authors'])) or '著者情報なし', wrap=True, max_width_chars=24, xalign=0)
            authors.set_lines(2)
            authors.set_ellipsize(Pango.EllipsizeMode.END)
            details.append(authors)
            status = Gtk.Label(label=f'{LABELS[row["status"]]}　{row["progress"]:.0%}', xalign=0)
            status.add_css_class('dim-label')
            details.append(status)
            local = Gtk.Label(label='端末に保存済み・端末のみ'+(' ／ 標準書式' if standard else ''), wrap=True, xalign=0)
            local.add_css_class('caption')
            details.append(local)
            content.append(details)
            button.set_child(content)
            button.connect('clicked', lambda widget, sha=row['sha']: self.shelf_book_clicked(sha))
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            button.set_hexpand(True)
            card.append(button)
            container.append(card)
        self.shelf_scroll.set_child(container)

    def toggle_delete_mode(self):
        if self.pending_imports or self.removing_books:
            return
        self.delete_mode = not self.delete_mode
        self.selected_books.clear()
        self.refresh_shelf()

    def shelf_book_clicked(self, sha):
        if not self.delete_mode:
            self.open_book(sha)
            return
        if self.removing_books:
            return
        if sha in self.selected_books:
            self.selected_books.remove(sha)
            self.book_buttons[sha].remove_css_class('delete-selected')
        else:
            self.selected_books.add(sha)
            self.book_buttons[sha].add_css_class('delete-selected')
        self.update_delete_button()

    def update_delete_button(self):
        count = len(self.selected_books)
        available = not self.pending_imports and not self.removing_books
        self.delete_button.set_label('キャンセル' if self.delete_mode else '削除')
        self.delete_button.set_sensitive(available)
        self.execute_delete_button.set_visible(self.delete_mode)
        self.execute_delete_button.set_label(f'実行（{count}）' if count else '実行')
        self.execute_delete_button.set_sensitive(available and bool(count))

    def confirm_remove(self, sha=None):
        if self.removing_books:
            return
        if self.pending_imports:
            self.toast('取り込み完了後に削除してください。')
            return
        shas = [sha] if sha else sorted(self.selected_books)
        if not shas:
            return
        rows = [self.library.get(key) for key in shas]
        titles = '\n'.join(row['title'] for row in rows[:5])
        if len(rows) > 5:
            titles += f'\nほか{len(rows)-5}冊'
        dialog = Adw.MessageDialog(transient_for=self.window, modal=True,
                                  heading=f'{len(rows)}冊を本棚から削除しますか？',
                                  body=titles+'\n\nアプリ内の本体・しおり・読書位置・書籍設定を削除します。元のファイルやフォルダは残ります。')
        dialog.add_response('cancel', 'キャンセル')
        dialog.add_response('delete', '削除')
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        def responded(dialog, response):
            self.dialog = None
            if response != 'delete':
                return
            if self.reader and self.reader.sha in shas:
                self.back_to_shelf()
            self.removing_books = True
            self.refresh_shelf()
            future = self.executor.submit(self.library.remove_books, shas)
            def done(future):
                try:
                    future.result()
                    message = '本棚から削除しました。'
                except Exception:
                    logging.exception('Removal failed')
                    message = '削除できませんでした。保存先を確認してください。'
                def finished():
                    self.removing_books = False
                    self.delete_mode = False
                    self.selected_books.clear()
                    self.refresh_shelf()
                    self.toast(message)
                    return False
                GLib.idle_add(finished)
            future.add_done_callback(done)
        dialog.connect('response', responded)
        self.dialog = dialog
        dialog.present()

    def cover_widget(self, row, mode):
        width, height = (132, 176) if mode == 0 else (72, 96)
        cover = self.library.books/row['cover'] if row['cover'] else None
        if cover and cover.is_file():
            try:
                if cover.suffix == '.svg':
                    gi.require_version('Rsvg', '2.0')
                    from gi.repository import Rsvg
                    handle = Rsvg.Handle.new_from_data(cover.read_bytes())
                    # Size callback keeps native rasterization at thumbnail dimensions.
                    handle.set_size_callback(lambda *_: (width, height), None)
                    pixbuf = handle.get_pixbuf()
                else:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(cover), width, height, True)
                image = Gtk.Picture.new_for_pixbuf(pixbuf)
                image.set_alternative_text('表紙')
                image.set_content_fit(Gtk.ContentFit.CONTAIN)
                image.set_size_request(width, height)
                return image
            except (GLib.Error, ImportError, ValueError):
                logging.exception('Cover could not be decoded')
        authors = '・'.join(json.loads(row['authors'])) or '著者情報なし'
        return simple_cover(row['title'], authors, width, height)

    def open_files(self, application, files, count, hint):
        self.activate(application)
        for file in files:
            path = file.get_path()
            if path:
                self.import_path(path)
            else:
                self.toast('端末に保存されたEPUBを選んでください。')

    def choose_files(self, button):
        # FileChooserDialog's OPEN response navigates into folders. Embed the
        # chooser so our accept action can import files and directories alike.
        dialog = Gtk.Dialog(title='EPUBを取り込む', transient_for=self.window, modal=True)
        dialog.set_default_size(800, 560)
        chooser = Gtk.FileChooserWidget(action=Gtk.FileChooserAction.OPEN, select_multiple=True,
                                        hexpand=True, vexpand=True)
        epub = Gtk.FileFilter(name='EPUB')
        epub.add_pattern('*.epub')
        epub.add_pattern('*.zip')
        epub.add_mime_type('application/epub+zip')
        chooser.add_filter(epub)
        dialog.get_content_area().append(chooser)
        hint = Gtk.Label(label='EPUBファイル・展開済みフォルダを選択（複数選択可）',
                         wrap=True, margin_top=6, margin_bottom=6)
        dialog.get_content_area().append(hint)
        dialog.add_buttons('キャンセル', Gtk.ResponseType.CANCEL, '取り込む', Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        self.import_dialog = dialog
        self.import_chooser = chooser
        def selected(dialog, response):
            if response != Gtk.ResponseType.ACCEPT:
                self.import_dialog = None
                self.import_chooser = None
                dialog.destroy()
                return
            files = chooser.get_files()
            paths = [files.get_item(i).get_path() for i in range(files.get_n_items())]
            if not paths:
                hint.set_label('取り込むEPUBファイルまたはフォルダを選択してください。')
                return
            if any(path is None for path in paths):
                hint.set_label('端末に保存されたEPUBを選択してください。')
                return
            # An ordinary folder is for navigation; a publication is imported.
            if len(paths) == 1 and Path(paths[0]).is_dir() and not (Path(paths[0])/'META-INF/container.xml').is_file():
                chooser.set_current_folder(Gio.File.new_for_path(paths[0]))
                return
            self.import_dialog = None
            self.import_chooser = None
            dialog.destroy()
            for path in paths:
                self.import_path(path)
        dialog.connect('response', selected)
        dialog.present()

    def drop(self, target, files, x, y):
        accepted = False
        for file in files.get_files():
            path = file.get_path()
            if path and (Path(path).suffix.lower() in ('.epub', '.zip') or Path(path).is_dir()):
                self.import_path(path)
                accepted = True
        return accepted

    def import_path(self, path):
        self.pending_imports += 1
        self.refresh_shelf()
        future = self.executor.submit(self.library.import_book, path)
        def done(future):
            try:
                row, added = future.result()
                GLib.idle_add(self.imported, added, None)
            except Exception as error:
                logging.exception('Import failed')
                message = str(error) if isinstance(error, ValueError) and not str(error).isascii() else '取り込めませんでした。EPUBの形式と保存先の空き容量を確認してください。'
                GLib.idle_add(self.imported, False, message)
        future.add_done_callback(done)

    def imported(self, added, error):
        self.pending_imports -= 1
        if self.window:
            self.refresh_shelf()
            self.toast(error or ('本棚に追加しました。' if added else 'この本は登録済みです。'))
        return False

    def open_book(self, sha):
        try:
            row = self.library.get(sha)
            reader = ReadingView(self.library, row, self.style.get_dark(), self.reader_status, self.reader_error, self.toast)
        except Exception:
            logging.exception('Book could not be opened')
            self.toast('本を開けませんでした。保存内容や対応形式を確認してください。')
            return
        self.back_to_shelf()
        self.reader = reader
        reader.on_controls = self.toggle_controls
        reader.on_link = self.open_reader_link
        pane = Gtk.Overlay()
        pane.set_child(reader)
        self.reader_bar = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER, valign=Gtk.Align.START,
                                  margin_top=8, margin_start=8, margin_end=8)
        self.reader_bar.add_css_class('toolbar')
        self.reader_bar.add_css_class('card')
        self.reader_bar.add_css_class('reader-controls')
        self.reader_bar.append(self.icon_button('go-previous-symbolic', '本棚へ戻る', self.back_to_shelf))
        self.link_back_button = self.icon_button('edit-undo-symbolic', 'リンク元へ戻る', reader.back_link)
        self.link_back_button.set_visible(False)
        self.reader_bar.append(self.link_back_button)
        self.reader_bar.append(self.icon_button('view-list-symbolic', '目次', self.contents))
        marks = Gtk.MenuButton(icon_name='user-bookmarks-symbolic', tooltip_text='しおり')
        marks.update_property([Gtk.AccessibleProperty.LABEL], ['しおり'])
        mark_popover = Gtk.Popover()
        mark_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for label, action in [('現在位置のしおりを追加／削除', self.bookmark), ('しおり一覧', self.bookmarks)]:
            button = Gtk.Button(label=label)
            button.connect('clicked', lambda _, action=action: (mark_popover.popdown(), action()))
            mark_box.append(button)
        mark_popover.set_child(mark_box)
        marks.set_popover(mark_popover)
        self.reader_bar.append(marks)
        settings = Gtk.Button(icon_name='font-select-symbolic', tooltip_text='読書設定')
        self.reading_settings_button = settings
        settings.update_property([Gtk.AccessibleProperty.LABEL], ['読書設定'])
        popover = Gtk.Popover(autohide=False)
        popover.set_parent(settings)
        self.settings_popover = popover
        settings.connect('clicked',lambda _:popover.popdown() if popover.get_visible() else popover.popup())
        options = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sizes = Gtk.Box(spacing=8, homogeneous=True)
        for label, amount in [('文字−', -2), ('文字＋', 2)]:
            button = Gtk.Button(label=label)
            button.connect('clicked', lambda _, amount=amount: reader.enqueue('size', amount))
            sizes.append(button)
        self.spread_choice = Gtk.DropDown.new_from_strings(['単ページ', '見開き', '自動（横長の画面）'])
        self.spread_choice.set_selected(['single','double','auto'].index(reader.options['spread']))
        self.spread_choice.set_tooltip_text('ページの表示方式')
        self.spread_choice.update_property([Gtk.AccessibleProperty.LABEL], ['ページの表示方式'])
        def change_setting(kind,value):
            def apply():
                if self.reader == reader and not reader.closed:
                    popover.popdown()
                    reader.enqueue(kind,value)
                return False
            # Finish the nested dropdown's selection before closing and reflowing.
            GLib.idle_add(apply)
        self.spread_choice.connect('notify::selected', lambda choice, _: change_setting('spread',['single','double','auto'][choice.get_selected()]))
        display_row = Gtk.Box(spacing=12)
        self.spread_choice.set_hexpand(True)
        display_row.append(self.spread_choice)
        options.append(display_row)
        spread_note = Gtk.Label(label='狭い画面と固定ページは単ページで表示します。', wrap=True, max_width_chars=48)
        spread_note.add_css_class('caption')
        options.append(spread_note)
        self.text_controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
        text_pages=Gtk.Stack()
        text_tabs=Gtk.StackSwitcher(stack=text_pages,halign=Gtk.Align.CENTER)
        self.text_controls.append(text_tabs)
        self.text_controls.append(text_pages)
        basic=Gtk.Box(spacing=16)
        text_pages.add_titled(basic,'basic','文字')
        appearance = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        typography = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        basic.append(appearance)
        basic.append(typography)
        typography.append(sizes)
        options.append(self.text_controls)
        self.font_choice = Gtk.DropDown.new_from_strings(['明朝体', 'ゴシック体', '書籍の書体']+reader.installed_fonts)
        self.font_choice.set_enable_search(True)
        font_factory=Gtk.SignalListItemFactory()
        font_factory.connect('setup',lambda factory,item:item.set_child(Gtk.Label(xalign=0,ellipsize=Pango.EllipsizeMode.END,max_width_chars=18)))
        font_factory.connect('bind',lambda factory,item:(item.get_child().set_label(item.get_item().get_string()),item.get_child().set_tooltip_text(item.get_item().get_string())))
        self.font_choice.set_factory(font_factory)
        self.font_choice.set_selected(reader.font_values.index(reader.options['font']))
        self.font_choice.set_tooltip_text('本文の書体')
        self.font_choice.update_property([Gtk.AccessibleProperty.LABEL], ['本文の書体'])
        self.font_choice.connect('notify::selected', lambda choice, _: reader.enqueue('font', reader.font_values[choice.get_selected()]))
        appearance.append(self.font_choice)
        self.typography_controls = {}
        self.syncing_text_settings = False
        for kind, label, lower, upper, step, digits in [
                ('line','行間（倍率）',1,3,.1,1),
                ('spacing','文字間（px）',-1,8,.25,2),
                ('margin','余白（px）',4,80,4,0)]:
            setting_row = Gtk.Box(spacing=8)
            setting_row.append(Gtk.Label(label=label,hexpand=True,xalign=0))
            spin = Gtk.SpinButton.new_with_range(lower,upper,step)
            spin.set_digits(digits)
            spin.set_value(reader.options[kind])
            spin.update_property([Gtk.AccessibleProperty.LABEL],[label])
            spin.connect('value-changed',lambda spin,kind=kind:None if self.syncing_text_settings else reader.enqueue(kind,spin.get_value()))
            setting_row.append(spin)
            self.typography_controls[kind] = spin
            typography.append(setting_row)
        direction = Gtk.Button(label='縦書き／横書き')
        direction.connect('clicked', lambda _: (popover.popdown(), reader.enqueue('direction')))
        appearance.append(direction)
        reset = Gtk.Button(label='文字設定を初期値に戻す')
        self.reset_text_button = reset
        reset.set_tooltip_text('文字18px・行間1.8倍・余白24px・文字間0px')
        def reset_text(unused):
            self.syncing_text_settings = True
            try:
                for kind,value in [('line',1.8),('spacing',0),('margin',24)]:
                    self.typography_controls[kind].set_value(value)
            finally:
                self.syncing_text_settings = False
            reader.enqueue('reset_text')
        reset.connect('clicked',reset_text)
        typography.append(reset)
        paragraphs=Gtk.Grid(column_spacing=16,row_spacing=12)
        text_pages.add_titled(paragraphs,'paragraphs','段落・ルビ')
        self.detail_choices={}
        for row_index,(kind,label,labels,values) in enumerate([
                ('paragraphGap','段落間隔',['原文どおり','なし','0.5字','1字','1.5字','2字','3字'],['original',0,.5,1,1.5,2,3]),
                ('indent','字下げ',['原文どおり','なし','1字','2字'],['original',0,1,2]),
                ('align','文字揃え',['原文どおり','行頭揃え','両端揃え'],['original','start','justify'])]):
            paragraphs.attach(Gtk.Label(label=label,xalign=0,hexpand=True),0,row_index,1,1)
            choice=Gtk.DropDown.new_from_strings(labels)
            choice.set_selected(values.index(reader.options[kind]))
            choice.update_property([Gtk.AccessibleProperty.LABEL],[label])
            choice.connect('notify::selected',lambda choice,unused,kind=kind,values=values:reader.enqueue(kind,values[choice.get_selected()]))
            paragraphs.attach(choice,1,row_index,1,1)
            self.detail_choices[kind]=choice
        self.ruby_switch=Gtk.Switch(active=reader.options['ruby'],halign=Gtk.Align.END,valign=Gtk.Align.CENTER)
        self.ruby_switch.connect('notify::active',lambda switch,unused:reader.enqueue('ruby',switch.get_active()))
        paragraphs.attach(Gtk.Label(label='ルビを表示',xalign=0),0,3,1,1)
        paragraphs.attach(self.ruby_switch,1,3,1,1)
        self.fixed_controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        note = Gtk.Label(label='固定ページは単ページで表示します。\n拡大中はドラッグで移動できます。', wrap=True)
        note.add_css_class('caption')
        self.fixed_controls.append(note)
        for label, action in [('拡大', lambda: reader.enqueue('zoom', 1.5)),
                              ('縮小', lambda: reader.enqueue('zoom', 1/1.5)),
                              ('全体表示', lambda: reader.enqueue('fit'))]:
            button = Gtk.Button(label=label)
            button.connect('clicked', lambda _, action=action: action())
            self.fixed_controls.append(button)
        options.append(self.fixed_controls)
        self.fixed_controls.set_visible(False)
        options.set_size_request(480, -1)
        popover.set_child(options)
        self.reader_popovers = [mark_popover, popover]
        for menu in self.reader_popovers:
            menu.connect('closed', lambda *_: self.reset_controls_timer())
        self.reader_bar.append(settings)
        self.reader_bar.append(self.icon_button('view-fullscreen-symbolic', '全画面を切り替える', self.fullscreen))
        self.reader_bar.append(self.icon_button('window-close-symbolic', '操作バーを隠す（中央タップで表示）', lambda: self.set_controls(False)))
        pane.add_overlay(self.reader_bar)
        self.reader_footer = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, valign=Gtk.Align.END, margin_bottom=8)
        self.reader_footer.add_css_class('toolbar')
        self.reader_footer.add_css_class('card')
        self.reader_footer.add_css_class('reader-controls')
        self.reader_footer.append(self.icon_button('go-previous-symbolic', '前のページ', lambda: reader.enqueue('move', -1)))
        self.reading_status = Gtk.Label(label='読み込み中…', wrap=True)
        self.reading_status.add_css_class('caption')
        self.reader_footer.append(self.reading_status)
        self.reader_footer.append(self.icon_button('go-next-symbolic', '次のページ', lambda: reader.enqueue('move', 1)))
        pane.add_overlay(self.reader_footer)
        self.shelf_header.set_visible(False)
        self.set_controls(not self.window.is_fullscreen())
        self.pages.add_named(pane, 'reading')
        self.pages.set_visible_child_name('reading')
        self.heading.set_title(row['title'])
        self.heading.set_subtitle('・'.join(json.loads(row['authors'])))
        reader.start()

    def reader_status(self, reader):
        value = reader.current
        self.link_back_button.set_visible(bool(reader.link_history))
        fixed = reader.is_fixed()
        self.text_controls.set_sensitive(not fixed)
        self.fixed_controls.set_visible(fixed)
        if fixed:
            self.text_controls.set_visible(False)
            self.reading_status.set_label(f'固定ページ {reader.spine + 1}/{len(reader.book.spine)}　{reader.options["fixedZoom"]:.0%}')
            return
        self.text_controls.set_visible(True)
        page = str(value['page']+1)
        if len(value['visiblePages']) == 2:
            page += '–'+str(value['visiblePages'][-1]+1)
        mode = '見開き' if reader.spread_active else '単ページ'
        self.reading_status.set_label(f'本文 {page}ページ　{mode}')

    def reader_error(self, message):
        self.reading_status.set_label('表示できませんでした。本棚へ戻って再度開いてください。')
        self.toast(message)

    def open_reader_link(self, target):
        if target['kind'] == 'external':
            dialog = Adw.MessageDialog(transient_for=self.window, modal=True,
                                      heading='ブラウザーで開きますか？', body=target['url'])
            dialog.add_response('cancel', 'キャンセル')
            dialog.add_response('open', 'ブラウザーで開く')
            dialog.set_default_response('cancel')
            dialog.set_close_response('cancel')
            def responded(dialog, response):
                self.dialog = None
                self.reset_controls_timer()
                if response == 'open':
                    try:
                        Gio.AppInfo.launch_default_for_uri(target['url'], None)
                    except GLib.Error:
                        self.toast('ブラウザーを開けませんでした。')
            dialog.connect('response', responded)
            self.dialog = dialog
            dialog.present()
            return
        dialog = Adw.Dialog(title='脚注・リンク先', content_width=480, content_height=400)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        text = target['text']
        if target.get('truncated'):
            text += '\n\n内容が長いため先頭部分を表示しています。'
        label = Gtk.Label(label=text, wrap=True, selectable=True, xalign=0, yalign=0,
                          margin_top=12, margin_bottom=12, margin_start=16, margin_end=16)
        label.set_use_markup(False)
        scroll.set_child(label)
        toolbar.set_content(scroll)
        return_button = Gtk.Button(label='本文に戻る', margin_top=8, margin_bottom=8,
                                   margin_start=12, margin_end=12)
        return_button.connect('clicked', lambda *_: dialog.close())
        toolbar.add_bottom_bar(return_button)
        dialog.set_child(toolbar)
        self.dialog = dialog
        dialog.connect('closed', self.dialog_closed)
        dialog.present(self.window)

    def dismiss_reading_settings(self, gesture, count, x, y):
        if not self.reader or not self.settings_popover.get_visible():
            return
        for widget in (self.reading_settings_button,self.settings_popover):
            valid,bounds = widget.compute_bounds(self.window)
            if valid and bounds.origin.x <= x <= bounds.origin.x+bounds.size.width and bounds.origin.y <= y <= bounds.origin.y+bounds.size.height:
                return
        self.settings_popover.popdown()

    def back_to_shelf(self):
        if getattr(self,'settings_popover',None):
            self.settings_popover.popdown()
            self.settings_popover.unparent()
            self.settings_popover = None
        self.cancel_controls_timer()
        self.reader_popovers = []
        if self.reader:
            self.reader.close()
            self.reader = None
            child = self.pages.get_child_by_name('reading')
            if child:
                self.pages.remove(child)
        self.shelf_header.set_visible(True)
        self.pages.set_visible_child_name('shelf')
        self.heading.set_title('本棚')
        self.heading.set_subtitle('端末内のライブラリ')
        self.refresh_shelf()

    def preferences(self, button):
        dialog = Adw.PreferencesDialog(title='アプリ設定')
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title='アプリと本文の配色')
        row = Adw.ComboRow(title='配色', model=Gtk.StringList.new(['OSに合わせる', 'ライトモード', 'ダークモード']))
        row.set_selected(THEMES.index(self.library.setting('theme', 'system')))
        row.connect('notify::selected', lambda row, unused: self.set_theme(THEMES[row.get_selected()]))
        group.add(row)
        page.add(group)
        memory=Adw.PreferencesGroup(title='読書用メモリ',description='読み込んだ本文のキャッシュに使う容量の目安です。古い部分を解放し、戻るときに再読み込みします。アプリ全体の使用量とは異なります。')
        self.cache_budget_control=Adw.SpinRow.new_with_range(MIN_CACHE_MB,MAX_CACHE_MB,32)
        self.cache_budget_control.set_title('読書キャッシュ（MB）')
        self.cache_budget_control.set_value(cache_budget_mb(self.library))
        self.cache_budget_control.connect('notify::value',lambda control,unused:self.library.set_setting('reader_cache_mb',int(control.get_value())))
        memory.add(self.cache_budget_control)
        page.add(memory)
        dialog.add(page)
        self.dialog = dialog
        dialog.connect('closed', self.dialog_closed)
        dialog.present(self.window)

    def set_theme(self, theme):
        if theme not in THEMES:
            theme = 'system'
        self.library.set_setting('theme', theme)
        self.style.set_color_scheme(SCHEMES[THEMES.index(theme)])

    def dark_changed(self, manager, unused):
        if self.reader:
            self.reader.set_theme(manager.get_dark())

    def list_dialog(self, title, entries, action, current=None):
        dialog = Adw.Dialog(title=title, content_width=480, content_height=480)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        scroll = Gtk.ScrolledWindow(vexpand=True)
        listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        current_row = None
        for index, (label, value) in enumerate(entries):
            row = Adw.ActionRow(title=GLib.markup_escape_text(label), activatable=True)
            row.connect('activated', lambda widget, value=value: (dialog.close(), action(value)))
            listing.append(row)
            if index == current:
                current_row = row
                row.add_css_class('accent')
        scroll.set_child(listing)
        toolbar.set_content(scroll)
        dialog.set_child(toolbar)
        self.dialog = dialog
        dialog.connect('closed', self.dialog_closed)
        if current_row is not None:
            attempts = 0
            def reveal(widget, clock):
                nonlocal attempts
                attempts += 1
                adjustment = scroll.get_vadjustment()
                if current_row.get_height() <= 0 or adjustment.get_page_size() <= 0:
                    return attempts < 60
                current_row.grab_focus()
                bounds = current_row.get_allocation()
                adjustment.set_value(max(adjustment.get_lower(), min(
                    bounds.y + bounds.height/2 - adjustment.get_page_size()/2,
                    adjustment.get_upper() - adjustment.get_page_size())))
                return False
            listing.add_tick_callback(reveal)
        dialog.present(self.window)

    def contents(self):
        if not self.reader or not self.reader.current:
            return
        reader = self.reader
        entries = []
        for chapter in reader.book.chapters:
            index = chapter['spine']
            locator = dict(version=1, sha256=reader.sha, spine_index=index, href=reader.book.hrefs[index],
                           cfi=None, fragment=chapter['fragment'], progression=0)
            entries.append(('　'*min(5,max(0,chapter.get('depth',1)-1))+chapter['label'], locator))
        self.list_dialog('目次', entries, lambda value: reader.enqueue('seek', value),
                         current=current_entry(reader.book, reader.anchor or reader.current.get('anchor')))

    def bookmark(self):
        if self.reader and self.reader.busy:
            self.toast('本文の表示が完了してから栞を追加してください。')
            return
        if self.reader and self.reader.anchor:
            added = self.library.toggle_bookmark(self.reader.sha, self.reader.anchor)
            self.toast('栞を追加しました。' if added else '栞を削除しました。')

    def bookmarks(self):
        if not self.reader:
            return
        reader = self.reader
        rows = self.library.bookmarks(reader.sha)
        if not rows:
            self.toast('栞はまだありません。')
            return
        self.list_dialog('栞一覧', [(row['label'], json.loads(row['locator'])) for row in rows],
                         lambda locator: reader.enqueue('seek', locator))

    def icon_button(self, icon, label, action):
        button = Gtk.Button(icon_name=icon, tooltip_text=label)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        button.connect('clicked', lambda _: action())
        return button

    def dialog_closed(self, *unused):
        self.dialog = None
        self.reset_controls_timer()

    def cancel_controls_timer(self):
        if self.controls_timer:
            GLib.source_remove(self.controls_timer)
            self.controls_timer = 0

    def reset_controls_timer(self):
        self.cancel_controls_timer()
        if self.reader and self.reader_bar.get_visible():
            self.controls_timer = GLib.timeout_add(5000, self.hide_idle_controls)

    def hide_idle_controls(self):
        self.controls_timer = 0
        if self.dialog or any(menu.get_visible() for menu in self.reader_popovers):
            self.reset_controls_timer()
        elif self.reader:
            self.set_controls(False)
        return False

    def set_controls(self, visible):
        self.reader_bar.set_visible(visible)
        self.reader_footer.set_visible(visible)
        self.reset_controls_timer()
        if not visible and self.reader:
            if self.current_toast:
                self.current_toast.dismiss()
            self.reader.web.grab_focus()

    def toggle_controls(self):
        if self.reader and not self.dialog:
            self.set_controls(not self.reader_bar.get_visible())

    def fullscreen_changed(self, window, unused):
        if self.reader:
            self.set_controls(not window.is_fullscreen())

    def fullscreen(self):
        if self.window.is_fullscreen():
            self.window.unfullscreen()
        else:
            self.window.fullscreen()

    def key(self, controller, key, code, modifiers):
        if key == Gdk.KEY_Escape and self.delete_mode and not self.reader and not self.dialog and not self.removing_books:
            self.toggle_delete_mode()
            return True
        self.reset_controls_timer()
        if not self.reader or self.dialog:
            return False
        if key == Gdk.KEY_Left and modifiers & Gdk.ModifierType.ALT_MASK and not modifiers & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK):
            focus = self.window.get_focus()
            if focus and (focus.get_ancestor(Gtk.Popover) or isinstance(focus, Gtk.Text)):
                return False
            self.reader.back_link()
            return True
        if modifiers & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        focus = self.window.get_focus()
        if focus and focus.get_ancestor(Gtk.Popover):
            return False
        # Dialogs and text entry keep their normal focus handling.
        if self.window.get_focus() and isinstance(self.window.get_focus(), Gtk.Text):
            return False
        if key == Gdk.KEY_F11:
            self.fullscreen()
            return True
        if key == Gdk.KEY_Escape:
            if self.reader_bar.get_visible():
                self.set_controls(False)
            elif self.window.is_fullscreen():
                self.window.unfullscreen()
            return True
        if key in (Gdk.KEY_space, Gdk.KEY_Menu):
            self.toggle_controls()
            return True
        if not self.reader.current:
            return False
        forward = Gdk.KEY_Left if self.reader.rtl() else Gdk.KEY_Right
        backward = Gdk.KEY_Right if self.reader.rtl() else Gdk.KEY_Left
        if key in (forward, Gdk.KEY_Page_Down):
            self.reader.enqueue('move', 1)
            return True
        if key in (backward, Gdk.KEY_Page_Up):
            self.reader.enqueue('move', -1)
            return True
        return False

    def toast(self, message):
        if self.current_toast:
            self.current_toast.dismiss()
        label = Gtk.Label(label=message, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                          justify=Gtk.Justification.CENTER)
        label.add_css_class('reader-toast-message')
        attributes = Pango.AttrList()
        attributes.insert(Pango.attr_language_new(Pango.Language.from_string('ja')))
        label.set_attributes(attributes)
        self.current_toast = Adw.Toast(title=message, custom_title=label, timeout=5)
        self.overlay.add_toast(self.current_toast)

    def closing(self, window):
        if getattr(self,'settings_popover',None):
            self.settings_popover.popdown()
            self.settings_popover.unparent()
            self.settings_popover = None
        self.cancel_controls_timer()
        if self.reader:
            self.reader.close()
            self.reader = None
        self.window = None
        return False

    def shutdown(self, application):
        self.executor.shutdown(wait=False, cancel_futures=True)
