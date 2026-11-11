"""Find real contradictions and resolve them. One entry per product and term, so tiers never look like conflicts.

Four kinds of evidence:
  stale copy   the same table row disagrees across pages (keyed by product, tier and term)
  changed      a row's winning value moved between snapshots
  calculator   a calculator page still carries an old copy of a live table
  sibling      rows that should match do not (Roth vs Traditional of one certificate)
  claim        press boilerplate drifts over time (branch counts, counties served)
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from . import site_profile as site
from .common import Conflict, Source
from .rates import _describe, _months_of

CLAIMS = site.CLAIMS
ORDINALS = re.compile(r"\b(third|fourth|fifth|sixth|seventh) time\b", re.I)
CALC_ROW = site.CALC_ROW
SKIP_FAMILIES = {"home_improvement"}


@dataclass
class Found:
    """A conflict plus what merging needs: which product and term it belongs to, and each tier's live value."""
    conflict: Conflict
    group: tuple
    parts: list = field(default_factory=list)


def _same(a: str, b: str) -> bool:
    """20.5% and 20.50% are one value written two ways."""
    nums = lambda v: [float(x) for x in re.findall(r"\d+(?:\.\d+)?", v)]
    return nums(a) == nums(b)


def _add(values: list, new) -> None:
    for v in new:
        if not any(_same(v, seen) for seen in values):
            values.append(v)


def _topic(row) -> str:
    label = re.sub(r"\s+", " ", row.label)
    prefix = {"certificate": "certificate", "ira": "IRA", "auto": "auto loan", "mortgage": "mortgage", "card": "credit card",
              "deposit": "deposit account", "personal": "personal loan", "home_equity": "home equity"}.get(row.family, row.family)
    scope = "business " if row.business and "business" not in label.lower() else ""
    name = f"{row.title} {label}" if row.family in ("card", "home_equity", "personal", "recreational") else label
    return f"{scope}{prefix}: {name}"


def _row_conflicts(views) -> tuple[list[Found], dict]:
    latest = views[-1]
    history = defaultdict(list)
    for view in views:
        for key, fact in view.facts.items():
            if not history[key] or history[key][-1][1] != fact.row.values:
                history[key].append((view, fact.row.values, fact.row))
    out, seen_sig = [], set()
    # Personal certificates first, so business and IRA copies of the same move fold into them.
    order = {"certificate": 0, "deposit": 1, "ira": 2}
    facts = sorted(latest.facts.items(), key=lambda kv: (kv[1].row.business, order.get(kv[1].row.family, 1), kv[0]))
    for key, fact in facts:
        row = fact.row
        changes = history[key]
        if row.family in SKIP_FAMILIES or (len(changes) < 2 and not fact.beaten):
            continue
        values, sources = [], []
        for r in [row] + fact.beaten:
            _add(values, r.values.values())
            sources.append(Source(url=r.url, snapshot_id=latest.snapshot_id, quote=r.quote))
        for view, vals, r in changes[:-1]:
            _add(values, vals.values())
            sources.append(Source(url=r.url, snapshot_id=view.snapshot_id, quote=r.quote))
        if len(values) <= len(row.values):
            continue
        signature = tuple(sorted(values))
        if signature in seen_sig:     # business and personal copies of one change are one contradiction
            continue
        seen_sig.add(signature)
        if fact.beaten:
            stale = fact.beaten[0]
            reason = (f"{row.url} states an effective date of {row.effective}, newer than {stale.url} "
                      f"({stale.effective}); the newest effective date wins, then the official rate sheet.")
        else:
            reason = f"The value changed between snapshots; the latest snapshot ({latest.snapshot_id}) is current."
        c = Conflict(topic=_topic(row), values=values, sources=sources[:6], resolution=_describe(row), reason=reason)
        out.append(Found(c, _group(row.family, row.label, c.topic), [f"{row.label}: {_describe(row)}"]))
    return out, history


def _group(family: str, label: str, topic: str) -> tuple:
    """Tiers of one product and term (loan size, model year, purchase vs refinance) are one fact on the site."""
    if family == "mortgage":
        m = re.search(r"(\d+)-Year", label)
        return ("mortgage", int(m.group(1))) if m else ("mortgage", label)
    if family == "auto":
        return ("auto", _months_of(label))
    return (topic,)


TIER_WORD = {"mortgage": "loan size, purchase or refinance", "auto": "model years"}
GROUP_TOPIC = {"mortgage": "{}-year fixed mortgage rate", "auto": "{}-month auto loan APR"}


def _merge(items: list[Found]) -> list[Conflict]:
    groups = defaultdict(list)
    for f in items:
        groups[f.group].append(f)
    out = []
    for key, members in groups.items():
        if len(members) == 1 and len(key) == 1:
            out.append(members[0].conflict)
            continue
        values, sources, parts = [], [], []
        for m in members:   # live values first, then the stale ones they beat
            c = m.conflict
            _add(values, [v for v in c.values if any(_same(v, x) for x in re.findall(r"\d+\.\d+%", c.resolution))])
        for m in members:
            _add(values, m.conflict.values)
            sources.extend(s for s in m.conflict.sources if s.url not in {x.url for x in sources})
            parts.extend(p for p in m.parts if p not in parts)
        live = {v for m in members for v in m.conflict.values if any(_same(v, x) for x in re.findall(r"\d+\.\d+%", m.conflict.resolution))}
        stale = [v for v in values if not any(_same(v, x) for x in live)]
        reason = (f"Tiers ({TIER_WORD[key[0]]}) are separate facts, so each tier's current value is listed in the resolution. "
                  f"Superseded values: {', '.join(stale)}. " + members[0].conflict.reason)
        out.append(Conflict(topic=GROUP_TOPIC[key[0]].format(key[1]), values=values, sources=sources[:6],
                            resolution="; ".join(parts), reason=reason))
    return out


def _calculator(views) -> list[Found]:
    """A calculator's copy of the auto table: pair tiers by position within each term, newest first."""
    latest = views[-1]
    page = next((p for p in latest.pages.values() if p.kind == "calculator" and CALC_ROW.search(p.markdown)), None)
    if not page:
        return []
    live = defaultdict(list)
    for fact in latest.rows("auto"):
        live[_months_of(fact.row.label)].append(fact.row)
    stale = defaultdict(list)
    for m in CALC_ROW.finditer(page.markdown):
        stale[int(m.group(2))].append((m.group(1), m.group(3)))
    out = []
    for months, tiers in sorted(stale.items()):
        for year, old in tiers:
            # Tier boundaries shifted by a year ("2023-26" became "2024-27"), so match the nearest start year.
            row = next((r for r in live.get(months, []) if abs(int(r.label[:4]) - int(year)) <= 1), None)
            if row and not _same(old, row.values["APR"]):
                c = Conflict(
                    topic=f"{months}-month auto loan APR, {row.label.split(' Model')[0]} model years",
                    values=[row.values["APR"], old],
                    sources=[Source(url=row.url, snapshot_id=latest.snapshot_id, quote=row.quote),
                             Source(url=page.url, snapshot_id=latest.snapshot_id, quote=old)],
                    resolution=row.values["APR"],
                    reason="The rate table is authoritative; the calculator page keeps an old copy that was never updated.")
                out.append(Found(c, ("auto", months), [f"{row.label}: {row.values['APR']} APR"]))
    return out


def _siblings(views) -> list[Conflict]:
    latest = views[-1]
    groups = defaultdict(dict)
    for fact in latest.rows("ira"):
        m = re.match(r"(Roth|Traditional): (.*)", fact.row.label)
        if m:
            groups[m.group(2)][m.group(1)] = fact.row
    out = []
    for name, pair in groups.items():
        if len(pair) == 2 and pair["Roth"].values != pair["Traditional"].values:
            roth, trad = pair["Roth"], pair["Traditional"]
            newest = next((f.row for f in latest.rows("certificate") if name.split(" Special")[0] in f.row.label), roth)
            out.append(Conflict(
                topic=f"IRA {name}: Roth vs Traditional", values=[roth.values["APY"], trad.values["APY"]],
                sources=[Source(url=roth.url, snapshot_id=latest.snapshot_id, quote=roth.quote),
                         Source(url=trad.url, snapshot_id=latest.snapshot_id, quote=trad.quote)],
                resolution=newest.values.get("APY", roth.values["APY"]),
                reason="Both IRA types track the same certificate, whose current rate settles it; one row was not updated."))
    return out


def _claims(views) -> list[Conflict]:
    latest = views[-1]
    out = []
    for topic, pattern, template in CLAIMS:
        dated = []
        for page in latest.pages.values():
            m = pattern.search(page.markdown)
            if m and page.published:
                dated.append((page.published, m.group(1), page.url))
        dated.sort(reverse=True)
        values = list(dict.fromkeys(template.format(v) for _, v, _ in dated))
        if len(values) > 1:
            seen, sources = set(), []
            for when, value, url in dated:
                if value not in seen:
                    seen.add(value)
                    sources.append(Source(url=url, snapshot_id=latest.snapshot_id, quote=template.format(value)))
            out.append(Conflict(topic=topic, values=values, sources=sources, resolution=values[0],
                                reason=f"Press boilerplate changes over time; the newest dated release ({dated[0][0]}) is current."))
    ordinal = []
    for page in latest.pages.values():
        if page.published and site.RECOGNITION[0] in page.title.lower():
            m = ORDINALS.search(page.markdown)
            if m:
                ordinal.append((page.published, m.group(1).lower(), page.url))
    ordinal.sort(reverse=True)
    if len({o[1] for o in ordinal}) > 1:
        out.append(Conflict(topic=site.RECOGNITION[1], values=[o[1] for o in ordinal],
                            sources=[Source(url=o[2], snapshot_id=latest.snapshot_id) for o in ordinal],
                            resolution=ordinal[0][1], reason="The newest release supersedes earlier ones."))
    return out


def _prose(views) -> list[Conflict]:
    """Sentence-stated rates whose value moved, for products that have no rate table to settle them."""
    from .rates import FAMILIES
    latest = views[-1]
    tabled = {f.row.family for f in latest.facts.values()}
    families = [(fam, pat) for fam, pat in FAMILIES if fam not in tabled]
    seen = defaultdict(list)   # label -> [(view, fact)]
    for view in views:
        for f in view.line_facts:
            if any(re.search(pat, f.label.lower()) for _, pat in families):
                seen[re.sub(r"\s+", " ", f.label.lower())].append((view, f))
    out = []
    for label, items in sorted(seen.items()):
        live = [f for v, f in items if v is latest and not f.expired]
        values = []
        _add(values, [f.value for f in live] + [f.value for _, f in items])
        if not live or len(values) < 2:
            continue
        current = live[0]
        out.append(Conflict(topic=current.label, values=values,
                            sources=[Source(url=f.url, snapshot_id=v.snapshot_id, quote=f.quote) for v, f in items][:6],
                            resolution=current.value,
                            reason="The newest snapshot's live statement wins; older copies and expired offers are history."))
    return out


def _safely(finder, views) -> list:
    """Each detector stands alone: one that fails loses its own entries, not the whole list."""
    try:
        return finder(views)
    except Exception:
        return []


def find(views) -> list[Conflict]:
    if not views:
        return []
    rows = _safely(lambda v: _row_conflicts(v)[0], views)
    merged = _safely(lambda v: _merge(rows + _calculator(v)), views)
    # Order: claims and single facts first, then the merged rate groups.
    merged.sort(key=lambda c: (c.topic.split("-")[0].isdigit(), c.topic))
    return _safely(_claims, views) + _safely(_siblings, views) + merged + _safely(_prose, views)
