"""Facts that live outside rate tables: routing number, branches, hours, links, fees."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Optional

from . import site_profile as site
from .common import REFUSAL, Source, long_date, reply
from .parse import MONTHS, page_kind, path_of
from .rates import family_of
from .search import query_tokens, tokens

ROUTING = re.compile(r"routing\s*(?:number|#|no\.?)?\W{0,12}(\d{9})\b", re.I)
BRANCH_COUNT = site.BRANCH_COUNT
ADDRESS = re.compile(r"^(\d+ [^\n,]+(?:, [^\n,]+)?), ([A-Za-z .]+), ([A-Z]{2}),? (\d{5})\s*$", re.M)
MD_LINK = re.compile(r"\[([^\]]{2,80})\]\((https?://[^)\s]+)\)")
FEE_MIN_COVERAGE = 0.6   # a fee-schedule line must name most of what was asked, including its rarest word
SCHEDULE_DAY = r"(?:Mon|Tues?|Wed|Thu|Fri|Sat|Sun)[a-z]*"


def _src(view, url, quote=None):
    return Source(url=url, snapshot_id=view.snapshot_id, quote=(quote or "")[:200] or None)


def valid_aba(n: str) -> bool:
    d = [int(c) for c in n]
    return sum(w * x for w, x in zip([3, 7, 1] * 3, d)) % 10 == 0


def routing(q, view):
    if not re.search(r"\b(routing|aba|rtn)\b", q):
        return None
    found = defaultdict(list)
    for page in view.pages.values():
        for m in ROUTING.finditer(page.markdown):
            if valid_aba(m.group(1)):
                found[m.group(1)].append(page.url)
    if not found:
        return None
    number, urls = max(found.items(), key=lambda kv: (len(kv[1]), kv[0]))
    urls = sorted(set(urls), key=lambda u: (len(u), u))[:6]
    return reply(f"{site.NAME}'s routing number is {number}.", [_src(view, u, f"Routing Number: {number}") for u in urls])


def branch_count(q, view):
    if not re.search(r"\bhow many\b.*\b(branch|branches|locations|offices)\b|\bnumber of (branch |branches|locations)", q):
        return None
    dated = []
    for page in view.pages.values():
        m = BRANCH_COUNT.search(page.markdown)
        if m and page.published:
            dated.append((page.published, m.group(1), page.url))
    if not dated:
        return None
    dated.sort(reverse=True)
    when, count, url = dated[0]
    older = sorted({c for _, c, _ in dated if c != count})
    note = f"Older releases say {', '.join(older)}; the newest dated release wins." if older else None
    return reply(f"{site.NAME} has {count} branch office locations, per its most recent press release"
                 f" ({long_date(when)}).", [_src(view, url, f"{count} branch office locations")], conflict_note=note)


def branches(view) -> list[dict]:
    page = next((p for p in view.pages.values() if path_of(p.url) == site.CONTACT_PATH), None)
    if not page:
        return []
    out = []
    for m in ADDRESS.finditer(page.markdown):
        before = page.markdown[max(0, m.start() - 60):m.start()]
        if site.BRANCH_MARKER in before:
            out.append({"street": m.group(1), "city": m.group(2), "state": m.group(3), "zip": m.group(4), "url": page.url})
    return out


def _schedules(view, label: str) -> list[tuple[str, str]]:
    """Published schedules for 'Lobby' or 'Drive-up', newest release first."""
    found = []
    pat = re.compile(rf"\*\*{label}\*\*(.{{0,260}})", re.I | re.S)
    for page in sorted(view.pages.values(), key=lambda p: (p.published or date.min, p.url), reverse=True):
        for m in pat.finditer(page.markdown):
            body = m.group(1).split("**")[0]
            lines = [re.sub(r"\s+", " ", l).strip() for l in body.split("\n")]
            lines = [l for l in lines if re.match(SCHEDULE_DAY, l)]
            if lines:
                found.append(("; ".join(lines), page.url))
    return found


DAY_NAME = r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekends?)\b"


def hours(q, view):
    if not re.search(r"\b(hours?|open|close|closes|closing)\b", q):
        return None
    # "Are you open on Sunday?" asks about branches even without the word.
    if not re.search(r"\b(lobby|drive[- ]?(up|thru|through)|branch|branches)\b|" + DAY_NAME + r"|\bare you open\b", q):
        return None
    label = "Drive-up" if re.search(r"drive[- ]?(up|thru|through)", q) else "Lobby"
    if _specific_branch(q, view):
        return None
    schedules = _schedules(view, label)
    if not schedules:
        return None
    schedules = [(s.replace("–", "-").replace(" - ", "-"), u) for s, u in schedules]
    distinct = Counter(s for s, _ in schedules)
    urls = {}
    for s, u in schedules:
        urls.setdefault(s, u)
    top = [s for s, _ in sorted(distinct.items(), key=lambda kv: (-kv[1], kv[0]))][:4]
    lead = f"{label} hours vary by branch."
    body = " ".join(f"Schedule {i + 1}: {s}." for i, s in enumerate(top))
    sources = [_src(view, urls[s], s) for s in top]
    contact = next((p.url for p in view.pages.values() if path_of(p.url) == site.CONTACT_PATH), None)
    home = next((p.url for p in view.pages.values() if path_of(p.url) == "/"), None)
    sources += [_src(view, u) for u in (contact, home) if u]
    return reply(f"{lead} {body} Which branch do you plan to visit?", sources)


GENERIC_BRANCH = site.NAME_WORDS | {"each", "every", "all", "any", "my", "a", "local", "nearest", "closest", "new", "other", "main",
                  "general", "same", "that", "this", "full-service"}


def _specific_branch(q, view) -> Optional[str]:
    m = re.search(r"\b(?:the|our|your) ([a-z][a-z'-]*(?: [a-z'-]+){0,2}) (?:branch|office|location)\b", q)
    if m and not set(m.group(1).split()) & GENERIC_BRANCH:
        return m.group(1)
    return None


BRANCH_IN = re.compile(r"\b(?:branch|branches|location|locations|office)\s+(?:in|near)\s+([a-z][a-z .'-]{1,30}?)\s*(?:\?|$|,| area\b| iowa\b| illinois\b| wisconsin\b)")


def branch_address(q, view):
    place = BRANCH_IN.search(q)
    if place and re.search(r"\b(do you|is there|are there|any|have)\b", q):
        city = place.group(1).strip()
        found = [b for b in branches(view) if b["city"].lower() == city]
        if not found:   # the locator page shows nearby branches only, so look for the town elsewhere on the site
            pat = re.compile(rf"[^.\n]*\b{re.escape(city)}\b[^.\n]*\bbranch(?:es)?\b[^.\n]*|"
                             rf"[^.\n]*\bbranch(?:es)?\b[^.\n]*\b{re.escape(city)}\b[^.\n]*", re.I)
            for page in sorted(view.pages.values(), key=lambda p: (p.published or date.min, p.url), reverse=True):
                m = pat.search(page.markdown) if page.kind in ("news", "product") else None
                if m and "shared branch" not in m.group(0).lower() and len(m.group(0)) < 400:
                    return reply(f"Yes, the site mentions {site.NAME} branches in {city.title()}: \"{m.group(0).strip()}\" "
                                 "The branch locator in this snapshot lists nearby branches only, so use it for addresses.",
                                 [_src(view, page.url, m.group(0))])
            return reply(REFUSAL.format(what=f"a branch in {city.title()}"), [], confident=False)
        lines = [f"{b['street']}, {b['city']}, {b['state']} {b['zip']}" for b in found]
        return reply(f"Yes. {site.NAME} has {'a branch' if len(lines) == 1 else 'branches'} in {found[0]['city']}: "
                     + "; ".join(lines) + ".", [_src(view, found[0]["url"], lines[0])])
    if not re.search(r"\b(address|located|location|where is|where's)\b", q):
        return None
    if not re.search(r"\b(branch|office|location)\b", q):
        return None
    words = set(tokens(q)) - {"branch", "office", "location", "address", "located", "credit", "union"}
    for b in branches(view):
        names = set(tokens(f"{b['street']} {b['city']}"))
        if words & names:
            text = f"{b['street']}, {b['city']}, {b['state']} {b['zip']}"
            return reply(f"The {b['city']} branch is at {text}.", [_src(view, b["url"], text)])
    return None


def specific_branch_hours(q, view):
    """Hours for one named branch are only answerable if the site publishes them."""
    name = _specific_branch(q, view)
    if not name or not re.search(r"\bhours?\b|\bopen (on|at|in|during)\b", q):
        return None
    for page in view.pages.values():
        if page.kind == "news" and name in page.title.lower() and re.search(r"\*\*lobby\*\*", page.markdown, re.I):
            sched = [s for s, u in _schedules(view, "Lobby") if u == page.url]
            if sched:
                return reply(f"Lobby hours for the {name.title()} branch: {sched[0]}.", [_src(view, page.url, sched[0])])
    return reply(REFUSAL.format(what=f"hours for the {name.title()} branch"), [], confident=False)


LINK_WORD = re.compile(r"\b(link|url|web ?page|web ?address|website|locator)\b|\bonline\b.*\bjoin\b|\bjoin\b.*\bonline\b")


def _canonical(url: str) -> str:
    base = url.split("?")[0]
    return base if "#rates" in base else base.split("#")[0]


def _rates_link(q, view):
    """A rates link is the product page that actually carries that family's table."""
    family = family_of(q)
    if not family or "rate" not in q:
        return None
    pages = Counter(r.url for f in view.rows(family) for r in [f.row] + f.agreeing if r.kind == "product")
    counts = Counter()
    for page in view.pages.values():
        for _, url in MD_LINK.findall(page.markdown):
            if "#rates" in url and any(url.split("#")[0].rstrip("/") == p.rstrip("/") for p in pages):
                counts[_canonical(url)] += 1
    return counts.most_common(1)[0][0] if counts else None


def link(q, view):
    if not LINK_WORD.search(q):
        return None
    url = _rates_link(q, view)
    if not url:
        wanted = set(query_tokens(q)) - {"link", "url", "website", "use", "see", "web", "page", "site"}
        score = Counter()
        for page in view.pages.values():
            for text, target in MD_LINK.findall(page.markdown):
                target = _canonical(target)
                if site.DOMAIN not in target or page_kind(target) not in ("product", "rate_sheet"):
                    continue
                path = target.split("//")[1].replace("-", " ").replace("/", " ").replace(".", " ")
                score[target] += len(wanted & set(tokens(text))) + 5 * len(wanted & set(tokens(path)))
        ranked = sorted(score.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))
        if not ranked or not ranked[0][1]:
            return None
        url = ranked[0][0]
    return reply(f"You can use {url}", [_src(view, url)])


FEE_WORD = re.compile(r"\b(fees?|charges?|charged|cost|costs|price|how much)\b")
FEE_LINE = re.compile(r"^(.{4,90}?)\s+(\$\d[\d,.]*\*?.*|Free.*|\d+% .*|Price varies.*|Available at no charge.*)$")
FEE_EFFECTIVE = re.compile(rf"Effective ((?:{MONTHS}) \d{{1,2}}, \d{{4}})")
# How members name an item -> how the schedule names it.
FEE_TERMS = {"overdrawn": "overdraft", "nsf": "overdraft", "bounce": "overdraft", "bounced": "overdraft",
             "cashier": "official", "rekey": "key", "notarize": "notary", "notarized": "notary", "coins": "coin"}
FEE_GENERIC = {"fee", "charge", "charged", "cost", "much", "price", "transfer", "consumer", "my", "per", "each", "item",
               "send", "get", "new", "account", "do", "non", "member"}


def _fee_lines(page):
    """(item, value, section) for each priced line; section headers are the unpriced lines between them."""
    section, out = "", []
    for raw in page.markdown.split("\n"):
        line = raw.strip().strip("|").replace("|", " ").replace("&amp;#45;", "-")
        line = re.sub(r"\s+", " ", line).strip()
        m = FEE_LINE.match(line)
        if m:
            out.append((m.group(1).strip(), m.group(2).strip().replace("*", ""), section))
        elif 3 < len(line) < 60 and not re.search(r"[.$*]", line) and line[:1].isupper():
            section = line
    return out


def fees(q, view):
    if not FEE_WORD.search(q):
        return None
    schedules = []
    for page in view.pages.values():
        m = FEE_EFFECTIVE.search(page.markdown[:400])
        if m and "fee" in page.markdown[:200].lower():
            schedules.append((datetime.strptime(m.group(1), "%B %d, %Y").date(), page.url, page))
    if not schedules:
        return None
    _, _, page = max(schedules, key=lambda s: (s[0], s[1]))
    asked = {FEE_TERMS.get(t, t) for t in query_tokens(q)}
    wanted = asked - FEE_GENERIC - {"business"}
    if not wanted:
        return None
    business = "business" in q
    key = view.index.rarest(wanted)
    scored = []
    for name, value, section in _fee_lines(page):
        terms = {FEE_TERMS.get(t, t) for t in tokens(name)}
        if ("business" in f"{name} {section}".lower()) != business:
            continue
        shared = wanted & terms
        if key in terms and len(shared) / len(wanted) >= FEE_MIN_COVERAGE:
            scored.append((-len(shared), len(name), name, value))
    if not scored:
        return None   # the schedule calls itself non-inclusive, so a miss means "look elsewhere", not "unpublished"
    top = min(s[0] for s in scored)
    best = sorted(s for s in scored if s[0] == top)
    text = "; ".join(f"{name}: {value}" for _, _, name, value in best[:4])
    return reply(text + ".", [_src(view, page.url, text)])


HOLIDAY = re.compile(r"\b(holidays?|labor day|memorial day|thanksgiving|christmas( day| eve)?|new year'?s|"
                     r"independence day|fourth of july|july 4(th)?|presidents'? day|martin luther king|mlk|veterans day|"
                     r"columbus day|juneteenth|easter|good friday)\b")


def holiday_hours(q, view):
    """Holiday closings are only answerable if a Dupaco page (not a blog post) states them."""
    m = HOLIDAY.search(q)
    if not m or not re.search(r"\b(open|closed?|closing|hours?|schedule)\b", q):
        return None
    name = m.group(0).rstrip("s")
    for page in view.pages.values():
        if page.kind not in ("product", "landing"):
            continue
        hit = re.search(rf"[^.\n]*\bclosed\b[^.\n]*\b{re.escape(name)}[^.\n]*|[^.\n]*\b{re.escape(name)}[^.\n]*\bclosed\b[^.\n]*",
                        page.markdown, re.I)
        if hit:
            text = hit.group(0).strip(" -*")
            return reply(text + ".", [_src(view, page.url, text)])
    return reply(REFUSAL.format(what="holiday hours"), [], confident=False)


LEADER = re.compile(r"\bPresident (?:and|&) CEO,? ([A-Z][a-z]+(?: [A-Z]\.)? [A-Z][a-z]+)")
FOUNDED = site.FOUNDED


def organization(q, view):
    """Who runs the credit union and when it started: stated in releases and on its own pages."""
    if re.search(r"\b(ceo|president|chief executive|who runs|who leads|head of)\b", q):
        named = sorted(((p.published, m.group(1), p.url) for p in view.pages.values() if p.kind == "news" and p.published
                        for m in [LEADER.search(p.markdown)] if m), reverse=True)
        if named:
            when, name, url = named[0]
            return reply(f"{name} is {site.NAME}'s President and CEO, per its newest press release that names the role"
                         f" ({long_date(when)}).", [_src(view, url, f"President and CEO {name}")])
        return None
    if re.search(r"\b(founded|established|chartered|started|start|begin|began|how old|since when|history)\b", q) and \
            re.search(rf"\b({site.NAME.lower()}|you|credit union)\b", q) and not re.search(r"\b(account|loan|card|membership)\b", q):
        years = Counter()
        urls = defaultdict(list)
        for p in view.pages.values():
            if p.kind in ("product", "landing"):
                for m in FOUNDED.finditer(p.markdown):
                    y = m.group(1) or m.group(2)
                    years[y] += 1
                    urls[y].append(p.url)
        if years:
            year, _ = max(years.items(), key=lambda kv: (kv[1], kv[0]))
            src = sorted(set(urls[year]), key=lambda u: (len(u), u))[:3]
            return reply(f"{site.NAME} has served members since {year}.", [_src(view, u, f"Since {year}") for u in src])
    return None


CLOSING = re.compile(rf"(?:clos\w+|last day)[^.\n]{{0,60}}?((?:{MONTHS}) \d{{1,2}}, \d{{4}})", re.I)


def branch_status(q, view):
    """Has a named branch closed? Compares the announced date with the date of this view."""
    name = _specific_branch(q, view)
    if not name or not re.search(r"\b(open|close|closes|closed|closing|shut)\b", q):
        return None
    key = name.replace("-", "")
    for page in sorted(view.pages.values(), key=lambda p: (p.published or date.min, p.url), reverse=True):
        if page.kind != "news" or key not in page.title.lower().replace("-", "") or "clos" not in page.title.lower():
            continue
        m = CLOSING.search(page.markdown)
        if m:
            when = datetime.strptime(m.group(1), "%B %d, %Y").date()
            past = when <= view.taken_on
            text = (f"No. The {name.title()} branch closed on {m.group(1)}." if past and re.search(r"\b(open|still)\b", q)
                    else f"The {name.title()} branch {'closed' if past else 'is scheduled to close'} on {m.group(1)}.")
            return reply(text, [_src(view, page.url, m.group(0))])
    return None


SERVES = site.SERVES


def membership(q, view):
    if not re.search(r"\b(eligible|eligibility|qualify|who can (join|become)|can i join|become a member|join " + site.NAME.lower() + r")\b", q):
        return None
    if re.search(r"\b(online|link|url|how do i|how can i|how much|minimum|deposit)\b", q):
        return None
    if re.search(r"\b(overdraft|loans?|cards?|privilege|checking|savings|mortgage|scholarships?|grants?|accounts?)\b", q):
        return None   # qualifying for a product is not joining the credit union
    latest = max(((p.published, p.url, m) for p in view.pages.values() if p.published
                  for m in [SERVES.search(p.markdown)] if m), default=None, key=lambda x: (x[0], x[1]))
    join = next((p for p in view.pages.values() if path_of(p.url) == site.JOIN_PATH), None)
    if not latest or not join:
        return None
    area = latest[2].group(2)
    return reply(f"You're eligible if you live or work in {site.NAME}'s charter area, which covers counties throughout {area}. "
                 "See the join page for the full list of ways to qualify.",
                 [_src(view, join.url, "You live or work within our charter area"), _src(view, latest[1], latest[2].group(0))])


PHONE = site.PHONE


def contact(q, view):
    """How to reach Dupaco, or change personal details, comes from the contact page itself."""
    update = re.search(r"\b(update|change|correct)\b.*\b(contact|address|phone|email|name|information|info)\b", q)
    reach = re.search(rf"\b(phone number|call|telephone|contact ({site.NAME.lower()}|you|customer|member))\b", q)
    if not (update or reach):
        return None
    # "What number do I call to activate my card?" is about the card, not about reaching Dupaco in general.
    subject = set(tokens(q)) - {"phone", "number", "call", "contact", "telephone", "reach", "customer", "member", "service",
                                "talk", "someone", "person", "speak", "main", "general", "update", "change", "correct",
                                "information", "info", "address", "email", "name", "credit", "union"}
    if reach and not update and subject:
        return None
    page = next((p for p in view.pages.values() if path_of(p.url) == site.CONTACT_PATH), None)
    m = PHONE.search(page.markdown) if page else None
    if not m:
        return None
    if update:
        text = f"Log in to {site.ONLINE_BANKING} and use the Contact Us tab, or call {m.group(1)}."
    else:
        text = f"You can reach {site.NAME} at {m.group(1)}."
    return reply(text, [_src(view, page.url, f"Phone: {m.group(1)}")])
