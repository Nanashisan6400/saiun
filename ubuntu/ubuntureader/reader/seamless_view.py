"""Trial-only incremental append; normal app/view/probe files stay untouched."""
import json
from pathlib import Path
import gi
gi.require_version("WebKit","6.0")
from gi.repository import GLib,WebKit
from .view import ReadingView,CORE
from .seamless_book import SeamlessBook
from .cache_budget import cache_budget_mb

HERE=Path(__file__).parent


def scripts():
    probe=(CORE/'probe.js').read_text()
    probe=probe.replace('glyphs.push({id:glyphs.length,text:', 'glyphs.push({node,offset,id:glyphs.length,text:')
    probe=probe.replace("glyphs.push({id:glyphs.length,kind:'object'", "glyphs.push({node:object,offset:0,id:glyphs.length,kind:'object'")
    start=probe.index(' function report(){');end=probe.index(' async function show(',start)
    probe=probe[:start]+(HERE/'seamless_probe.js').read_text()+'\n'+(HERE/'seamless_report.js').read_text()+probe[end:]
    probe=probe.replace('return {setup,report,show,anchor,restore,elementLocation};','return {setup:compactSetup,extend,signature,positions,report,show,anchor,restore,elementLocation};')
    reading=(HERE/'reading.js').read_text().replace('return {anchor, progression:', 'return {anchor, cacheBytes:report.cacheBytes||0, progression:').replace('return {layout,show,theme,result};','return {layout,show,theme,result,refresh:()=>progressIndexes.clear()};')
    wrappers='''
    (()=>{
      const textSizes=new WeakMap(),baseSizes=new WeakMap();
      window.readerTextSettings=(options,nodes)=>{
        const source=document.getElementById('source'),oldSize=source.style.fontSize;
        source.style.fontSize='16px';
        const ancestors=new Map();
        for(const node of nodes)for(let parent=node.parentElement;parent&&parent!==source;parent=parent.parentElement)if(textSizes.has(parent)&&!ancestors.has(parent)){
          ancestors.set(parent,[parent.style.getPropertyValue('font-size'),parent.style.getPropertyPriority('font-size')]);
          parent.style.setProperty('font-size',textSizes.get(parent)+'px','important');
        }
        const measured=nodes.map(node=>{
          const section=node.closest('[data-reader-part]')||source;
          if(!baseSizes.has(section))baseSizes.set(section,parseFloat(getComputedStyle(section).fontSize)||16);
          if(!textSizes.has(node))textSizes.set(node,parseFloat(getComputedStyle(node).fontSize)||16);
          return [node,textSizes.get(node)/baseSizes.get(section)];
        });
        for(const [node,[value,priority]] of ancestors)node.style.setProperty('font-size',value,priority);
        source.style.fontSize=oldSize;
        for(const [node,ratio] of measured){
          node.style.setProperty('font-size',options.size*ratio+'px','important');
          node.style.setProperty('letter-spacing',options.spacing+'px','important');
          if(node.tagName==='P'){
            if(options.paragraphGap!=='original'){
              node.style.setProperty('margin-block-start','0','important');
              node.style.setProperty('margin-block-end',options.paragraphGap+'em','important');
            }
            if(options.indent!=='original')node.style.setProperty('text-indent',options.indent+'em','important');
            if(options.align!=='original')node.style.setProperty('text-align',options.align,'important');
          }
          if(['RT','RP'].includes(node.tagName)&&options.ruby===false)node.style.setProperty('display','none','important');
          if(['ARTICLE','SECTION','DIV','P','H1','H2','H3','H4','LI','BLOCKQUOTE','TD'].includes(node.tagName))node.style.setProperty('line-height',String(options.line),'important');
        }
      };
      window.readerAuthorBreaks=section=>{for(const node of [section,...section.querySelectorAll('[data-reader-chapter-break]')])if(node.hasAttribute('data-reader-chapter-break'))node.style.setProperty('break-before','column','important');const style=getComputedStyle(section),forced=v=>['page','column','always','left','right','recto','verso'].includes(v);
       for(const side of ['before','after'])if(section.dataset[side==='before'?'readerDocStart':'readerDocEnd']==='1'&&(section.dataset[side==='before'?'readerBreakBefore':'readerBreakAfter']||forced(style[side==='before'?'breakBefore':'breakAfter']))&&!section.querySelector('[data-reader-author-'+side+']')){
        const marker=document.createElement('div');marker.setAttribute('data-reader-author-'+side,'');marker.style.cssText='display:block!important;width:0!important;height:0!important;margin:0!important;padding:0!important;break-'+side+':column!important';
        if(side==='before')section.prepend(marker);else section.append(marker);
       }
      };
      const locate=verticalTypography.locate,indices=new Map();
      verticalTypography.locate=(node,offset)=>{const l=locate(node,offset);if(l?.cfi){const key=l.cfi.slice(0,l.cfi.lastIndexOf(':'));if(!indices.has(key))indices.set(key,indices.size+1);l.textIndex=indices.get(key);}return l;};
      window.readerOccupiedPages=(glyphs,origin,extent,step)=>{const occupied=new Set();for(const glyph of glyphs){const first=Math.max(0,Math.floor((glyph.start-origin)/step)-1),last=Math.floor((glyph.end-origin)/step)+1;for(let index=first;index<=last;index++){const start=origin+index*step,end=start+extent;if(Math.min(glyph.end,end)-Math.max(glyph.start,start)>.25)occupied.add(index);}}return [...occupied].sort((a,b)=>a-b).map(index=>({start:origin+index*step,end:origin+index*step+extent}));};
      let initial=true;const layout=reading.layout;reading.layout=async(options,...args)=>{if(initial)options={...options,deferReady:true};for(const section of document.querySelectorAll('[data-reader-part]'))readerAuthorBreaks(section);if(options.direction!=='original'&&options.direction!=='fixed')for(const section of document.querySelectorAll('[data-reader-part]'))section.style.setProperty('writing-mode',options.direction==='vertical'?'vertical-rl':'horizontal-tb','important');if(options.direction!=='fixed')readerTextSettings(options,[...document.querySelectorAll('#source *')]);const result=await layout(options,...args);initial=false;return result;};
    })();
    '''
    return (CORE/'typography.js').read_text()+'\n'+probe+'\n'+reading+'\n'+wrappers


class SeamlessView(ReadingView):
    book_type = SeamlessBook
    def __init__(self,*args,**kwargs):
        self.initializing=True
        self.initial_hidden=True
        self.extending=False
        self.stream_future=None
        self.stream_payload=None
        self.stream_key=None
        self.stream_barrier=None
        self.waiting_step=None
        self.stream_start=None
        self.stream_end=None
        self.append_count=0
        self.cache_releases=0
        super().__init__(*args,**kwargs)

    def load(self):
        if self.stream_future:self.stream_future.cancel()
        self.stream_future=None
        self.initializing=True
        self.initial_hidden=True
        self.extending=False
        self.stream_payload=self.stream_key=None
        self.stream_barrier=None
        self.waiting_step=None
        self.stream_start=self.stream_end=(self.spine,self.part)
        self.group_spines=[self.spine]
        for name in ('section_plans','window_plans','_contents_paths'):
            cache=getattr(self.book,name,{})
            for index in list(cache):
                if index!=self.spine:del cache[index]
        super().load()

    def loaded(self,web,event):
        if event==WebKit.LoadEvent.STARTED:
            self.loading_generation[web]=self.generation
            return
        if self.loading_generation.get(web)!=self.generation or event!=WebKit.LoadEvent.FINISHED or self.closed:return
        def injected(unused):
            self.injected.add(web)
            if len(self.injected)==2:
                self.ready=True
                self.layout(self.target,self.last_page)
        self.evaluate_web(web,scripts()+';JSON.stringify(true)',injected)

    def layout(self,target=None,last=False):
        if self.initializing or self.is_fixed():
            return super().layout(target,last)
        # A new size/font/direction invalidates the old page cache. Start a new
        # bounded stream at the original locator rather than reflowing all history.
        target=target or self.anchor
        if target:
            self.spine=target.get('spine_index',target.get('spine',self.spine))
            self.part=self.book.part_for(self.spine,target)
        self.target=target
        self.last_page=last
        self.load()

    def after(self,key,step=1):
        spine,part=key
        candidate=part+step
        if 0<=candidate<self.book.part_count(spine):return spine,candidate
        spine+=step
        if 0<=spine<len(self.book.spine):return spine,0 if step>0 else self.book.part_count(spine)-1
        return None

    def next_section(self,step=1):
        return self.after(self.stream_end if step>0 else self.stream_start,step)

    def can_extend(self,key):
        return key is not None and key!=self.stream_barrier and not self.is_fixed() and self.book.layouts[key[0]]!='fixed'

    def cache_usage(self):
        return self.current.get('cacheBytes',0)*(2 if self.spread_active else 1) if self.current else 0

    def release_old_cache(self):
        if self.extending or self.initializing or not self.anchor or self.is_fixed():return False
        key=(self.anchor.get('spine_index',self.spine),self.book.part_for(self.anchor.get('spine_index',self.spine),self.anchor))
        if key<=self.stream_start:return False
        self.cache_releases+=1
        # Drop plans/ID maps for older documents as well as the display DOM.
        for name in ('section_plans','window_plans','_contents_paths'):
            cache=getattr(self.book,name,{})
            for index in list(cache):
                if index!=key[0]:del cache[index]
        self.save_navigation=False
        self.layout(self.anchor)
        return True

    def prefetch(self):
        if not self.current or self.closed or self.initializing:return
        limit=cache_budget_mb(self.library)*1024*1024
        if self.cache_usage()>=limit:
            if self.release_old_cache():return
            # A single current window must stay displayable. Do not accumulate
            # speculative data above the budget; forward navigation can demand it.
            if self.waiting_step is None:return
        key=self.after(self.stream_end)
        if not self.can_extend(key):return
        # Keep a few complete pages ahead; never expose an artificial tail page.
        if self.current['pages']-1-self.current['visiblePages'][-1]>4 and self.waiting_step is None:return
        if self.stream_key!=key:
            self.stream_key=key
            generation=self.generation
            self.stream_future=self.loader.submit(self.prepare_section,*key)
            def prepared(future):
                def activate():
                    if self.closed or generation!=self.generation or key!=self.stream_key:return False
                    try:self.stream_payload=future.result()
                    except Exception as error:self.failed(str(error));return False
                    self.try_extend()
                    return False
                GLib.idle_add(activate)
            self.stream_future.add_done_callback(prepared)
        self.try_extend()

    def try_extend(self):
        if (self.busy and self.waiting_step is None) or self.extending or self.stream_payload is None or self.closed:return
        article,css,links,standard,reasons=self.stream_payload
        key=self.stream_key
        self.stream_payload=None
        self.busy=True
        self.extending=True
        self.save_navigation=False
        self.reveal_layout=False
        payload=json.dumps(dict(html=article,css=css,font=self.options['font'],direction=self.options['direction'],size=self.options['size'],line=self.options['line'],spacing=self.options['spacing'],paragraphGap=self.options['paragraphGap'],indent=self.options['indent'],align=self.options['align'],ruby=self.options['ruby']))
        expression='await probe.extend('+payload+')'
        def complete(value):
            if value.get('barrier'):
                self.stream_barrier=key
                self.extending=False
                self.busy=False
                if self.waiting_step is not None:
                    step=self.waiting_step;self.waiting_step=None;self.actions.appendleft(('move',step))
                self.dispatch()
                return
            self.stream_end=key
            self.stream_key=None
            self.book.links.update(links)
            self.book.standard_spines.update(standard)
            self.book.standard_reasons.update(reasons)
            if standard:self.library.mark_standard_style(self.sha,standard)
            self.group_spines=list(range(self.stream_start[0],self.stream_end[0]+1))
            self.secondary_layout_needed=False
            self.append_count+=1
            self.completed(value)
        if self.spread_active:
            self.run_web(self.secondary,expression,lambda _:self.run_web(self.web,expression,complete))
        else:
            # Secondary is laid out when spread is enabled (which resets stream).
            self.run_web(self.web,expression,complete)

    def commit(self,value):
        navigated=self.save_navigation
        was_extending=self.extending
        if was_extending and self.current and value.get('restored') is None:
            value['restored']=self.current.get('restored')
        if value.get('anchor'):
            self.spine=value['anchor'].get('spine',self.spine)
            self.part=self.book.part_for(self.spine,value['anchor'])
        self.initializing=False
        if self.initial_hidden:
            needs_tail=value.get('cacheBytes',0)*(2 if self.spread_active else 1)<cache_budget_mb(self.library)*1024*1024 and self.can_extend(self.after(self.stream_end)) and value['visiblePages'][-1]>=value['pages']-2
            self.reveal_layout=not needs_tail
            if not needs_tail:self.initial_hidden=False
        if was_extending and self.waiting_step is not None:
            self.actions.appendleft(('move',self.waiting_step))
            self.waiting_step=None
        super().commit(value)
        if navigated and self.anchor and self.stream_end==(len(self.book.spine)-1,self.book.part_count(len(self.book.spine)-1)-1) and value['visiblePages'][-1]==value['pages']-1:
            self.library.save_position(self.sha,self.anchor,True)
        if was_extending:
            self.extending=False
            self.prefetch()

    def move_after_selection(self,step,selected):
        if not selected and step>0:
            key=self.after(self.stream_end)
            target=self.current['page']+step*(2 if self.spread_active else 1)
            if self.can_extend(key) and target+(1 if self.spread_active else 0)>=self.current['pages']-2:
                self.waiting_step=step
                self.busy=True
                self.prefetch()
                return
        super().move_after_selection(step,selected)

    def follow_link(self,reference,web=None):
        spine,generation=self.spine,self.generation
        if web==self.secondary and self.current and self.current.get('secondary'):
            self.spine=self.current['secondary']['anchor'].get('spine',spine)
        try:
            super().follow_link(reference,web)
        finally:
            if self.generation==generation:self.spine=spine

    def close(self):
        if self.stream_future:self.stream_future.cancel()
        super().close()
