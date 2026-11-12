 async function compactSetup(options){
  const value=await setup(options);
  if(state.direction!=='fixed'){
   const step=state.extent+(state.columnGap??0);
   pages=window.readerOccupiedPages(glyphs,pages[0]?.start??0,state.extent,step);
   state.pages=pages.length;reportCache=null;
   return report();
  }
  return value;
 }
 // Appending keeps the current transform: existing pages remain visible.
 async function extend(payload){
  const src=document.getElementById('source'),view=document.getElementById('viewport');
  const beforeCfi=glyphs.filter(g=>g.kind!=='object').at(-1)?.locator.cfi;
  const dirty=new Set(glyphs.filter(g=>g.end>=(pages[Math.max(0,pages.length-2)]?.start??0)-.5).map(g=>g.node));
  const shift=pages[state.currentIndex??0]?.start??0;
  const doc=new DOMParser().parseFromString(payload.html,'text/html');
  const incoming=doc.querySelector('[data-reader-part]');
  if(!incoming)throw Error('No publication section');
  const expectedText=src.textContent+incoming.textContent;
  const spine=incoming.dataset.spine;
  let target=src.querySelector('[data-reader-part="'+spine+'"]');
  const styleId='reader-stream-css-'+spine;
  let sheet=document.getElementById(styleId);
  if(!sheet){sheet=document.createElement('style');sheet.id=styleId;sheet.textContent=payload.css;document.head.append(sheet);}
  const markers=[],fresh=[];
  function collect(node){
   if(node.nodeType!==Node.ELEMENT_NODE)return;
   fresh.push(node,...node.querySelectorAll('*'));
   if(node.hasAttribute('data-stage0-cfi'))markers.push(node);
   markers.push(...node.querySelectorAll('[data-stage0-cfi]'));
  }
  function key(node){return node.nodeType===1?(node.hasAttribute('data-reader-node')?'n'+node.dataset.readerNode:node.hasAttribute('data-stage0-cfi')?'t'+node.dataset.stage0Cfi:null):null;}
  if(!target){
   target=document.importNode(incoming,true);src.append(target);collect(target);
   const natural=getComputedStyle(target).writingMode.startsWith('vertical')?'vertical':'horizontal';
   if(payload.direction==='original'&&natural!==state.direction){target.remove();sheet.remove();return {barrier:true};}
  }else{
   const existing=new Map([...target.querySelectorAll('[data-reader-node],[data-stage0-cfi]')].map(n=>[key(n),n]));
   function merge(parent,other){
    for(const child of other.childNodes){
     const identity=key(child),prior=identity&&existing.get(identity);
     if(prior){if(!identity.startsWith('t'))merge(prior,child);continue;}
     const added=document.importNode(child,true);parent.append(added);collect(added);
    }
   }
   merge(target,incoming);
   target.dataset.readerEnd=incoming.dataset.readerEnd;
  }
  target.dataset.readerDocEnd=incoming.dataset.readerDocEnd;
  readerAuthorBreaks(target);
  readerTextSettings(payload,fresh);
  target.style.setProperty('writing-mode',state.vertical?'vertical-rl':'horizontal-tb','important');
  const family=state.font,foreground=window.readerDark?'#e8e6e3':'#202124';
  const styles=fresh.map(node=>{const s=getComputedStyle(node);return [node,s.breakBefore,s.breakAfter];});
  for(const [node,before,after] of styles){
   if(payload.font!=='original')node.style.setProperty('font-family',family,'important');
   node.style.setProperty('color',foreground,'important');node.style.setProperty('background-color','transparent','important');
   for(const [side,value] of [['before',before],['after',after]])node.style.setProperty('break-'+side,['page','left','right','recto','verso','always'].includes(value)?'column':value,'important');
  }
  for(const image of fresh.filter(n=>n.tagName==='IMG')){
   image.style.setProperty('max-width',(state.W-2*state.pad)+'px','important');image.style.setProperty('max-height',(state.H-2*state.pad)+'px','important');image.style.objectFit='contain';image.style.breakInside='avoid';
   const parent=image.parentElement;
   if(!parent.textContent.trim()&&parent.querySelectorAll('img').length===1){
    let horizontal=0,vertical=0;
    for(let box=parent;box&&box!==src;box=box.parentElement){const s=getComputedStyle(box),positive=k=>Math.max(0,parseFloat(s[k])||0);horizontal+=positive('marginLeft')+positive('marginRight')+positive('paddingLeft')+positive('paddingRight');vertical+=positive('marginTop')+positive('marginBottom')+positive('paddingTop')+positive('paddingBottom');}
    image.style.setProperty('max-width',Math.max(1,state.W-2*state.pad-horizontal)+'px','important');image.style.setProperty('max-height',Math.max(1,state.H-2*state.pad-vertical)+'px','important');image.style.setProperty('display','block','important');image.style.setProperty('margin-inline','auto','important');
   }
  }
  const rubies=fresh.filter(n=>n.tagName==='RUBY');
  for(const ruby of rubies){
   ruby.style.setProperty('display','ruby','important');
   if(!ruby.parentElement.hasAttribute('data-reader-ruby-box')){const box=document.createElement('reader-ruby-box');box.setAttribute('data-reader-ruby-box','');box.style.display='inline-block';ruby.before(box);box.append(ruby);}
  }
  for(const marker of markers)verticalTypography.apply(marker);
  if(payload.font==='original')await Promise.all([...document.fonts].map(face=>face.load()));
  await document.fonts.ready;await frame();
  const spacing=[];
  for(const rt of fresh.filter(n=>n.tagName==='RT')){
   const units=[...rt.querySelectorAll('[data-stage0-ruby-char]')],chars=units.filter(n=>n.textContent.trim()),align=getComputedStyle(rt).rubyAlign;
   if(chars.length<2||!['space-around','space-between'].includes(align))continue;
   const b=rt.getBoundingClientRect(),available=state.vertical?b.height:b.width,natural=units.reduce((s,n)=>{const r=n.getBoundingClientRect();return s+(state.vertical?r.height:r.width);},0);
   if(available-natural>.5)spacing.push({chars,between:align==='space-between',space:(available-natural)/(chars.length-(align==='space-between'?1:0))});
  }
  for(const {chars,between,space} of spacing)for(const [i,char] of chars.entries()){char.style.paddingInlineStart=between&&i===0?'0':space/2+'px';char.style.paddingInlineEnd=between&&i===chars.length-1?'0':space/2+'px';}
  if(spacing.length)await frame();
  const overhang=[];
  for(const ruby of rubies){
   const box=ruby.parentElement,b=box.getBoundingClientRect(),rs=[...ruby.querySelectorAll('rt')].map(n=>n.getBoundingClientRect());
   if(rs.length)overhang.push([box,Math.max(0,...rs.map(r=>state.vertical?b.top-r.top:b.left-r.left)),Math.max(0,...rs.map(r=>state.vertical?r.bottom-b.bottom:r.right-b.right))]);
  }
  for(const [box,start,end] of overhang){if(start>.1)box.style.paddingInlineStart=Math.ceil(start)+'px';if(end>.1)box.style.paddingInlineEnd=Math.ceil(end)+'px';}
  if(overhang.some(([,s,e])=>s>.1||e>.1))await frame();
  if(state.vertical)for(const marker of markers)verticalTypography.containOptical(marker,view.getBoundingClientRect());
  const newMarkers=new Set(markers),vp=view.getBoundingClientRect();
  glyphs=glyphs.filter(g=>g.kind!=='object'&&!dirty.has(g.node));
  const walker=document.createTreeWalker(src,NodeFilter.SHOW_TEXT);let node;
  while(node=walker.nextNode()){
   if(!node.data.trim()||(!dirty.has(node)&&!newMarkers.has(node.parentElement.closest('[data-stage0-cfi]'))))continue;
   for(let offset=0;offset<node.length;){
    const length=node.data.codePointAt(offset)>65535?2:1,range=document.createRange();range.setStart(node,offset);range.setEnd(node,offset+length);
    for(const r of [...range.getClientRects()].filter(r=>r.width>0&&r.height>0))glyphs.push({node,offset,id:0,text:node.data.slice(offset,offset+length),
     start:(state.axisX?(state.reverseX?vp.right-r.right:r.left-vp.left):r.top-vp.top)+shift,end:(state.axisX?(state.reverseX?vp.right-r.left:r.right-vp.left):r.bottom-vp.top)+shift,
     crossStart:state.axisX?r.top-vp.top:r.left-vp.left,crossEnd:state.axisX?r.bottom-vp.top:r.right-vp.left,locator:verticalTypography.locate(node,offset)});
    offset+=length;
   }
  }
  const counters=new Map();
  for(const object of src.querySelectorAll('img,svg')){
   const r=object.getBoundingClientRect();if(!r.width||!r.height)continue;
   const owner=object.closest('[data-spine]'),spine=Number(owner.dataset.spine),index=counters.get(spine)??Number(owner.dataset.readerImageOffset||0);counters.set(spine,index+1);
   glyphs.push({node:object,offset:0,id:0,kind:'object',text:'',start:(state.axisX?(state.reverseX?vp.right-r.right:r.left-vp.left):r.top-vp.top)+shift,end:(state.axisX?(state.reverseX?vp.right-r.left:r.right-vp.left):r.bottom-vp.top)+shift,
    crossStart:state.axisX?r.top-vp.top:r.left-vp.left,crossEnd:state.axisX?r.bottom-vp.top:r.right-vp.left,locator:{kind:'object',objectIndex:index,spine,href:owner.dataset.href,element:object.id||null}});
  }
  const order=new Map(),all=document.createTreeWalker(src,NodeFilter.SHOW_TEXT|NodeFilter.SHOW_ELEMENT);let item,index=0;
  while(item=all.nextNode())order.set(item,index++);
  for(const [i,g] of glyphs.entries()){g.id=i;g.order=order.get(g.node)??i;}
  const max=glyphs.reduce((n,g)=>Math.max(n,g.end),0),step=state.extent+state.columnGap,origin=pages[0]?.start??0;
  // Chapter breaks can leave empty columns. Keep only occupied columns so
  // every page turn has a first-glyph anchor.
  pages=window.readerOccupiedPages(glyphs,origin,state.extent,step);
  state.pages=pages.length;state.totalGlyphs=glyphs.length;state.max=max;reportCache=null;
  if(src.textContent!==expectedText)throw Error('Publication text changed while appending');
  reading.refresh();
  const beforeGlyph=glyphs.find(g=>g.locator.cfi===beforeCfi),afterGlyph=glyphs.find(g=>g.kind!=='object'&&newMarkers.has(g.node.parentElement.closest('[data-stage0-cfi]')));
  const pageFor=g=>g?pages.findIndex(p=>g.start>=p.start-.5&&g.end<=p.end+.5):-1;
  window.seamRecords=window.seamRecords||[];
  window.seamRecords.push({beforePage:pageFor(beforeGlyph),afterPage:pageFor(afterGlyph),beforeSpine:beforeGlyph?.locator.spine,afterSpine:afterGlyph?.locator.spine,chapterStart:!!afterGlyph?.node.parentElement.closest('[data-reader-chapter-break]')});
  return reading.result();

 }
 function signature(){
  let hash=2166136261;
  const values=glyphs.map(g=>(g.locator.cfi||JSON.stringify(g.locator))+'|'+Math.round(g.start*64)+'|'+Math.round(g.end*64)+'|'+Math.round(g.crossStart*64)+'|'+Math.round(g.crossEnd*64)).sort();
  for(const value of values)for(let i=0;i<value.length;i++)hash=Math.imul(hash^value.charCodeAt(i),16777619);
  return {hash:hash>>>0,glyphs:glyphs.length,pages:pages.length};
 }
 function positions(){return glyphs.map(g=>({cfi:g.locator.cfi||JSON.stringify(g.locator),v:[g.start,g.end,g.crossStart,g.crossEnd]})).sort((a,b)=>a.cfi.localeCompare(b.cfi)||a.v[0]-b.v[0]);}
