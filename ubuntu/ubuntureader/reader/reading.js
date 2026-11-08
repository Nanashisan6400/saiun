/* Only application code runs here. The EPUB document supplies sanitized markup. */
window.reading = (() => {
    const firstPart = document.querySelector('#source [data-reader-part]') || document.getElementById('source');
    const nativeDirection = getComputedStyle(firstPart).writingMode.startsWith('vertical') ? 'vertical' : 'horizontal';
    const authorFonts = new Map([document.getElementById('source'), ...document.querySelectorAll('#source *')].map(node => [node, getComputedStyle(node).fontFamily]));
    const authorBreaks = new Map([document.getElementById('source'), ...document.querySelectorAll('#source *')].map(node => [node, {before:getComputedStyle(node).breakBefore,after:getComputedStyle(node).breakAfter}]));
    function theme(dark) {
        const foreground = dark ? '#e8e6e3' : '#202124';
        const background = dark ? '#202124' : '#faf9f6';
        document.body.style.setProperty('background-color', background, 'important');
        document.body.style.setProperty('color', foreground, 'important');
        if (probe.report().direction === 'fixed') return;
        const source = document.getElementById('source');
        for (const node of [source, ...source.querySelectorAll('*')]) {
            node.style.setProperty('color', foreground, 'important');
            node.style.setProperty('background-color', 'transparent', 'important');
        }
    }
    function partFor(spine) {
        const source = document.getElementById('source');
        if (Number(source.dataset.spine) === spine) return source;
        return [...source.querySelectorAll('[data-spine]')].find(node => Number(node.dataset.spine) === spine) || source;
    }
    function units(root) {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
        const entries = [];
        let node, total = 0, imageIndex = Number(root.dataset.readerImageOffset || 0);
        while (node = walker.nextNode()) {
            if (node.nodeType === Node.TEXT_NODE) {
                entries.push({node,start:total,length:node.length});
                total += node.length;
            } else if (node.tagName === 'IMG') {
                entries.push({node,start:total,length:1,imageIndex:imageIndex++});
                total++;
            }
        }
        return {entries,total};
    }
    function sectionBounds(spine) {
        const root = partFor(spine);
        return {start:Number(root.dataset.readerStart || 0), end:Number(root.dataset.readerEnd || 1)};
    }
    function wholeProgress(spine, local) {
        const {start,end} = sectionBounds(spine);
        return start+(end-start)*local;
    }
    const progressIndexes = new Map();
    function progression(anchor) {
        let index = progressIndexes.get(anchor.spine);
        if (!index) {
            const {entries,total} = units(partFor(anchor.spine));
            index = {total,texts:new Map(),images:new Map()};
            for (const entry of entries) {
                if (entry.imageIndex !== undefined) index.images.set(entry.imageIndex,entry.start);
                else {
                    const location = verticalTypography.locate(entry.node,0);
                    if (!location) continue;
                    const ranges = index.texts.get(location.textIndex) || [];
                    ranges.push({offset:location.offset,length:entry.length,start:entry.start});
                    index.texts.set(location.textIndex,ranges);
                }
            }
            progressIndexes.set(anchor.spine,index);
        }
        if (!index.total) return wholeProgress(anchor.spine,0);
        if (anchor.kind === 'object') return wholeProgress(anchor.spine,(index.images.get(anchor.objectIndex) || 0)/index.total);
        const range = (index.texts.get(anchor.textIndex) || []).find(item => anchor.offset >= item.offset && anchor.offset < item.offset+item.length);
        return wholeProgress(anchor.spine,range ? (range.start+anchor.offset-range.offset)/index.total : 0);
    }
    async function restoreProgression(locator) {
        const spine = locator?.spine_index ?? locator?.spine ?? Number(firstPart.dataset.spine);
        const {entries,total} = units(partFor(spine));
        const {start,end} = sectionBounds(spine);
        const local = ((locator?.progression || 0)-start)/(end-start || 1);
        const position = Math.floor(Math.max(0,Math.min(1,local))*Math.max(0,total-1));
        let entry = entries.find(item => item.start+item.length > position);
        if (!entry) return null;
        const visible = item => {
            if(item.imageIndex!==undefined)return item.node.getClientRects().length>0;
            if(!item.node.data.trim())return false;
            const range=document.createRange();range.selectNodeContents(item.node);
            return [...range.getClientRects()].some(rect=>rect.width>0&&rect.height>0);
        };
        if(!visible(entry)){
            const index=entries.indexOf(entry);
            entry=entries.slice(index+1).find(visible)||entries.slice(0,index).reverse().find(visible);
            if(!entry)return null;
        }
        let target;
        if (entry.imageIndex !== undefined) target = {kind:'object',objectIndex:entry.imageIndex,spine};
        else {
            let offset = Math.max(0,Math.min(entry.node.length-1,position-entry.start));
            if (/\s/u.test(entry.node.data[offset])) {
                const next = entry.node.data.slice(offset).search(/\S/u);
                offset = next < 0 ? Math.max(0,entry.node.data.search(/\S/u)) : offset+next;
            }
            if (offset && /[\uDC00-\uDFFF]/u.test(entry.node.data[offset])) offset--;
            target = verticalTypography.locate(entry.node,offset);
        }
        if (!target) return null;
        return {...await probe.restore(target),anchor:target,progression:progression(target)};
    }
    function result(restored = null) {
        const report = probe.report();
        const anchor = probe.anchor() || null;
        return {anchor, progression:anchor && report.direction !== 'fixed' ? progression(anchor) : 0, page:report.currentIndex, pages:report.pages,
                direction:report.direction, restored,
                zoom:report.zoom, fitWidth:report.fitWidth, fitHeight:report.fitHeight,
                fixedBounds:report.fixedBounds, panX:report.panX, panY:report.panY,
                geometry:{cuts:report.cuts,missing:report.missing,duplicate:report.duplicate,crossOverflow:report.crossOverflow}};
    }
    async function seek(locator) {
        if (probe.report().direction === 'fixed') {
            await probe.show(0);
            return {found:true,visible:true,index:0,anchor:probe.anchor(),progression:0};
        }
        let restored = null;
        let targetAnchor = locator;
        if (locator?.cfi || ['object', 'fixed'].includes(locator?.kind)) restored = await probe.restore(locator);
        if (!restored?.visible && locator?.fragment) {
            const root = partFor(locator.spine_index ?? locator.spine);
            const target = [...root.querySelectorAll('[id],a[name]')].find(node => node.id === locator.fragment || node.tagName === 'A' && node.getAttribute('name') === locator.fragment);
            if (target) {
                const {entries} = units(root);
                const visible = entry => entry.imageIndex !== undefined || entry.node.data.trim();
                let entry = entries.find(entry => visible(entry) && (target.contains(entry.node) || target.compareDocumentPosition(entry.node) & Node.DOCUMENT_POSITION_FOLLOWING));
                if (!entry) entry = [...entries].reverse().find(visible);
                if (entry) {
                    targetAnchor = entry.imageIndex !== undefined ? {kind:'object',objectIndex:entry.imageIndex,spine:locator.spine_index ?? locator.spine} : verticalTypography.locate(entry.node,Math.max(0,entry.node.data.search(/\S/u)));
                    restored = await probe.restore(targetAnchor);
                }
            }
        }
        if (!restored?.visible) restored = await restoreProgression(locator);
        if (!restored?.visible) {
            const boundary = (probe.report().boundaries || []).findIndex(page => page.first?.spine === (locator?.spine_index ?? locator?.spine));
            const page = boundary < 0 ? 0 : boundary;
            await probe.show(page);
            targetAnchor = probe.report().boundaries[page]?.first || probe.anchor();
            restored = {visible:true,index:page,fallback:true};
        }
        if (probe.report().direction === 'fixed') targetAnchor = probe.anchor();
        return {...restored,anchor:restored?.anchor || targetAnchor,progression:restored?.progression ?? (targetAnchor ? progression(targetAnchor) : 0)};
    }
    async function layout(options, locator, last, dark) {
        document.documentElement.removeAttribute('data-reader-ready');
        window.readerDark = dark;
        const source = document.getElementById('source');
        const original = options.font === 'original';
        const family = verticalTypography.normalizeFamily(original ? authorFonts.get(firstPart) : options.font);
        if (options.direction !== 'fixed') {
            for (const node of [source, ...source.querySelectorAll('*')]) {
                const chosen = original ? authorFonts.get(node) : family;
                if (chosen) node.style.setProperty('font-family', verticalTypography.normalizeFamily(chosen), 'important');
                else node.style.removeProperty('font-family');
            }
        }
        if (original || options.direction === 'fixed') {
            await Promise.race([
                Promise.all([...document.fonts].map(face => face.load())),
                new Promise((_, reject) => setTimeout(() => reject(new Error('書籍のフォントを読み込めませんでした。')), 10000))
            ]);
        }
        const direction = options.direction === 'original' ? nativeDirection : options.direction;
        if (direction !== 'fixed') {
            for (const [node,breaks] of authorBreaks) {
                if (node.hasAttribute('data-reader-chapter')) continue;
                for (const side of ['before','after']) {
                    let value = breaks[side];
                    if (['page','left','right','recto','verso','always'].includes(value)) value = 'column';
                    node.style.setProperty('break-'+side,value,'important');
                }
            }
        }
        await probe.setup({...options, font:family, direction, chapterBreaks:false});
        if (options.direction === 'fixed') await probe.show(0);
        const restored = locator ? await seek(locator) : null;
        if (!locator) await probe.show(last ? probe.report().pages-1 : 0);
        if (options.pageStep === 2) await probe.show(Math.floor(probe.report().currentIndex/2)*2);
        theme(window.readerDark);
        document.getElementById('viewport').style.outline = 'none';
        if (!options.deferReady) document.documentElement.dataset.readerReady = '';
        return result(restored);
    }
    async function show(page) { await probe.show(page); return result(); }
    return {layout,show,theme,result};
})();
