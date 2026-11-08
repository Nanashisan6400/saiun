/* Measure rendered ranges; typography spans map back to original text offsets. */
window.probe = (() => {
 let state={}, glyphs=[], pages=[], reportCache=null;
 const frame=()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
 async function setup(options) {
  reportCache=null;
  const src=document.getElementById('source');
  src.style.cssText='';
  document.documentElement.style.cssText='margin:0;width:100%;height:100%;overflow:hidden';
  document.body.style.cssText='margin:0;width:100%;height:100%;overflow:hidden;background:#fff;color:#111';
  let view=document.getElementById('viewport');
  if(!view){view=document.createElement('div');view.id='viewport';src.before(view);view.append(src);}
  const W=innerWidth,H=innerHeight,pad=options.margin??24;
  if(options.direction==='fixed'){
   const width=options.fixedWidth??800,height=options.fixedHeight??1100;
   const scale=Math.min((W-2*pad)/width,(H-2*pad)/height);
   const zoom=options.zoom??1,panX=options.panX??0,panY=options.panY??0;
   view.style.cssText=`position:absolute;left:${(W-width*scale)/2}px;top:${(H-height*scale)/2}px;width:${width*scale}px;height:${height*scale}px;overflow:hidden;border:1px solid teal`;
   src.style.cssText=`width:${width}px;height:${height}px;transform-origin:top left;transform:translate(${panX}px,${panY}px) scale(${scale*zoom});font:20px serif;writing-mode:horizontal-tb`;
   await document.fonts.ready;await frame();
   const bounds=src.getBoundingClientRect();
   state={...options,W,H,pad,zoom,panX,panY,devicePixelRatio,extent:height,pages:1,totalGlyphs:0,fitScale:scale,fitWidth:width*scale,fitHeight:height*scale,fixedBounds:{x:bounds.x,y:bounds.y,width:bounds.width,height:bounds.height}};glyphs=[];pages=[{start:0,end:height}];reportCache=null;
   return report();
  }

  const vertical=options.direction==='vertical';
  view.style.cssText=`position:absolute;left:${pad}px;top:${pad}px;width:${W-2*pad}px;height:${H-2*pad}px;overflow:hidden;outline:1px solid #58a`;
  src.style.cssText=`position:absolute;margin:0;writing-mode:${vertical?'vertical-rl':'horizontal-tb'};font-family:${options.font??'serif'};font-size:${options.size??20}px;line-height:${options.line??1.8};letter-spacing:${options.spacing??0}px;`;
  if(options.preserveAuthor){for(const property of ['writing-mode','font-family','font-size','line-height','letter-spacing'])src.style.removeProperty(property);}
  if(options.hasAuthorCss&&!options.preserveAuthor){for(const property of ['writing-mode','font-family','font-size','line-height','letter-spacing'])src.style.setProperty(property,src.style.getPropertyValue(property),'important');}
  src.style.padding='0';src.style.boxSizing='border-box';
  if(options.method==='columns'){
   src.style.width=(W-2*pad)+'px';src.style.height=(H-2*pad)+'px';
   src.style.columnWidth=(vertical?H-2*pad:W-2*pad)+'px';src.style.columnGap=(2*(options.size??20))+'px';
   src.style.columnFill='auto';src.style.right=vertical?'0':'auto';
   const markers=[...src.querySelectorAll('section,[data-chapter-start]')].filter(node=>
    node.hasAttribute('data-chapter-start')||(node.getAttribute('epub:type')||'').split(/\s+/).includes('chapter'));
   for(const marker of markers){
    if(!marker.hasAttribute('data-original-break-before'))marker.dataset.originalBreakBefore=marker.style.breakBefore;
    const before=document.createRange();before.selectNodeContents(src);before.setEndBefore(marker);
    const firstObject=src.querySelector('img,svg');
    const hasPrefix=before.toString().trim()||(firstObject&&(marker.compareDocumentPosition(firstObject)&Node.DOCUMENT_POSITION_PRECEDING));
    if(marker.hasAttribute('data-reader-chapter'))marker.style.setProperty('break-before',options.chapterBreaks!==false&&hasPrefix?'column':'auto','important');
    else marker.style.breakBefore=options.chapterBreaks!==false&&before.toString().trim()?'column':marker.dataset.originalBreakBefore;
   }
  }else if(vertical){src.style.right='0';src.style.height=(H-2*pad)+'px';src.style.width='max-content';}
  else{src.style.left='0';src.style.top='0';src.style.width=(W-2*pad)+'px';}
  // Percentage image limits can remain indefinite inside CSS columns. Bound
  // the replaced element in viewport pixels before measuring page geometry.
  for(const image of src.querySelectorAll('img')){
   image.style.setProperty('max-width',(W-2*pad)+'px','important');
   image.style.setProperty('max-height',(H-2*pad)+'px','important');
   image.style.setProperty('object-fit','contain','important');
   image.style.setProperty('break-inside','avoid','important');
   const parent=image.parentElement;
   if(!parent.textContent.trim()&&parent.querySelectorAll('img').length===1){
    let horizontalSpace=0,verticalSpace=0;
    for(let box=parent;box&&box!==src;box=box.parentElement){
     const style=getComputedStyle(box),positive=key=>Math.max(0,parseFloat(style[key])||0);
     horizontalSpace+=positive('marginLeft')+positive('marginRight')+positive('paddingLeft')+positive('paddingRight');
     verticalSpace+=positive('marginTop')+positive('marginBottom')+positive('paddingTop')+positive('paddingBottom');
    }
    image.style.setProperty('max-width',Math.max(1,W-2*pad-horizontalSpace)+'px','important');
    image.style.setProperty('max-height',Math.max(1,H-2*pad-verticalSpace)+'px','important');
    // Image-only paragraphs otherwise reserve an inline text baseline below
    // a full-height image and can fragment it across column boundaries.
    image.style.setProperty('display','block','important');
    image.style.setProperty('margin-inline','auto','important');
   }
  }
  for(const ruby of src.querySelectorAll('ruby')){
   // Keep native ruby formatting: changing RUBY itself to inline-block leaves
   // RT rectangles measurable in WebKit but prevents their text from painting.
   // The outer box handles column boundaries without changing source text/CFI.
   ruby.style.setProperty('display','ruby','important');
   ruby.style.removeProperty('padding-inline-start');
   ruby.style.removeProperty('padding-inline-end');
   let box=ruby.parentElement;
   if(!box.hasAttribute('data-reader-ruby-box')){
    box=document.createElement('reader-ruby-box');
    box.setAttribute('data-reader-ruby-box','');
    ruby.before(box);box.append(ruby);
   }
   box.style.setProperty('display','inline-block','important');
   box.style.removeProperty('padding-inline-start');
   box.style.removeProperty('padding-inline-end');
  }
  if(window.verticalTypography){
   const family=window.verticalTypography.normalizeFamily(getComputedStyle(src).fontFamily);
   src.style.setProperty('font-family',family,src.style.getPropertyPriority('font-family'));
  }
  const typography=window.verticalTypography?.apply(src)??null;
  await document.fonts.ready;await frame();
  let rubySpaced=false;
  for(const annotation of src.querySelectorAll('rt')){
   const units=[...annotation.querySelectorAll('[data-stage0-ruby-char]')];
   const chars=units.filter(char=>char.textContent.trim());
   const align=getComputedStyle(annotation).rubyAlign;
   if(chars.length<2||!['space-around','space-between'].includes(align))continue;
   const bounds=annotation.getBoundingClientRect();
   const available=vertical?bounds.height:bounds.width;
   const natural=units.reduce((sum,char)=>{const rect=char.getBoundingClientRect();return sum+(vertical?rect.height:rect.width);},0);
   if(available-natural<=.5)continue;
   const between=align==='space-between',space=(available-natural)/(chars.length-(between?1:0));
   for(const [index,char] of chars.entries()){
    char.style.setProperty('padding-inline-start',between&&index===0?'0':space/2+'px','important');
    char.style.setProperty('padding-inline-end',between&&index===chars.length-1?'0':space/2+'px','important');
   }
   rubySpaced=true;
  }
  if(rubySpaced)await frame();
  let rubyAdjusted=false;
  // WebKit may exclude annotation overhang from the ruby's inline advance.
  // Reserve only that measured excess before collecting cached page geometry.
  for(const ruby of src.querySelectorAll('ruby')){
   const container=ruby.parentElement,box=container.getBoundingClientRect(),annotations=[...ruby.querySelectorAll('rt')].map(rt=>rt.getBoundingClientRect());
   if(!annotations.length)continue;
   const start=Math.max(0,...annotations.map(r=>vertical?box.top-r.top:box.left-r.left));
   const end=Math.max(0,...annotations.map(r=>vertical?r.bottom-box.bottom:r.right-box.right));
   if(start>.1){container.style.setProperty('padding-inline-start',Math.ceil(start)+'px','important');rubyAdjusted=true;}
   if(end>.1){container.style.setProperty('padding-inline-end',Math.ceil(end)+'px','important');rubyAdjusted=true;}
  }
  if(rubyAdjusted)await frame();
  if(vertical)window.verticalTypography?.containOptical(src,view.getBoundingClientRect());
  const axisX=options.method==='columns'?!vertical:vertical;
  const reverseX=axisX&&vertical;
  state={...options,typography,observedWritingMode:getComputedStyle(src).writingMode,observedFontSize:getComputedStyle(src).fontSize,observedFontFamily:getComputedStyle(src).fontFamily,W,H,pad,vertical,axisX,reverseX,devicePixelRatio,extent:axisX?W-2*pad:H-2*pad};
  glyphs=[];
  const vp=view.getBoundingClientRect();
  const walker=document.createTreeWalker(src,NodeFilter.SHOW_TEXT);
  let node,textIndex=0;
  while(node=walker.nextNode()){
   textIndex++;if(!node.textContent.trim())continue;
   for(let offset=0;offset<node.length;){
    const length=node.textContent.codePointAt(offset)>65535?2:1;
    const range=document.createRange();range.setStart(node,offset);range.setEnd(node,offset+length);
    const rects=[...range.getClientRects()].filter(r=>r.width>0&&r.height>0);
    for(const r of rects){
     const start=state.axisX?(state.reverseX?vp.right-r.right:r.left-vp.left):r.top-vp.top;
     const end=state.axisX?(state.reverseX?vp.right-r.left:r.right-vp.left):r.bottom-vp.top;
     glyphs.push({id:glyphs.length,text:node.textContent.slice(offset,offset+length),start,end,
       crossStart:state.axisX?r.top-vp.top:r.left-vp.left,crossEnd:state.axisX?r.bottom-vp.top:r.right-vp.left,
       locator:window.verticalTypography?.locate(node,offset)??{textIndex,element:node.parentElement.closest('[id]')?.id??null,offset,tag:node.parentElement.tagName}});
    }
    offset+=length;
   }
  }
  const objectCounters=new Map();
  for(const object of src.querySelectorAll('img,svg')){
   const rect=object.getBoundingClientRect();
   if(!rect.width||!rect.height)continue;
   const owner=object.closest('[data-spine]');
   const objectSpine=owner?.dataset.spine??'';
   const objectIndex=objectCounters.get(objectSpine)??Number(owner?.dataset.readerImageOffset||0);objectCounters.set(objectSpine,objectIndex+1);
   const locator={kind:'object',objectIndex,element:object.id||null};
   if(owner){locator.spine=Number(owner.dataset.spine);locator.href=owner.dataset.href;}
   glyphs.push({id:glyphs.length,kind:'object',text:'',
    start:state.axisX?(state.reverseX?vp.right-rect.right:rect.left-vp.left):rect.top-vp.top,
    end:state.axisX?(state.reverseX?vp.right-rect.left:rect.right-vp.left):rect.bottom-vp.top,
    crossStart:state.axisX?rect.top-vp.top:rect.left-vp.left,
    crossEnd:state.axisX?rect.bottom-vp.top:rect.right-vp.left,locator});
  }
  const max=glyphs.reduce((maximum,g)=>Math.max(maximum,g.end),0);
  pages=[];
  if(options.method==='lines'){
   // Intervals cannot be separated through base text, ruby, or emphasis boxes.
   const intervals=glyphs.map(g=>[Math.max(0,g.start),g.end]).sort((a,b)=>a[0]-b[0]);
   const bands=[];
   for(const band of intervals){const last=bands.at(-1);if(last&&band[0]<last[1]-.1)last[1]=Math.max(last[1],band[1]);else bands.push([...band]);}
   let start=0;
   for(let i=0;i<bands.length;){
    let j=i;while(j<bands.length&&bands[j][1]-start<=state.extent+.1)j++;
    if(j===i){pages.push({start,end:start+state.extent,oversized:true});i++;start=bands[i]?.[0]??max;}
    else {const end=j<bands.length?(bands[j-1][1]+bands[j][0])/2:Math.max(max,start+state.extent);pages.push({start,end:Math.min(end,start+state.extent)});start=j<bands.length?end:max;i=j;}
   }
  }else {const step=state.extent+(options.method==='columns'?2*(options.size??20):0);for(let start=0;start<max;start+=step)pages.push({start,end:start+state.extent});}
  // A break-before on the first element may create empty leading columns.
  // Start at the first occupied column without altering any internal breaks
  // or original source coordinates used by CFI restoration.
  const firstContentStart=glyphs.reduce((minimum,g)=>Math.min(minimum,g.start),Infinity);
  const firstContentPage=pages.findIndex(p=>p.end>firstContentStart+.25);
  if(firstContentPage>0)pages.splice(0,firstContentPage);
  state.columnGap=options.method==='columns'?2*(options.size??20):0;
  state.totalGlyphs=glyphs.length;state.pages=pages.length;state.max=max;reportCache=null;
  return report();
 }
 function report(){
  if(reportCache)return {...reportCache,...state};
  const counts=new Map();let cuts=0;const examples=[];
  for(const [index,p] of pages.entries())for(const g of glyphs){
   const overlap=Math.min(g.end,p.end)-Math.max(g.start,p.start);
   if(overlap>.25){counts.set(g.id,(counts.get(g.id)||0)+1);if(g.start<p.start-.5||g.end>p.end+.5){cuts++;if(examples.length<12)examples.push({page:index,...g});}}
  }
  const crossExtent=state.axisX?state.H-2*state.pad:state.W-2*state.pad;
  reportCache={...state,cuts,missing:glyphs.filter(g=>!counts.has(g.id)).length,duplicate:glyphs.filter(g=>(counts.get(g.id)||0)>1).length,
   crossOverflow:glyphs.filter(g=>g.crossStart<-.5||g.crossEnd>crossExtent+.5).length,
   crossExamples:glyphs.filter(g=>g.crossStart<-.5||g.crossEnd>crossExtent+.5).slice(0,12),
   chapterStarts:[...new Set(['ch1','ch2',...document.querySelectorAll('#source [data-chapter-start]')].map(node=>typeof node==='string'?node:node.id))].map(id=>{const g=glyphs.find(g=>g.locator.element===id&&g.locator.tag==='H1');return {id,start:g?.start,page:g?pages.findIndex(p=>g.start>=p.start-.5&&g.end<=p.end+.5):-1};}),examples,
   boundaries:pages.map((p,i)=>({...p,index:i,first:glyphs.find(g=>g.start>=p.start-.5&&g.end<=p.end+.5)?.locator}))};
  return reportCache;
 }
 async function show(index){
  if(state.direction==='fixed'){state.currentIndex=0;return {index:0,start:0,first:null};}
  const p=pages[Math.max(0,Math.min(index,pages.length-1))];
  const src=document.getElementById('source'),view=document.getElementById('viewport');
  src.style.transform=state.axisX?`translateX(${state.reverseX?p.start:-p.start}px)`:`translateY(${-p.start}px)`;
  const blank=Math.max(0,state.extent-(p.end-p.start));
  view.style.clipPath=state.axisX?(state.reverseX?`inset(0 0 0 ${blank}px)`:`inset(0 ${blank}px 0 0)`):`inset(0 0 ${blank}px 0)`;
  // Page transforms use cached geometry; no layout measurement or frame wait is needed.
  state.currentIndex=index;
  return {index,start:p.start,first:report().boundaries[index]?.first};
 }
 function anchor(){
  if(state.direction==='fixed')return {kind:'fixed',spine:state.spine,href:state.href};
  return report().boundaries[state.currentIndex??0]?.first;
 }
 async function restore(locator){
  if(state.direction==='fixed'){
   const found=locator?.kind==='fixed'&&locator.spine===state.spine;
   return {found,visible:found,index:0};
  }
  const g=glyphs.find(g=>locator.cfi?g.locator.cfi===locator.cfi:g.locator.spine===locator.spine&&(locator.kind==='object'
   ?g.locator.kind==='object'&&g.locator.objectIndex===locator.objectIndex
   :g.locator.kind!=='object'&&g.locator.textIndex===locator.textIndex&&g.locator.offset===locator.offset));
  if(!g)return {found:false};
  const i=pages.findIndex(p=>g.start>=p.start-.5&&g.end<=p.end+.5);
  if(i<0)return {found:true,visible:false};
  await show(i);return {found:true,visible:true,index:i,locator};
 }
 function elementLocation(id,spine,edge='first'){
  const matching=glyphs.filter(g=>g.locator.element===id&&(spine===undefined||g.locator.spine===spine));
  const glyph=edge==='last'?matching.at(-1):matching[0];
  if(!glyph)return null;
  const page=pages.findIndex(p=>glyph.start>=p.start-.5&&glyph.end<=p.end+.5);
  return {page,start:glyph.start,end:glyph.end,crossStart:glyph.crossStart,crossEnd:glyph.crossEnd,withinPage:page<0?null:glyph.start-pages[page].start,locator:glyph.locator};
 }
 return {setup,report,show,anchor,restore,elementLocation};
})();
