"""One View = the website as it stood at one snapshot, with each fact resolved to a winner."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .parse import AUTHORITY, Row, extract_line_facts, extract_rows, page_kind, published_on
from .search import Index


@dataclass
class PageV:
    url: str
    title: str
    markdown: str
    fetched_at: str
    kind: str
    published: Optional[date]


@dataclass
class Resolved:
    """The winning row for a fact, the pages agreeing with it, and the stale copies it beat."""
    row: Row
    agreeing: list = field(default_factory=list)
    beaten: list = field(default_factory=list)


def rank(row: Row) -> tuple:
    """What counts as current: newest stated effective date, then the most authoritative page."""
    return (row.effective or date.min, AUTHORITY[row.kind], row.url)


class View:
    def __init__(self, snapshot):
        self.snapshot_id = snapshot.snapshot_id
        self.taken_on = date.fromisoformat(snapshot.taken_on)
        self.pages = {}
        rows = []
        self.line_facts = []
        self.skipped = []   # pages whose tables could not be parsed; the page stays searchable
        for p in snapshot.pages:
            text = (p.markdown or "").replace("\xa0", " ")   # non-breaking spaces hide table cells from every pattern
            self.pages[p.url] = PageV(p.url, p.title or "", text, p.fetched_at, page_kind(p.url), published_on(text))
            try:
                rows.extend(extract_rows(p.url, text))
                self.line_facts.extend(extract_line_facts(p.url, text))
            except Exception:   # one malformed page must not stop the snapshot from loading
                self.skipped.append(p.url)
        by_key = defaultdict(list)
        for r in rows:
            by_key[r.key].append(r)
        self.facts = {}
        self.retired = {}
        for key, group in by_key.items():
            group.sort(key=rank, reverse=True)
            # A promo page can outlive a tier the rate sheet dropped; such a row is history, not an offer.
            if all(r.kind == "landing" for r in group):
                self.retired[key] = group[0]
                continue
            win = group[0]
            self.facts[key] = Resolved(win, [r for r in group if r.values == win.values],
                                       [r for r in group if r.values != win.values])
        self.index = Index(self.pages.values())

    def rows(self, family: str, business: Optional[bool] = None) -> list[Resolved]:
        return [f for f in self.facts.values()
                if f.row.family == family and (business is None or f.row.business == business)]
