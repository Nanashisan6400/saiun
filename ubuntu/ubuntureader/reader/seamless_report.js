 function report(){
  if(reportCache)return {...reportCache,...state};
  const counts=new Map(),firsts=new Map();let cuts=0;const examples=[];
  const step=state.extent+(state.columnGap??(state.method==='columns'?2*(state.size??20):0)),origin=pages[0]?.start??0;
  for(const g of glyphs){
   const start=Math.max(0,Math.floor((g.start-origin)/step)-1),end=Math.min(pages.length-1,Math.floor((g.end-origin)/step)+1);
   for(let index=start;index<=end;index++){
    const p=pages[index],overlap=Math.min(g.end,p.end)-Math.max(g.start,p.start);
    if(overlap>.25){counts.set(g.id,(counts.get(g.id)||0)+1);if(g.start<p.start-.5||g.end>p.end+.5){cuts++;if(examples.length<12)examples.push({page:index,start:g.start,end:g.end});}}
    if(g.start>=p.start-.5&&g.end<=p.end+.5){const previous=firsts.get(index);if(!previous||(g.order??g.id)<(previous.order??previous.id)||((g.order??g.id)===(previous.order??previous.id)&&(g.offset??0)<(previous.offset??0)))firsts.set(index,g);}
   }
  }
  const crossExtent=state.axisX?state.H-2*state.pad:state.W-2*state.pad;
  // Conservative retained-data accounting: glyph/locator records, DOM,
  // text/CSS and decoded images. Browser/runtime baseline is outside this cache.
  const source=document.getElementById('source');
  const decoded=[...source.querySelectorAll('img')].reduce((sum,img)=>sum+(img.naturalWidth||0)*(img.naturalHeight||0)*4,0);
  state.cacheBytes=glyphs.length*1536+source.querySelectorAll('*').length*768+source.textContent.length*2+decoded+
   [...document.querySelectorAll('style')].reduce((sum,style)=>sum+style.textContent.length*2,0);
  reportCache={...state,cuts,missing:glyphs.filter(g=>!counts.has(g.id)).length,duplicate:glyphs.filter(g=>(counts.get(g.id)||0)>1).length,crossOverflow:glyphs.filter(g=>g.crossStart<-.5||g.crossEnd>crossExtent+.5).length,
   examples,crossExamples:[],chapterStarts:[],boundaries:pages.map((p,index)=>({...p,index,first:firsts.get(index)?.locator}))};
  return reportCache;
 }
