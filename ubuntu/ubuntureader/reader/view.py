"""Serialized reader widget using the stage0 typography and pagination core."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('WebKit', '6.0')
from gi.repository import Gtk, WebKit, GLib, Gdk, Graphene

from .epub import EpubBook
from .bundled_fonts import register_web_fonts, font_css

CORE = Path(__file__).resolve().parent.parent/'stage0'


class PageLayout(Gtk.LayoutManager):
    """Allocate a page to its frame without propagating WebKit's last natural size."""
    def do_measure(self, widget, orientation, for_size):
        return 0, 0, -1, -1

    def do_allocate(self, widget, width, height, baseline):
        child = widget.get_first_child()
        if child:
            child.allocate(width, height, baseline, None)


class ReadingView(Gtk.Box):
    book_type = None
    def __init__(self, library, row, dark, on_status, on_error, on_notice=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        self.library = library
        self.sha = row['sha']
        self.base_uri = f'file:///reader-local/{self.sha}/'
        path = library.path(self.sha)
        import hashlib
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.sha:
            raise ValueError('保存したEPUBの検証に失敗しました。元ファイルを再取り込みしてください。')
        self.book = (self.book_type or EpubBook)(path)
        self.on_status = on_status
        self.on_error = on_error
        self.on_notice = on_notice or on_error
        self.on_controls = None
        self.on_link = None
        self.link_history = []
        self.pending_history = None
        self.dark = dark
        self.options = dict(direction='original', method='columns', size=18, font='serif',
                            spacing=0, line=1.8, margin=24, paragraphGap='original',indent='original',align='original',ruby=True, hasAuthorCss=True)
        saved_options = json.loads(row['options'])
        self.options.update({key: saved_options[key] for key in ['direction', 'size', 'font'] if key in saved_options})
        for key, lower, upper in [('line',1,3),('spacing',-1,8),('margin',4,80)]:
            value = saved_options.get(key)
            if type(value) in (int,float):
                self.options[key] = max(lower,min(upper,value))
        self.installed_fonts=sorted({family.get_name() for family in self.get_pango_context().list_families()},key=str.casefold)
        self.font_values=['serif','sans-serif','original']+self.installed_fonts
        if self.options['font'] not in self.font_values:self.options['font']='serif'
        choices={'paragraphGap':['original',0,.5,1,1.5,2,3], 'indent':['original',0,1,2], 'align':['original','start','justify'], 'ruby':[True,False]}
        for key,allowed in choices.items():
            if saved_options.get(key) in allowed:self.options[key]=saved_options[key]
        zoom = saved_options.get('fixedZoom', 1)
        self.options['fixedZoom'] = max(1, min(4, zoom)) if type(zoom) in (int, float) else 1
        self.options['spread'] = saved_options.get('spread', 'single') if saved_options.get('spread', 'single') in ('single','double','auto') else 'single'
        # Disabled: batching and inferred chapter breaks are not used by the reader.
        # Ignore saved values so older enabled settings cannot reactivate them.
        self.options.update(chapterMode='original',combineDocuments=False,preserveAuthorBreaks=True)
        self.generation = 0
        self.loading_generation = {}
        self.spread_active = False
        self.secondary_layout_needed = True
        self.reveal_layout = False
        self.secondary_blank = None
        self.layout_options = None
        self.injected = set()
        self.pan_x = self.pan_y = 0
        self.anchor = json.loads(row['position']) if row['position'] else None
        if self.anchor:
            library.validate_locator(self.sha, self.anchor)
        self.spine = self.anchor['spine_index'] if self.anchor else 0
        self.part = self.book.part_for(self.spine, self.anchor)
        self.loader = ThreadPoolExecutor(max_workers=1, thread_name_prefix='reader-next')
        self.prepared_key = self.prepared_future = None
        self.group_spines = [self.spine]
        self.flow_fallback = False
        self.actions = deque()
        self.busy = False
        self.ready = False
        self.closed = False
        self.current = None
        self.viewport = None
        self.resize_timer = 0
        self.pending_layout = None
        self.target = self.anchor
        self.last_page = False
        self.pending_seek = False
        self.save_navigation = self.anchor is None
        self.web = WebKit.WebView()
        self.secondary = WebKit.WebView()
        register_web_fonts(self.web.get_context())
        register_web_fonts(self.secondary.get_context())
        for web in (self.web, self.secondary):
            web.set_vexpand(True)
            web.set_hexpand(True)
            settings = web.get_settings()
            settings.set_enable_javascript_markup(False)
            settings.set_allow_file_access_from_file_urls(False)
            settings.set_allow_universal_access_from_file_urls(False)
            settings.set_enable_html5_local_storage(False)
            settings.set_enable_html5_database(False)
            settings.set_javascript_can_access_clipboard(False)
            web.connect('load-changed', self.loaded)
            web.connect('load-failed', self.load_failed)
            web.connect('decide-policy', self.policy)
            pane = Gtk.Box(hexpand=True, vexpand=True)
            pane.set_layout_manager(PageLayout())
            pane.append(web)
            self.append(pane)
            click = Gtk.GestureClick()
            click.set_touch_only(True)
            click.connect('pressed', self.pressed)
            click.connect('released', self.tap)
            web.add_controller(click)
            pointer = Gtk.EventControllerLegacy()
            pointer.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            pointer.connect('event', self.pointer_event)
            web.add_controller(pointer)
            swipe = Gtk.GestureSwipe()
            swipe.connect('swipe', self.swipe)
            web.add_controller(swipe)
            drag = Gtk.GestureDrag()
            drag.connect('drag-end', self.pan)
            web.add_controller(drag)
            scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES | Gtk.EventControllerScrollFlags.DISCRETE)
            scroll.connect('scroll', self.scroll)
            web.add_controller(scroll)
        self.primary_pane = self.web.get_parent()
        self.secondary_pane = self.secondary.get_parent()
        self.secondary_pane.set_visible(False)
        self.web.add_tick_callback(self.tick)
        self.update_background()

    def is_fixed(self):
        return self.book.layouts[self.spine] == 'fixed'

    def start(self):
        self.load()

    def policy(self, web, decision, kind):
        if kind in [WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION]:
            uri = decision.get_navigation_action().get_request().get_uri()
            reference = self.book.links.get(uri)
            if reference:
                decision.ignore()
                if decision.get_navigation_action().get_navigation_type() == WebKit.NavigationType.LINK_CLICKED:
                    self.follow_link(reference, web)
                return True
            if kind == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION or uri not in (self.base_uri, 'about:blank'):
                decision.ignore()
                return True
        return False

    def follow_link(self, reference, web=None):
        if not self.ready or self.busy or self.closed:
            return
        try:
            target = self.book.link_target(reference)
        except Exception:
            self.on_notice('リンク先を開けません。参照先や識別子を確認してください。')
            return
        if target['kind'] != 'internal':
            if self.on_link:
                self.on_link(target)
            return
        origin = dict(self.anchor)
        if web == self.secondary and self.current.get('secondary'):
            partner = self.current['secondary']
            origin = dict(partner['anchor'], version=1, sha256=self.sha,
                          spine_index=self.spine, href=self.book.hrefs[self.spine],
                          progression=partner['progression'], cfi=partner['anchor'].get('cfi'),
                          fragment=partner['anchor'].get('element'))
        locator = dict(version=1, sha256=self.sha, spine_index=target['spine'],
                       href=self.book.hrefs[target['spine']], fragment=target['fragment'],
                       cfi=None, progression=0)
        self.pending_history = ('push', origin)
        self.enqueue('seek', locator)

    def back_link(self):
        if not self.link_history or self.busy or self.closed:
            return
        self.pending_history = ('pop', None)
        self.enqueue('seek', dict(self.link_history[-1]))

    def update_background(self):
        color = Gdk.RGBA()
        color.parse('#202124' if self.dark else '#faf9f6')
        for web in (self.web, self.secondary):
            web.set_background_color(color)

    def set_theme(self, dark):
        self.dark = dark
        self.update_background()
        if self.ready:
            value = str(dark).lower()
            for web in (self.web,self.secondary):
                self.evaluate_web(web, 'window.readerDark='+value+';if(window.reading)reading.theme(window.readerDark);JSON.stringify(true)', lambda _: None)

    def load(self):
        self.generation += 1
        self.busy = True
        self.ready = False
        self.options['spine'] = self.spine
        self.injected.clear()
        self.secondary_layout_needed = True
        self.secondary_blank = None
        generation = self.generation
        key = (self.spine, self.part)
        if key == self.prepared_key:
            future = self.prepared_future
            self.prepared_key = self.prepared_future = None
        else:
            if self.prepared_future:
                self.prepared_future.cancel()
            self.prepared_key = self.prepared_future = None
            future = self.loader.submit(self.prepare_section, *key)
        def complete(future):
            def activate():
                if self.closed or generation != self.generation:
                    return False
                try:
                    article, css, links, standard, reasons = future.result()
                    self.book.links = links
                    self.book.standard_spines.update(standard)
                    self.book.standard_reasons.update(reasons)
                    if standard:
                        self.library.mark_standard_style(self.sha, standard)
                    self.display_document(article, css)
                except Exception as error:
                    self.failed('本文を読み込めませんでした。'+str(error))
                return False
            GLib.idle_add(activate)
        future.add_done_callback(complete)

    def prepare_section(self, spine, part):
        # Each worker owns its ZIP/parser/token table. It never touches GTK or SQLite.
        with (self.book_type or EpubBook)(self.library.path(self.sha)) as book:
            article, css = book.document(spine, provenance=True, part=part)
            return article, css, book.links, book.standard_spines, book.standard_reasons

    def next_section(self, step=1):
        part = self.part+step
        if 0 <= part < self.book.part_count(self.spine):
            return self.spine, part
        spine = self.spine+step
        if 0 <= spine < len(self.book.spine):
            return spine, 0 if step > 0 else self.book.part_count(spine)-1
        return None

    def prefetch(self):
        key = self.next_section()
        if key is None or key == self.prepared_key:
            return
        if self.prepared_future:
            self.prepared_future.cancel()
        self.prepared_key = key
        self.prepared_future = self.loader.submit(self.prepare_section, *key)

    def display_document(self, article, css):
        self.group_spines = [self.spine]
        color = '#202124' if self.dark else '#faf9f6'
        css = font_css()+'\n'+css
        css += '\n#viewport{border:0!important}'
        css += '\n[data-stage0-cfi]{all:unset!important;display:contents!important}'
        css += '\nhtml:not([data-reader-ready]) body{visibility:hidden!important}'
        css += '\n#source img{max-width:100%;max-height:100%;object-fit:contain}'
        css += '\nhtml,body{margin:0;background:'+color+'}'
        if self.is_fixed():
            css += '\n.reader-fixed-page{position:relative;width:100%;height:100%;overflow:hidden;background:white;color:#111}'
        document = ('<!doctype html><html lang="ja"><head><meta charset="utf-8">'
                    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
                    'style-src \'unsafe-inline\'; img-src data:; font-src data: reader-font:; script-src \'none\'; connect-src \'none\'">'
                    '<style>'+css+'</style></head><body>'+article+'</body></html>')
        for web in (self.web,self.secondary):
            web.load_html(document, self.base_uri)

    def loaded(self, web, event):
        if event == WebKit.LoadEvent.STARTED:
            self.loading_generation[web] = self.generation
            return
        if self.loading_generation.get(web) != self.generation:
            return
        if event != WebKit.LoadEvent.FINISHED or self.closed:
            return
        scripts = '\n'.join((CORE/name).read_text() for name in ['typography.js', 'probe.js'])
        scripts += '\n'+Path(__file__).with_name('reading.js').read_text()
        def injected(unused):
            self.injected.add(web)
            if len(self.injected) == 2:
                self.ready = True
                self.layout(self.target, self.last_page)
        self.evaluate_web(web,scripts+';JSON.stringify(true)',injected)

    def load_failed(self, web, event, uri, error):
        if error.matches(WebKit.network_error_quark(),WebKit.NetworkError.CANCELLED):
            return True
        self.failed(str(error))
        return False

    def evaluate(self, script, callback):
        self.evaluate_web(self.web,script,callback)

    def evaluate_web(self, web, script, callback):
        generation = self.generation
        def done(web, result, *unused):
            if self.closed or generation != self.generation:
                return
            try:
                callback(json.loads(web.evaluate_javascript_finish(result).to_string()))
            except Exception as error:
                self.failed(str(error))
        web.evaluate_javascript(script, -1, None, None, None, done, None)

    def run(self, expression, reflow=False):
        # Only reflow needs the font/layout flash guard. Hiding entire WebKit
        # documents for an ordinary turn invalidates long chapters' paint data.
        self.reveal_layout = self.spread_active and reflow
        if self.reveal_layout:
            hidden = 'document.body.style.setProperty("visibility","hidden","important");document.documentElement.removeAttribute("data-reader-ready");'
            expression = '(async()=>{'+hidden+'return '+expression+'})()'
            expression = 'await '+expression
            self.evaluate_web(self.secondary,hidden+'JSON.stringify(true)',lambda _:None)
        self.run_web(self.web,expression,self.completed)

    def run_web(self, web, expression, callback):
        generation = self.generation
        script = ('window.readerTask=null;(async()=>{try{window.readerTask={value:('+expression+
                  ')};}catch(error){window.readerTask={error:String(error)};}})();JSON.stringify(true)')
        def received(result):
            if result is None:
                GLib.timeout_add(16,poll)
            elif 'error' in result:
                self.failed(result['error'])
            else:
                callback(result['value'])
        def poll():
            if not self.closed and generation == self.generation:
                self.evaluate_web(web,'JSON.stringify(window.readerTask)',received)
            return False
        self.evaluate_web(web,script,lambda _:GLib.timeout_add(16,poll))

    def update_spread(self):
        width, height = self.get_width(), self.get_height()
        requested = self.options['spread']
        active = not self.is_fixed() and width >= 640 and height >= 320 and (
            requested == 'double' or requested == 'auto' and width >= 800 and width > height)
        spacing = width % 2 if active else 0
        spacing_changed = spacing != self.get_spacing()
        self.set_spacing(spacing)
        if active == self.spread_active:
            return spacing_changed
        self.spread_active = active
        self.secondary_pane.set_visible(active)
        self.secondary_layout_needed = True
        return True

    def layout(self, target=None, last=False):
        if self.closed:
            return
        self.busy = True
        if self.update_spread():
            self.pending_layout = (target,last)
            if self.resize_timer:
                GLib.source_remove(self.resize_timer)
            self.resize_timer = GLib.timeout_add(250,self.resized)
            return
        if self.web.get_width() <= 2*self.options['margin']+16 or self.web.get_height() <= 2*self.options['margin']+32:
            self.pending_layout = (target, last)
            return
        self.pending_layout = None
        options = dict(self.options)
        options['pageStep'] = 2 if self.spread_active else 1
        options['deferReady'] = self.spread_active
        self.secondary_layout_needed = True
        if self.is_fixed():
            width, height = self.book.fixed_size(self.spine)
            scale = min((self.web.get_width()-48)/width, (self.web.get_height()-48)/height)
            zoom = self.options['fixedZoom']
            self.pan_x = max(-width*scale*(zoom-1), min(0, self.pan_x))
            self.pan_y = max(-height*scale*(zoom-1), min(0, self.pan_y))
            options.update(direction='fixed', fixedWidth=width, fixedHeight=height, href=self.book.hrefs[self.spine],
                           zoom=zoom, panX=self.pan_x, panY=self.pan_y)
        self.layout_options = options
        self.run('await reading.layout('+json.dumps(options)+','+json.dumps(target)+','+
                 str(last).lower()+','+str(self.dark).lower()+')', reflow=True)

    def enqueue(self, kind, value=None):
        if self.closed:
            return
        if kind in ('combine','chapter','authorBreaks'):
            return
        self.actions.append((kind, value))
        self.dispatch()

    def dispatch(self):
        if self.busy or not self.ready or not self.actions:
            return
        kind, value = self.actions.popleft()
        self.busy = True
        self.save_navigation = kind in ('move', 'seek')
        if kind == 'move':
            self.has_selection(lambda selected:self.move_after_selection(value,selected))
            return
        if kind == 'seek':
            self.pending_seek = True
            self.library.validate_locator(self.sha, value)
            self.pan_x = self.pan_y = 0
            self.spine = value['spine_index']
            self.part = self.book.part_for(self.spine, value)
            self.target = value
            self.last_page = False
            self.load()
            return
        if self.is_fixed() and kind in ('size', 'font', 'direction', 'line', 'spacing', 'margin', 'reset_text', 'paragraphGap', 'indent', 'align', 'ruby'):
            self.busy = False
            self.on_status(self)
            self.dispatch()
            return
        if kind == 'direction':
            self.options['direction'] = 'horizontal' if self.current['direction'] == 'vertical' else 'vertical'
        elif kind == 'spread':
            if value in ('single','double','auto'):
                self.options['spread'] = value
        elif kind == 'zoom':
            self.options['fixedZoom'] = max(1, min(4, self.options['fixedZoom']*value))
        elif kind == 'fit':
            self.options['fixedZoom'] = 1
            self.pan_x = self.pan_y = 0
        elif kind == 'pan':
            self.pan_x += value[0]
            self.pan_y += value[1]
        elif kind == 'reset_text':
            self.options.update(size=18,line=1.8,margin=24,spacing=0)
        elif kind == 'size':
            self.options['size'] = max(10, min(48, self.options['size']+value))
        elif kind in ('line', 'spacing', 'margin'):
            lower,upper = {'line':(1,3),'spacing':(-1,8),'margin':(4,80)}[kind]
            self.options[kind] = max(lower,min(upper,value))
        elif kind in ('paragraphGap','indent','align','ruby'):
            allowed={'paragraphGap':['original',0,.5,1,1.5,2,3], 'indent':['original',0,1,2], 'align':['original','start','justify'], 'ruby':[True,False]}
            if value in allowed[kind]:self.options[kind]=value
        elif kind == 'font':
            families = ['serif', 'sans-serif', 'original']
            self.options['font'] = value if value in self.font_values else families[(families.index(self.options['font'])+1) % len(families)] if self.options['font'] in families else 'serif'
        self.library.save_options(self.sha, self.options)
        self.layout(self.anchor)

    def move_after_selection(self, step, selected):
        if selected:
            self.busy = False
            self.dispatch()
            return
        page = self.current['page']+step*(2 if self.spread_active else 1)
        if 0 <= page < self.current['pages']:
            self.run(f'await reading.show({page})')
            return
        destination = self.next_section(step)
        if destination is None:
            self.busy = False
            self.dispatch()
            return
        self.pan_x = self.pan_y = 0
        self.spine, self.part = destination
        self.target = None
        self.last_page = step < 0
        self.load()

    def completed(self, value):
        rtl = self.book.progression == 'rtl' or (not self.book.progression and value['direction'] == 'vertical')
        self.reorder_child_after(self.primary_pane,self.secondary_pane if rtl else None)
        if self.spread_active:
            page = value['page']+1
            blank = page >= value['pages']
            prefix = ''
            if self.secondary_layout_needed:
                options = dict(self.layout_options, pageStep=1)
                prefix = 'await reading.layout('+json.dumps(options)+',null,false,'+str(self.dark).lower()+');'
            expression = 'await (async()=>{'+prefix+'return await reading.show('+str(min(page,value['pages']-1))+');})()'
            def paired(partner):
                if partner['pages'] != value['pages']:
                    self.failed('見開きのページ数が一致しません。本棚へ戻って開き直してください。')
                    return
                self.secondary_layout_needed = False
                script = 'document.body.style.setProperty("visibility","'+('hidden' if blank else 'visible')+'","important");JSON.stringify(true)'
                def shown(unused):
                    value['secondary'] = None if blank else partner
                    value['visiblePages'] = [value['page']] if blank else [value['page'],page]
                    self.commit(value)
                if self.reveal_layout or self.secondary_blank != blank:
                    self.secondary_blank = blank
                    self.evaluate_web(self.secondary,script,shown)
                else:
                    shown(None)
            self.run_web(self.secondary,expression,paired)
            return
        value['secondary'] = None
        value['visiblePages'] = [value['page']]
        self.commit(value)

    def commit(self, value):
        if not value.get('anchor'):
            self.failed('本文を表示できません。この区間の形式は後続の取り込み拡張で対応します。')
            return
        self.current = value
        value['spines'] = list(self.group_spines)
        value['flowFallback'] = self.flow_fallback
        self.spine = value['anchor'].get('spine',self.spine)
        if self.reveal_layout:
            for web in (self.web,self.secondary):
                reveal = 'document.body.style.removeProperty("visibility");' if web == self.web else ''
                self.evaluate_web(web,reveal+'document.documentElement.dataset.readerReady="";JSON.stringify(true)',lambda _:None)
        self.reveal_layout = False
        if self.save_navigation or self.anchor is None:
            restored = value.get('restored') or {}
            anchor = dict(restored.get('anchor') if self.pending_seek and restored.get('anchor') else value['anchor'])
            source_spine = anchor.get('spine',self.spine)
            anchor.update(version=1, sha256=self.sha, spine_index=source_spine, href=self.book.hrefs[source_spine],
                          cfi=anchor.get('cfi'),
                          fragment=anchor.get('element') if anchor.get('element') != 'source' else None,
                          progression=restored.get('progression',value['progression']) if self.pending_seek else value['progression'])
            self.anchor = anchor
            finished = self.spine == len(self.book.spine)-1 and self.part == self.book.part_count(self.spine)-1 and value['visiblePages'][-1] == value['pages']-1
            self.library.save_position(self.sha, self.anchor, finished)
        self.pending_seek = False
        if self.pending_history:
            action, origin = self.pending_history
            if action == 'push':
                self.link_history = self.link_history[-31:]+[origin]
            elif self.link_history:
                self.link_history.pop()
            self.pending_history = None
        self.busy = False
        self.prefetch()
        self.on_status(self)
        self.dispatch()

    def failed(self, message):
        if self.closed:
            return
        self.ready = False
        self.busy = False
        self.pending_seek = False
        self.pending_history = None
        self.actions.clear()
        self.on_error(message)

    def tick(self, widget, clock):
        viewport = (widget.get_width(), widget.get_height(), widget.get_scale_factor())
        if viewport != self.viewport:
            self.viewport = viewport
            if self.resize_timer:
                GLib.source_remove(self.resize_timer)
            self.resize_timer = GLib.timeout_add(250, self.resized)
        return not self.closed

    def resized(self):
        self.resize_timer = 0
        if self.pending_layout is not None and self.ready:
            target, last = self.pending_layout
            self.layout(target, last)
        elif self.current:
            self.enqueue('resize')
        return False

    def rtl(self):
        return self.book.progression == 'rtl' or (not self.book.progression and self.current and self.current['direction'] == 'vertical')

    def has_selection(self, callback):
        script = 'JSON.stringify(Boolean(window.getSelection()?.toString()))'
        def primary(selected):
            if selected or not self.spread_active or self.current and self.current.get('secondary') is None:
                callback(selected)
            else:
                self.evaluate_web(self.secondary,script,callback)
        self.evaluate(script,primary)

    def pointer_event(self, controller, event):
        # Some GI versions pass None as the legacy signal argument.
        event = event or controller.get_current_event()
        if event is None or event.get_pointer_emulated():
            return False
        kind = event.get_event_type()
        if kind not in (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE) or event.get_button() != 1:
            return False
        valid, x, y = event.get_position()
        web = controller.get_widget()
        if not valid or not web.get_native():
            return False
        valid, point = web.get_native().compute_point(web, Graphene.Point().init(x,y))
        if not valid:
            return False
        if kind == Gdk.EventType.BUTTON_PRESS:
            self.pressed(controller, 1, point.x, point.y)
        else:
            self.tap(controller, 1, point.x, point.y)
        # Observe without claiming the sequence: WebKit still handles links and selection.
        return False

    def pressed(self, gesture, count, x, y):
        self.press = (x, y, GLib.get_monotonic_time())

    def tap(self, gesture, count, x, y):
        if count != 1:
            return
        if not hasattr(self, 'press') or abs(x-self.press[0])+abs(y-self.press[1]) > 12:
            return
        if GLib.get_monotonic_time()-self.press[2] > 450000:
            return
        if gesture and self.book.links and self.ready and not self.busy:
            web = gesture.get_widget()
            script = 'JSON.stringify(Boolean(document.elementFromPoint('+str(float(x))+','+str(float(y))+')?.closest("a[href]")))'
            self.evaluate_web(web, script, lambda link: None if link else self.page_tap(gesture,x,y))
            return
        self.page_tap(gesture,x,y)

    def page_tap(self, gesture, x, y):
        if self.is_fixed() and self.options['fixedZoom'] > 1:
            if self.on_controls:
                self.on_controls()
            return
        if gesture:
            valid,bounds = gesture.get_widget().compute_bounds(self)
            if valid:
                x += bounds.origin.x
        if x < self.get_width()*.2:
            self.enqueue('move', 1 if self.rtl() else -1)
        elif x > self.get_width()*.8:
            self.enqueue('move', -1 if self.rtl() else 1)

        elif self.on_controls:
            if self.ready and not self.busy:
                self.has_selection(lambda selected:self.on_controls() if not selected and not self.closed else None)
            else:
                self.on_controls()

    def pan(self, gesture, x, y):
        if self.is_fixed() and self.options['fixedZoom'] > 1:
            self.enqueue('pan', (x, y))

    def swipe(self, gesture, x, y):
        if self.is_fixed() and self.options['fixedZoom'] > 1:
            return
        if abs(x) > abs(y):
            self.enqueue('move', 1 if (x > 0) == self.rtl() else -1)

    def scroll(self, controller, x, y):
        if not x and not y:
            return False
        if self.is_fixed() and self.options['fixedZoom'] > 1:
            self.enqueue('pan', (-x*40, -y*40))
            return True
        self.enqueue('move', 1 if y > 0 or (x < 0 if self.rtl() else x > 0) else -1)
        return True

    def close(self):
        self.closed = True
        if self.resize_timer:
            GLib.source_remove(self.resize_timer)
        for web in (self.web,self.secondary):
            web.stop_loading()
            web.load_html('<!doctype html><html><body></body></html>', 'about:blank')
        if self.prepared_future:
            self.prepared_future.cancel()
        self.loader.shutdown(wait=False, cancel_futures=True)
        self.book.z.close()
