/* Diagnostic WebKitGTK workaround. Source text/codepoints are never changed.
 * Display-only spans retain original text node ordinal and UTF-16 offsets.
 * U+2014 and ASCII periods remain under ordinary mixed-orientation shaping.
 */
window.verticalTypography=(()=>{
 const targets=/[=\uff1d\u2015\u2025\u2026\u3001\u3002\u3008-\u3011\u3014-\u301f\u30fcぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ\u31f0-\u31ff\uff08\uff09\uff1c\uff1e\uff3b\uff3d\uff5b-\uff60]/u;
 const maps=new WeakMap(),roots=new WeakMap();
 const rubySegments=new Intl.Segmenter('ja',{granularity:'grapheme'});
 function normalizeFamily(family){
  if(/IPAex(Mincho|Gothic)|Noto (Serif|Sans) CJK JP/.test(family))return family;
  return family.replace(/-webkit-standard/g,'sans-serif')
   .replace(/\bsans-serif\b/g,'"IPAexGothic", sans-serif')
   .replace(/(?<!sans-)\bserif\b/g,'"IPAexMincho", serif');
 }
 function opticalOffset(text,style,family){
  const canvas=document.createElement('canvas'),context=canvas.getContext('2d');
  context.font=`${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${family}`;
  const metrics=context.measureText(text);
  const delta=((metrics.fontBoundingBoxAscent-metrics.fontBoundingBoxDescent)
   -(metrics.actualBoundingBoxAscent-metrics.actualBoundingBoxDescent))/2;
  return Number.isFinite(delta)&&Math.abs(delta)<=parseFloat(style.fontSize)*.25?delta:null;
 }
 function apply(root,angleMode='optical',latinMode='optical'){
  let state=roots.get(root);
  if(!state){
   const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT),nodes=[];
   let n,index=0;while(n=walker.nextNode())nodes.push([n,++index]);
   state={spans:[],sourceNodes:nodes.length};roots.set(root,state);
   const spineCounters=new Map();
   for(const [node,textIndex] of nodes){
    const source={textIndex,element:node.parentElement.closest('[id]')?.id??null,tag:node.parentElement.tagName};
    const owner=node.parentElement.closest('[data-spine]');
    const provenance=node.parentElement.closest('[data-stage0-cfi]');
    if(provenance&&owner?.dataset.stage0Base)source.cfiPath=owner.dataset.stage0Base+provenance.dataset.stage0Cfi;
    if(owner){
     source.spine=Number(owner.dataset.spine);
     source.href=owner.dataset.href;
     source.textIndex=(spineCounters.get(source.spine)||0)+1;
     spineCounters.set(source.spine,source.textIndex);
    }
    maps.set(node,{...source,base:0});
    const latinPattern='(?<![\\p{Script=Latin}\\p{M}0-9])[A-Za-z0-9]+(?![\\p{Script=Latin}\\p{M}0-9])';
    const pattern=new RegExp(latinPattern+'|'+targets.source,'gu');
    const rubyText=!!node.parentElement.closest('rt');
    const matches=rubyText?[...rubySegments.segment(node.data)].map(part=>({0:part.segment,index:part.index})):[...node.data.matchAll(pattern)];
    if(!matches.length)continue;
    const fragment=document.createDocumentFragment();let start=0,offset=0;
    function text(value,base,parent){const child=document.createTextNode(value);maps.set(child,{...source,base});parent.append(child);}
    for(const match of matches){
      const character=match[0];offset=match.index;
      if(offset>start)text(node.data.slice(start,offset),start,fragment);
      const span=document.createElement('span');span.dataset.stage0Typography='';
      if(rubyText)span.dataset.stage0RubyChar='';
      else if(/^[A-Za-z0-9]+$/.test(character))span.dataset.stage0Latin='true';
      text(character,offset,span);fragment.append(span);state.spans.push(span);start=offset+character.length;
    }
    if(start<node.length)text(node.data.slice(start),start,fragment);
    node.replaceWith(fragment);
   }
  }
  for(const span of state.spans){
   const style=getComputedStyle(span.parentElement);
   span.style.cssText='';
   if(span.hasAttribute('data-stage0-ruby-char')&&!targets.test(span.textContent))continue;
   if(!style.writingMode.startsWith('vertical')||style.textCombineUpright==='all'){
    if(span.hasAttribute('data-stage0-latin'))span.style.display='contents';
    continue;
   }
   if(span.hasAttribute('data-stage0-latin')){
    if(latinMode!=='native'){
     span.style.fontFamily=normalizeFamily(style.fontFamily);
     span.style.paddingTop='.06em';
     span.style.paddingLeft='.06em';
     if(latinMode==='optical'){
      const delta=opticalOffset(span.textContent,style,span.style.fontFamily);
      if(delta!==null){
       const fine=parseFloat(style.fontSize)*.03;
       span.style.position='relative';span.style.left=(delta+fine)+'px';span.style.top=fine+'px';
      }
     }
    }
    continue;
   }
   if(/[=＝]/u.test(span.textContent)){
    span.style.textOrientation='sideways';
    span.style.fontFeatureSettings='"vert" 0, "vrt2" 0';
    span.style.fontFamily=normalizeFamily(style.fontFamily);
    if(angleMode==='optical'){
     const delta=opticalOffset(span.textContent,style,span.style.fontFamily);
     if(delta!==null){
      span.style.position='relative';
      span.style.left=(delta+parseFloat(style.fontSize)*.04)+'px';
     }
    }
    continue;
   }
   if(/[＜＞]/u.test(span.textContent)){
    span.style.fontFamily=normalizeFamily(style.fontFamily);
    if(angleMode==='optical'){
     // A clockwise sideways glyph moves its vertical ink offset onto X.
     // Recenter using measured font bounds, without changing its advance.
     const delta=opticalOffset(span.textContent,style,span.style.fontFamily);
     if(delta!==null){
      const fine=parseFloat(style.fontSize)*.04;
      span.style.position='relative';span.style.left=(delta+fine)+'px';
      if(span.textContent==='＞')span.style.top=fine+'px';
     }
    }
    if(angleMode==='middle')span.style.verticalAlign='middle';
    continue;
   }
   // Feature activation alone double-rotates some characters: use upright only
   // for this limited set of glyphs that have Japanese vertical alternates.
   span.style.textOrientation='upright';
   span.style.fontFeatureSettings='"vert" 1, "vrt2" 0';
   span.style.fontFamily=normalizeFamily(style.fontFamily);
  }
  return {sourceNodes:state.sourceNodes,displaySpans:state.spans.length,angles:state.spans.filter(s=>/[＜＞]/u.test(s.textContent)).map(s=>({code:s.textContent.codePointAt(0),offsetX:parseFloat(s.style.left)||0})),fonts:[...new Set(state.spans.map(s=>getComputedStyle(s.parentElement).fontFamily))],first:state.spans.slice(0,3).map(s=>({code:s.textContent.codePointAt(0),font:getComputedStyle(s).fontFamily,features:getComputedStyle(s).fontFeatureSettings,orientation:getComputedStyle(s).textOrientation}))};
 }
 function locate(node,offset){
  const m=maps.get(node);
  if(!m)return null;
  const locator={textIndex:m.textIndex,offset:m.base+offset,element:m.element,tag:m.tag};
  if(m.cfiPath)locator.cfi=`epubcfi(${m.cfiPath}:${m.base+offset})`;
  if(m.spine!==undefined){locator.spine=m.spine;locator.href=m.href;}
  return locator;
 }
 function containOptical(root,bounds){
  // Relative optical adjustments must not push an otherwise fitting line out
  // of the physical viewport. Keep the requested offset on interior lines.
  for(const span of roots.get(root)?.spans??[]){
   if(span.style.position!=='relative')continue;
   const offset=parseFloat(span.style.left)||0;
   const rect=span.getBoundingClientRect();
   const originalLeft=rect.left-offset,originalRight=rect.right-offset;
   if(originalLeft>=bounds.left-.1&&originalRight<=bounds.right+.1){
    const limited=Math.max(bounds.left-originalLeft,Math.min(offset,bounds.right-originalRight));
    span.style.left=limited+'px';
   }
  }
 }
 return {apply,locate,normalizeFamily,containOptical};
})();
