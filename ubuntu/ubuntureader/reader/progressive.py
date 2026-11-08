"""Isolated trial: bounded paragraph windows with the existing cached page renderer.

Normal launches never import this module. The EPUB, source CFIs and normal shelf
are unchanged. A window ends at an original node boundary, never inside ruby.
"""
from dataclasses import dataclass
import re
from .epub import EpubBook
from .sections import SectionPlan, paths, local

WINDOW_CHARS = 3000
WINDOW_ELEMENTS = 350


@dataclass
class WindowPlan(SectionPlan):
    slots: dict

    def choose(self, locator=None):
        cfi = (locator or {}).get('cfi') or ''
        if '!' in cfi:
            content = cfi.split('!',1)[1].split(':',1)[0]
            steps = tuple(int(n) for n in re.findall(r'/([0-9]+)',content))
            if steps in self.slots:
                return self.for_unit(self.slots[steps])
        return super().choose(locator)


def window_plan(root, chapter_plan):
    locations = paths(root)
    lookup = {path:node for node,path in locations.items()}
    body = next(node for node in root.iter() if local(node)=='body')
    units = []
    def refine(node):
        # Split a large paragraph only at original inline-node boundaries.
        # Each ruby stays intact, with its original ancestors and XML paths.
        large = sum(len(t) for t in node.itertext()) > WINDOW_CHARS or sum(1 for _ in node.iter()) > WINDOW_ELEMENTS
        if large and len(node) and local(node) in {'p','span','div','section','blockquote','li'}:
            for child in node:
                refine(child)
        else:
            units.append(node)
    for path in chapter_plan.unit_paths:
        refine(lookup[path])
    unit_paths = [locations[node] for node in units]
    unit_ids = {node:i for i,node in enumerate(units)}
    first,last = {},{}
    for i,path in enumerate(unit_paths):
        for length in range(len(path)+1):
            prefix = path[:length]
            first.setdefault(prefix,i)
            last[prefix]=i
    chars=[0]*len(units)
    weights=[0]*len(units)
    elements=[0]*len(units)
    images=[0]*len(units)
    readable=[False]*len(units)
    slots,ids = {},{}
    def text(value,slot,owner):
        if value and owner is not None:
            slots[slot]=owner
            chars[owner]+=len(value)
            weights[owner]+=len(value.encode('utf-16-le'))//2
            readable[owner] |= bool(value.strip())
    def assign(node,owner=None):
        path=locations[node]
        owner=unit_ids.get(node,owner)
        destination=owner if owner is not None else first.get(path)
        if destination is not None:
            elements[destination]+=1
            for value in (node.get('id'),node.get('name') if local(node)=='a' else None):
                if value:
                    ids.setdefault(value,destination)
            text(node.text,path+(1,),destination)
            if local(node) in {'img','svg'}:
                images[destination]+=1
                weights[destination]+=1
                readable[destination]=True
        for position,child in enumerate(node):
            assign(child,owner)
            child_path=locations[child]
            tail_owner=owner if owner is not None else last.get(child_path)
            text(child.tail,path+(2*(position+1)+1,),tail_owner)
    assign(body)
    chapters=sorted({0,len(units)} | {first[chapter_plan.unit_paths[start]] for start,end in chapter_plan.ranges})
    ranges=[]
    for chapter_start,chapter_end in zip(chapters,chapters[1:]):
        start=chapter_start
        while start<chapter_end:
            size=count=0
            end=start
            while end<chapter_end:
                if end>start and (size+chars[end]>WINDOW_CHARS or count+elements[end]>WINDOW_ELEMENTS):
                    break
                size+=chars[end];count+=elements[end];end+=1
            # Do not create a window consisting only of trailing blank markup.
            if not any(readable[start:end]) and ranges:
                ranges[-1]=(ranges[-1][0],end)
            else:
                ranges.append((start,end))
            start=end
    starts=[];total=0
    for i,weight in enumerate(weights):
        starts.append(total)
        weights[i]=max(1,weight)
        total+=weights[i]
    return WindowPlan(ranges,unit_paths,ids,starts,weights,max(1,total),images,slots)


class ProgressiveBook(EpubBook):
    def section_plan(self,index):
        if not hasattr(self,'window_plans'):
            self.window_plans={}
        if index not in self.window_plans:
            chapter_plan=super().section_plan(index)
            self.window_plans[index]=window_plan(self.xml(self.spine[index]),chapter_plan) if chapter_plan else None
        return self.window_plans[index]
