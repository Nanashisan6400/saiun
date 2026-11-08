"""Locate the current TOC entry in original document order, independent of pages."""
import re
from .sections import paths


def current_entry(book, locator):
    if not book.chapters:
        return None
    locator = locator or {}
    spine = locator.get('spine_index', locator.get('spine', 0))
    cache = getattr(book, '_contents_paths', None)
    if cache is None:
        cache = book._contents_paths = {}
    if spine not in cache:
        cache[spine] = {node.get('id') or node.get('name'): path
                        for node, path in paths(book.xml(book.spine[spine])).items()
                        if node.get('id') or node.get('name')}
    identifiers = cache[spine]
    cfi = locator.get('cfi') or ''
    position = (tuple(int(n) for n in re.findall(r'/([0-9]+)', cfi.split('!',1)[1].split(':',1)[0]))
                if '!' in cfi else identifiers.get(locator.get('fragment') or locator.get('element')))
    candidates = []
    for i, chapter in enumerate(book.chapters):
        index = chapter['spine']
        fragment = chapter.get('fragment')
        if index < spine:
            candidates.append(((index, ()), i))
        elif index == spine:
            start = identifiers.get(fragment) if fragment else ()
            if start is not None and (position is None or start <= position):
                if position is None and fragment:
                    plan = book.section_plan(spine)
                    unit = plan.ids.get(fragment) if plan else None
                    if unit is None or plan.starts[unit]/plan.total > (locator.get('progression') or 0):
                        continue
                candidates.append(((index, start), i))
    return max(candidates)[1] if candidates else 0
