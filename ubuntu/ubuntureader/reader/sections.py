"""Display-only ranges; source spine and XML CFI paths never change."""
from dataclasses import dataclass
import re

from ..stage0.sample_epub import BLOCKED

HARD_CHARS = 250_000
HARD_ELEMENTS = 20_000
CONTAINERS = {'body', 'div', 'section', 'article', 'main'}


def local(node):
    return node.tag.rsplit('}', 1)[-1].lower()


def paths(root):
    result = {}
    def visit(node, path=()):
        result[node] = path
        for i, child in enumerate(node):
            visit(child, path+(2*(i+1),))
    visit(root)
    return result


def atoms(body):
    result = []
    def visit(node):
        # Containers with mixed text are indivisible, so inline/ruby text stays together.
        children = list(node)
        if (local(node) in CONTAINERS and children and not (node.text or '').strip()
                and not any((child.tail or '').strip() for child in children)):
            for child in children:
                visit(child)
        else:
            result.append(node)
    visit(body)
    return result


@dataclass
class SectionPlan:
    ranges: list
    unit_paths: list
    ids: dict
    starts: list
    weights: list
    total: int
    images: list

    def choose(self, locator=None):
        locator = locator or {}
        cfi = locator.get('cfi') or ''
        if '!' in cfi:
            content = cfi.split('!', 1)[1].split(':', 1)[0]
            steps = tuple(int(x) for x in re.findall(r'/([0-9]+)', content))
            # The final odd CFI step identifies a text slot.
            for unit, path in enumerate(self.unit_paths):
                if steps[:len(path)] == path:
                    return self.for_unit(unit)
        fragment = locator.get('fragment') or locator.get('element')
        if fragment in self.ids:
            return self.for_unit(self.ids[fragment])
        if locator.get('kind') == 'object':
            image = locator.get('objectIndex', 0)
            offset = 0
            for unit, count in enumerate(self.images):
                if offset <= image < offset+count:
                    return self.for_unit(unit)
                offset += count
        position = (locator.get('progression') or 0)*self.total
        for part, (start, end) in enumerate(self.ranges):
            if position < self.starts[end-1]+self.weights[end-1] or part == len(self.ranges)-1:
                return part
        return 0

    def for_unit(self, unit):
        return next(i for i, (start, end) in enumerate(self.ranges) if start <= unit < end)

    def bounds(self, part):
        start, end = self.ranges[part]
        begin = self.starts[start]/self.total
        finish = (self.starts[end-1]+self.weights[end-1])/self.total
        return begin, finish, sum(self.images[:start])


def make_plan(root, chapter_ids=()):
    body = next(e for e in root.iter() if local(e) == 'body')
    all_paths = paths(root)
    units = atoms(body)
    costs, weights, images, ids, chapters = [], [], [], {}, set()
    for i, node in enumerate(units):
        chars = sum(len(t) for t in node.itertext())+len(node.tail or '')
        elements = sum(1 for _ in node.iter())
        if chars > HARD_CHARS or elements > HARD_ELEMENTS:
            raise ValueError('一つの段落または表が大きすぎるため分割できません。')
        costs.append((chars, elements))
        image_count = sum(local(e) in {'img','svg'} for e in node.iter())
        images.append(image_count)
        weights.append(max(1, sum(len(t.encode('utf-16-le'))//2 for t in node.itertext())+image_count))
        if any(local(e) in {'h1','h2'} or e.get('id') in chapter_ids for e in node.iter()):
            chapters.add(i)
        for e in node.iter():
            for value in (e.get('id'), e.get('name') if local(e) == 'a' else None):
                if value:
                    ids.setdefault(value, i)
    # Ancestor anchors select their first descendant unit.
    for ancestor in body.iter():
        if ancestor.get('id') and ancestor.get('id') not in ids:
            path = all_paths[ancestor]
            for i, node in enumerate(units):
                if all_paths[node][:len(path)] == path:
                    ids[ancestor.get('id')] = i
                    break
    # Each chapter is a separate load, including short chapters. Only an
    # oversized chapter needs a further paragraph cut at the hard safety limit.
    chapters.update(ids[value] for value in chapter_ids if value in ids)
    def has_content(node):
        if local(node) in {'img','svg'}:
            return True
        if local(node) in BLOCKED or node.get('hidden') is not None:
            return False
        return bool((node.text or '').strip()) or any(has_content(child) or (child.tail or '').strip() for child in node)
    first_content = next((i for i, node in enumerate(units) if has_content(node)), 0)
    boundaries = sorted({0, len(units)} | {c for c in chapters if c > first_content})
    ranges = []
    for chapter_start, chapter_end in zip(boundaries, boundaries[1:]):
        start = chapter_start
        while start < chapter_end:
            chars = elements = 0
            end = start
            while end < chapter_end:
                c, e = costs[end]
                if end > start and (chars+c > HARD_CHARS or elements+e > HARD_ELEMENTS):
                    break
                chars += c
                elements += e
                end += 1
            ranges.append((start, end))
            start = end
    starts, total = [], 0
    for weight in weights:
        starts.append(total)
        total += weight
    return SectionPlan(ranges, [all_paths[n] for n in units], ids, starts, weights, max(1,total), images)


def select_body(root, body, plan, part):
    """Capture original nodes, then prune after provenance annotations are inserted."""
    lookup = {path: node for node, path in paths(root).items()}
    start, end = plan.ranges[part]
    selected = {lookup[path] for path in plan.unit_paths[start:end]}
    keep = set(selected)
    first, last = {}, {}
    for unit, path in enumerate(plan.unit_paths):
        for length in range(len(path)+1):
            prefix = path[:length]
            first.setdefault(prefix, unit)
            last[prefix] = unit
    allowed_text = set()
    for path, node in lookup.items():
        if node.text and path in first and start <= first[path] < end:
            allowed_text.add(''.join('/'+str(n) for n in path)+'/1')
        for position, child in enumerate(node):
            child_path = path+(2*(position+1),)
            if child.tail and child_path in last and start <= last[child_path] < end:
                allowed_text.add(''.join('/'+str(n) for n in path)+f'/{2*(position+1)+1}')
    for path in plan.unit_paths[start:end]:
        for length in range(len(path)+1):
            ancestor = lookup.get(path[:length])
            if ancestor is not None:
                keep.add(ancestor)
    def finish():
        def prune(node):
            if node in selected:
                return
            for child in list(node):
                if child not in keep and child.get('data-stage0-cfi') not in allowed_text:
                    node.remove(child)
                else:
                    prune(child)
        prune(body)
    return finish
