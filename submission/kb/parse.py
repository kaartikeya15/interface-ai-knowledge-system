"""Turn one crawled page into typed, dated evidence: page metadata and rate-table rows."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional
from urllib.parse import urlparse

from . import site_profile as site

# Higher wins when two sources state the same fact on the same effective date.
AUTHORITY = {"rate_sheet": 5, "product": 4, "news": 2, "file": 2, "landing": 2, "blog": 1, "calculator": 0}

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
BYLINE_DATE = re.compile(rf"^\s*((?:{MONTHS}) \d{{1,2}}, \d{{4}})\s*$", re.M)
EFFECTIVE = re.compile(r"effective (?:as of )?(\d{1,2}/\d{1,2}/\d{2,4})", re.I)
PCT = re.compile(r"\d+(?:\.\d+)?%")
SEPARATOR = re.compile(r"^\|[\s:\-|]+\|$")


def path_of(url: str) -> str:
    return urlparse(url).path.rstrip("/").lower() or "/"


def page_kind(url: str) -> str:
    p = path_of(url)
    if p in site.RATE_SHEET_PATHS:
        return "rate_sheet"
    if "calculator" in p:
        return "calculator"
    if p.startswith(site.NEWS_PREFIXES):
        return "news"
    if p.startswith(site.BLOG_PREFIXES) or re.match(r"/20\d\d/", p):
        return "blog"
    if p.startswith(site.FILE_PREFIXES):
        return "file"
    if p.endswith(site.LANDING_SUFFIX) or p in site.LANDING_PATHS:
        return "landing"
    return "product"


def published_on(markdown: str) -> Optional[date]:
    """Articles carry a byline date on its own line near the top; body dates must not count."""
    m = BYLINE_DATE.search(markdown[:800])
    return datetime.strptime(m.group(1), "%B %d, %Y").date() if m else None


def parse_us_date(s: str) -> Optional[date]:
    mth, day, year = (int(x) for x in s.split("/"))
    try:
        return date(year + 2000 if year < 100 else year, mth, day)
    except ValueError:
        return None


@dataclass
class Row:
    """One row of a published rate table."""
    url: str
    kind: str
    family: str                 # certificate, ira, deposit, auto, mortgage, card, ...
    business: bool
    title: str                  # the heading the table sits under
    label: str                  # what the row describes
    cells: dict
    effective: Optional[date]
    key: str = ""               # same key on two pages = same fact
    tier: str = ""
    account: str = ""
    values: dict = field(default_factory=dict)   # metric -> "4.20%" or "12.50%-19.50%"
    quote: str = ""


@dataclass
class LineFact:
    """A rate stated in a sentence: '6-month certificate APY: 3.50%'."""
    url: str
    kind: str
    label: str
    value: str
    expired: bool
    quote: str


LINE_FACT = re.compile(r"^\s*[-*]?\s*(?:[^:\n]{0,60}:\s*)?(?P<label>[A-Za-z0-9][^:\n]{2,80}?)\s*:?\s+"
                       r"(?P<value>\d+(?:\.\d+)?%)(?P<rest>[^\n]*)$", re.M)
ENDED = re.compile(r"\b(?:offer |promotion )?(?:ended|expired|no longer available)\b", re.I)


def extract_line_facts(url: str, markdown: str) -> list[LineFact]:
    """Rates stated in prose lines. Table rows are handled by extract_rows; lines starting with '|' are skipped."""
    out = []
    for m in LINE_FACT.finditer(markdown):
        line = m.group(0).strip()
        if line.startswith("|") or len(line) > 200:
            continue
        label = re.sub(r"\s+", " ", m.group("label")).strip()
        out.append(LineFact(url, page_kind(url), label, m.group("value"), bool(ENDED.search(m.group("rest"))), line))
    return out


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _clean_header(h: str) -> str:
    return re.sub(r"[\d\\*]+$", "", h.replace("\\*", "")).strip()


def _value(cell: str) -> Optional[str]:
    found = PCT.findall(cell)
    if not found and re.fullmatch(r"\d+\.\d+", cell.strip()):   # some older rows drop the percent sign
        found = [cell.strip() + "%"]
    if not found:
        return None
    is_range = len(found) > 1 and re.search(r"%\s*(?:APR\s*)?[-–—]\s*\d", cell)   # "12.50% - 19.50%", not "2.90%, then 12.50%"
    return "-".join(found[:2]) if is_range else found[0]


def _metric(header: str) -> Optional[str]:
    h = header.lower()
    if "yield" in h or "apy" in h:
        return "APY"
    if "annual percentage rate" in h or "apr" in h:
        return "APR"
    if "dividend" in h:
        return "dividend rate"
    if h == "rate":
        return "rate"
    return None


def _family(headers: list[str], context: list[str], first_cell: str) -> Optional[str]:
    """Table layouts changed between crawls, so read the headers and the nearby titles together."""
    first = headers[0].lower()
    joined = " ".join(headers).lower()
    title = context[0].lower() if context else ""
    if "points" in joined:
        return "mortgage"
    if first == "ira type" or re.search(r"\biras?\b", title) or re.match(r"(roth|traditional):", first_cell.lower()):
        return "ira"
    if "visa" in first or "credit card" in first or first == "card type":
        return "card"
    if first == "certificate" or (first == "term" and "dividend" in joined):
        return "certificate"
    if first in ("account type", "balance tier", "balance"):
        return "deposit"
    for pattern, family in ((r"home equity|heloc", "home_equity"), (r"home improvement", "home_improvement"),
                            (r"personal", "personal"), (r"boat|atv|snowmobile|recreational", "recreational"),
                            (r"auto", "auto")):
        if re.search(pattern, title):
            return family
    if first == "loan program":
        return "auto"
    return None


def _account_name(cells: dict, lines: list[str], i: int) -> str:
    if cells.get("Account Type"):
        return cells["Account Type"]
    for j in range(i - 1, max(-1, i - 60), -1):
        low = lines[j].lower()
        if len(low) < 90:
            for pattern, name in site.TABLE_ACCOUNT_NAMES:
                if re.search(pattern, low):
                    return name
    return ""


def _norm_key(s: str) -> str:
    s = s.lower().replace(",", "").replace("–", "-").replace("—", "-")
    s = re.sub(r"\.00\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _titles_before(lines: list[str], i: int) -> list[str]:
    """Tables have no heading markup here; the nearest short plain lines are their titles."""
    found = []
    for j in range(i - 1, max(-1, i - 45), -1):
        t = lines[j].strip().lstrip("#").strip()
        if not t or t.startswith(("[", "- ", "|", "!", "\\", "_", "*")) or len(t) > 70:
            continue
        if t[0].isdigit() or t.lower().startswith(("rates are", "apy")) or t.endswith("."):
            continue
        found.append(t)
        if len(found) == 3:
            break
    return found


def extract_rows(url: str, markdown: str) -> list[Row]:
    kind = page_kind(url)
    if kind in ("calculator", "file"):
        return []
    lines = markdown.split("\n")
    rows: list[Row] = []
    i = 0
    while i < len(lines) - 1:
        if not (lines[i].startswith("|") and SEPARATOR.match(lines[i + 1].strip())):
            i += 1
            continue
        headers = [_clean_header(c) for c in _cells(lines[i])]
        end = i + 2
        while end < len(lines) and lines[end].startswith("|"):
            end += 1
        metrics = {n: _metric(h) for n, h in enumerate(headers)}
        context = _titles_before(lines, i)
        title = context[0] if context else ""
        first_cell = _cells(lines[i + 2])[0] if i + 2 < end else ""
        fam = _family(headers, context, first_cell) if any(metrics.values()) else None
        if fam:
            effective = None
            for after in lines[end:end + 6]:
                m = EFFECTIVE.search(after)
                if m:
                    effective = parse_us_date(m.group(1))
                    break
            for raw in lines[i + 2:end]:
                cells = _cells(raw)
                if len(cells) != len(headers):
                    continue
                values = {metrics[n]: _value(c) for n, c in enumerate(cells) if metrics[n] and _value(c)}
                if not values:
                    continue
                named = dict(zip(headers, cells))
                if fam == "card" and "intro" in cells[0].lower():
                    values = {k: v.split("-")[0] for k, v in values.items()}   # older rows append the go-to rate
                desc = [c for n, c in enumerate(cells) if not metrics[n] and _is_descriptor(headers[n])]
                label = " ".join(desc)
                account = ""
                tier = ""
                if fam == "deposit":
                    account = _account_name(named, lines, i)
                    tier = named.get("Balance Tier") or named.get("Balance") or ""
                    label = f"{account} {tier}".strip()
                business = "/business" in path_of(url) or "business" in (title + " " + label).lower()
                key_scope = "business" if business else "personal"
                key_title = re.sub(r"s$", "", title) if fam in ("personal", "home_equity", "recreational") else ""
                key_label = label
                if fam == "card":
                    key_title = title
                    key_label = "intro" if "intro" in label.lower() else "standard"
                rows.append(Row(url=url, kind=kind, family=fam, business=business, title=title, label=label, cells=named,
                                effective=effective, tier=tier, account=account, values=values,
                                key=_norm_key(f"{fam}|{key_scope}|{key_title}|{key_label}"), quote=raw.strip()[:200]))
        i = end
    return rows


def _is_descriptor(header: str) -> bool:
    return not re.search(r"min\.|payment|points", header, re.I)
