"""The contract object: keeps one View per snapshot and routes each question."""
from __future__ import annotations

import re
from bisect import bisect_right
from calendar import monthrange
from datetime import date, datetime
from typing import Optional

from . import conflicts as conflict_finder
from .common import REFUSAL, Source, long_date, reply
from .parse import MONTHS, path_of
from .rates import answer_minimum, answer_rate, asks_for_rate
from .site import (branch_address, branch_count, branch_status, contact, fees, holiday_hours, hours, link, membership,
                   organization, routing, specific_branch_hours)
from .llm import Selector
from .search import path_terms, query_tokens, tokens
from .view import View

SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"])")
VALUE_QUESTION = re.compile(r"\b(rates?|apy|apr|fees?|hours?|interest|how much|cost|costs)\b")
# A date written into the question ("on May 15, 2026", "in May 2026") is an as-of date.
QUESTION_DATE = re.compile(rf"\b(?:({MONTHS})\.? (\d{{1,2}})(?:st|nd|rd|th)?,? (\d{{4}})|({MONTHS}),? (\d{{4}})|"
                           rf"(\d{{4}}-\d{{2}}-\d{{2}}))\b", re.I)
# "Did the 12-month rate change?" asks for history, not just today's value.
MOVED = r"(?:chang\w*|go(?:ne)? (?:up|down)|went (?:up|down)|increas\w*|decreas\w*|rais\w*|lower\w*|drop\w*|cut)"
CHANGE = re.compile(
    rf"^(?:did|has|have|was|were|is|are)\b.*\b{MOVED}\b"                       # "did the 12-month rate change?"
    rf"|\bhow (?:has|have) .* chang\w*|\bwhen did\b.*\b{MOVED}"               # "how has it changed?", "when did it change?"
    r"|\bbefore (?:it|they|the \w+) (?:chang\w*|went|was|were)\b|\bused to (?:be|pay|cost)\b"   # "what was it before?"
    r"|\b(?:higher|lower|more|less|better|worse)\b.*\b(?:now than|than (?:in|on|at|before|last|it was))\b")
EFFECTIVE_NOTE = re.compile(r" \(rates effective [^)]*\)")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
OFFICIAL_KINDS = {"product", "rate_sheet", "landing", "file"}
FORM_NOISE = re.compile(r"\(Required\)|First Name|Last Name|\\\*")
# --- Thresholds for answering from free text. Each was set on the public set and checked on held-out sets. ---
# Share of the question's idf weight a passage must cover. Value questions ("how much") need more of it.
MIN_COVERAGE = {"value": 0.6, "open": 0.4}
MIN_FAQ_MATCH = 0.45         # FAQ question vs member question, weighted overlap (search.Index.faq)
MIN_FAQ_VALUE_MATCH = 0.5    # stricter when the member wants a value: the FAQ answer must also contain a number
OFFICIAL_SHARE = 0.5         # a product page within half the top score beats an article
LOOSE_ARTICLE = 0.75         # an article answering "you/my" questions must cover this much of the question
SAME_STORY = 0.7             # articles within this share of the top score count as one story; the newest wins
DATED_KINDS = {"news", "blog"}
RELEASE_ROUTES = {"named", "yearly"}   # rules answers that depend on release dates; the LLM layer leaves them alone
AMOUNT = re.compile(r"\$\d")
OFFER_QUESTION = re.compile(r"^(do|does|can|is|are)\b.*\b(offer|have|provide|support|available)\b")
OFFER_KINDS = {"product", "rate_sheet", "landing"}
DEFINING = re.compile(r"\b(is|are|provides?|offers?|gives?|helps?|named|ranked|recogni[sz]ed|time)\b")
# A percentage only counts as a published rate when a rate word sits right next to it.
STATED_RATE = re.compile(r"\d%\s*(?:apr|apy|annual|rate|interest|dividend)|\b(?:apr|apy|rate)\b[^.]{0,30}\d(?:\.\d+)?%", re.I)
CURRENT_VALUE_KINDS = {"product", "rate_sheet"}   # blogs and releases are never a source for a live rate


def parse_as_of(as_of) -> Optional[date]:
    """'2026-05-15', '2026-05-15T10:00Z', '2026-05' (end of May) or '2026' (end of year). Anything else means now."""
    if not as_of:
        return None
    s = str(as_of).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}", s):
            year, month = int(s[:4]), int(s[5:])
            return date(year, month, monthrange(year, month)[1])
        if re.fullmatch(r"\d{4}", s):
            return date(int(s), 12, 31)
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


class KnowledgeBase:
    def __init__(self):
        self.views: list[View] = []
        self.llm = Selector()   # optional; off without an API key

    def ingest(self, snapshot) -> None:
        self.views.append(View(snapshot))
        self.views.sort(key=lambda v: v.taken_on)

    def view_at(self, as_of: Optional[str]) -> Optional[View]:
        day = parse_as_of(as_of)
        if day is None:
            return self.views[-1] if self.views else None
        i = bisect_right([v.taken_on for v in self.views], day)
        return self.views[i - 1] if i else None

    def answer(self, question: str, as_of: Optional[str] = None):
        try:
            return self._answer(question or "", as_of)
        except Exception:   # one odd question must not cost a crash; an honest refusal is the safe answer
            return self._unknown()

    def _answer(self, question: str, as_of: Optional[str]):
        q = re.sub(r"\s+", " ", question.lower().replace("’", "'")).strip()
        change = bool(CHANGE.search(q))
        if not as_of:
            # A change question runs up to now: "higher now than in June 2026" compares June with today, and
            # "between May 2026 and August 2026" ends at its later date. Any other question is asked as of its date.
            dates = self._dates_in(question)
            end = (max(dates) if len(dates) > 1 else None) if change else (dates[0] if dates else None)
            as_of = end.isoformat() if end and end < self.views[-1].taken_on else None
        view = self.view_at(as_of)
        if view is None:
            return reply(REFUSAL.format(what="anything from that early"), [], confident=False)
        if change:
            return self._history(question, q, view)
        return self._route(question, q, view)

    def _route(self, question, q, view):
        for handler in (routing, branch_count, link, membership, branch_status, specific_branch_hours, branch_address,
                        holiday_hours, hours, contact, organization, answer_minimum, answer_rate, fees):
            result = handler(q, view)
            if result is not None:
                return result
        return self._from_text(question, q, view)

    def _dates_in(self, question: str) -> list:
        """Dates written in the question, in order. Only one before the newest snapshot changes anything:
        'today (October 2026)' stays current."""
        days = []
        for m in QUESTION_DATE.finditer(question) if self.views else ():
            try:
                if m.group(1):
                    days.append(datetime.strptime(f"{m.group(1).title()} {m.group(2)} {m.group(3)}", "%B %d %Y").date())
                elif m.group(4):
                    month = datetime.strptime(m.group(4).title(), "%B").month
                    year = int(m.group(5))
                    days.append(date(year, month, monthrange(year, month)[1]))   # "in May" means as May ended
                else:
                    days.append(date.fromisoformat(m.group(6)))
            except ValueError:   # "February 30": not a real date, so it sets no as-of date
                continue
        return days

    def _history(self, question, q, view):
        """Answer the question at every snapshot up to this one and report where the value moved."""
        timeline = []
        for v in [v for v in self.views if v.taken_on <= view.taken_on]:
            a = self._route(question, q, v)
            gist = sorted(float(n.replace(",", "")) for n in NUMBER.findall(EFFECTIVE_NOTE.sub("", a.text)))   # values only
            if timeline and timeline[-1][2] == gist:
                timeline[-1] = (timeline[-1][0], v, gist, a)
            else:
                timeline.append((v, v, gist, a))
        now = timeline[-1][3]
        if not now.confident:
            return now
        if len(timeline) == 1:
            first = long_date(timeline[0][0].taken_on)
            return reply(f"No. It has not changed in any snapshot from {first} to {long_date(view.taken_on)}: "
                         f"{now.text}", now.sources)
        steps = []
        for (_, last_old, _, old), (first_new, _, _, _) in zip(timeline, timeline[1:]):
            if old.confident:
                steps.append(f"Before {long_date(first_new.taken_on)} (snapshot of "
                             f"{long_date(last_old.taken_on)}): {old.text}")
        return reply(f"Yes, it changed. Now: {now.text} " + " ".join(steps), now.sources,
                     conflict_note="Earlier snapshots show the older values listed above.")

    def _from_text(self, question, q, view):
        """No handler claimed the question: answer from a site FAQ, else from the best passage, else refuse."""
        is_rate = asks_for_rate(q)
        is_value = is_rate or bool(VALUE_QUESTION.search(q))
        names = view.index.names_in(question)
        answer, route = self._rules_text(question, q, view, is_rate, is_value, names)
        # The optional LLM picks a passage when the rules found nothing or chose by word overlap. It never overrides
        # the release routes: they apply "newest dated release wins", which a model seeing one passage cannot.
        if self.llm.enabled and route not in RELEASE_ROUTES:
            chosen = self._llm_choice(question, q, view, is_rate)
            if chosen is not None:
                return chosen
        return answer

    def _rules_text(self, question, q, view, is_rate, is_value, names):
        """The rules-only answer and the route that produced it."""
        faq = self._faq_answer(question, view, is_rate, is_value)
        if faq:
            return faq, "faq"
        if len(query_tokens(question)) < 2 and not names:
            return self._unknown(), "unknown"   # one generic word is not enough to claim an answer
        hits = self._candidates(question, q, view, is_rate, is_value)
        if not hits:
            return self._unknown(), "unknown"
        return self._pick(hits, question, q, view, names, is_rate)

    def _faq_answer(self, question, view, is_rate, is_value):
        match = view.index.faq(question)
        if not match:
            return None
        score, passage = match
        if not is_value and score >= MIN_FAQ_MATCH:
            return self._quote(passage, view)
        # A value question can still be a site FAQ ("What down payment do I need?"), if the match is strong and numeric.
        if is_value and not is_rate and score >= MIN_FAQ_VALUE_MATCH and re.search(r"\d", passage.text):
            return self._quote(passage, view)
        return None

    def _candidates(self, question, q, view, is_rate, is_value):
        """Passages that could answer, after every filter a careful reader would apply. Each hit: (score, coverage, passage)."""
        floor = MIN_COVERAGE["value" if is_value else "open"]
        wanted = set(query_tokens(question))
        top = sorted(wanted, key=lambda t: (-view.index.weight(t), t))[:4]
        hits = [h for h in view.index.search(question, limit=30)
                if h[1] >= floor or (not is_value and len(h[2].path_terms & wanted) >= 2)]   # the page is about it
        # Short questions: the answer must mention the most specific word. Wordy ones: two of the most specific.
        hits = [h for h in hits if self._on_point(top, set(tokens(h[2].heading + " " + h[2].text)) | h[2].path_terms)]
        hits = [h for h in hits if not FORM_NOISE.search(h[2].text)]
        hits = [h for h in hits if not h[2].text.endswith("?")] or hits
        if OFFER_QUESTION.search(q):   # a scam warning naming Zelle does not mean the credit union offers it
            hits = [h for h in hits if h[2].kind in OFFER_KINDS]
        if is_rate:
            hits = [h for h in hits if STATED_RATE.search(h[2].text) and h[2].kind in CURRENT_VALUE_KINDS]
        elif re.search(r"\b(how much|cost|costs|fees?|price|charge)\b", q):   # a cost question needs a price
            hits = [h for h in hits if re.search(r"\$\d|\bfree\b|\bno (?:fee|charge|cost)\b|\d%", h[2].text, re.I)]
        elif is_value:
            hits = [h for h in hits if re.search(r"\d", h[2].text)]
        return hits

    def _pick(self, hits, question, q, view, names, is_rate):
        """Choose among surviving passages: a named subject's own page, then product pages, then the newest article."""
        if names and not is_rate and re.match(r"(what|tell|has|have|who)\b", q):
            found = self._about(hits, names, question, view)
            if found:
                return self._cite(found[0], found[1], view), "named"
        official = [h for h in hits if h[2].kind in OFFICIAL_KINDS and h[0] >= OFFICIAL_SHARE * hits[0][0]]
        if official:   # the product pages speak for the credit union; articles about other people do not
            hits = official
        elif re.search(r"\b(you|your|i|my|me)\b", q) and hits[0][1] < LOOSE_ARTICLE:
            return self._unknown(), "unknown"   # a member asking about a service needs more than a loosely related article
        best = hits[0][2]
        if best.kind in DATED_KINDS:   # among comparable articles, the newest one is the current statement
            close = [h for h in hits if h[0] >= SAME_STORY * hits[0][0] and h[2].kind in DATED_KINDS]
            best = max(close, key=lambda h: (h[2].published or date.min, h[0]))[2]
            if re.search(r"\b(how many|how much|number of)\b", q):
                return self._yearly(best, close, set(query_tokens(question)), view), "yearly"
        return self._quote(best, view), "passage"

    def _yearly(self, best, close, wanted, view):
        """Annual releases each report a different year; give the newest figure and the one before it, dated."""
        answer = self._quote(best, view)
        older = sorted((h[2] for h in close if h[2].url != best.url and h[2].published and best.published
                        and h[2].published < best.published), key=lambda p: p.published, reverse=True)
        if not older:
            return answer
        prev = older[0]
        sentences = [x for x in re.split(r"(?<=[.!?])\s+|\s+-\s+", prev.text) if re.search(r"\d", x)]
        if not sentences:
            return answer
        line = max(sentences, key=lambda x: (len(wanted & set(tokens(x))), -len(x)))
        text = (f"{answer.text} (Newest release, {long_date(best.published)}.) The previous release"
                f" ({long_date(prev.published)}) reported: {line.strip()}")
        return reply(text, answer.sources + [Source(url=prev.url, snapshot_id=view.snapshot_id, quote=line[:200])])

    def _llm_choice(self, question, q, view, is_rate):
        """Let the LLM pick among the site's FAQs and passages. The answer is still a verbatim quote of the pick."""
        pool = [p for _, p in view.index.faq_ranked(question, 4)]
        seen = {p.text for p in pool}
        for _, _, p in view.index.search(question, limit=20):
            if len(pool) >= 12:
                break
            if p.text in seen or FORM_NOISE.search(p.text) or p.text.endswith("?"):
                continue
            if is_rate and (p.kind not in CURRENT_VALUE_KINDS or not STATED_RATE.search(p.text)):
                continue   # live rates come from rate tables, never from articles
            if OFFER_QUESTION.search(q) and p.kind not in OFFER_KINDS:
                continue
            seen.add(p.text)
            pool.append(p)
        if not pool:
            return None
        listing = []
        for p in pool:
            where = path_of(p.url)
            dated = f", {p.published}" if p.kind in DATED_KINDS and p.published else ""
            listing.append(f"({p.kind} page {where}{dated}) {p.heading[:80]}: {p.text[:350]}")
        choice = self.llm.pick(question, listing)
        if choice is None:
            return None            # the layer could not decide: fall back to the rules
        if choice < 0:
            return self._unknown()
        return self._quote(pool[choice], view)

    @staticmethod
    def _on_point(top, terms) -> bool:
        if len(top) <= 2:
            return top[0] in terms
        return len(terms.intersection(top)) >= 2

    @staticmethod
    def _unknown():
        return reply(REFUSAL.format(what="that"), [], confident=False)

    def _about(self, hits, names, question, view):
        """Best sentences about a named thing: pages named after it first, then the newest page."""
        wanted = set(query_tokens(question))
        compound = "".join(names)
        urls = {h[2].url for h in hits} | {u for u in view.pages if set(names) & path_terms(u)}

        def page_rank(url):
            page = view.pages[url]
            terms = path_terms(url) | {t.replace(" ", "") for t in path_terms(url)}
            titled = any(n in terms for n in names) or any(compound in t for t in terms)
            return (titled, page.published or date.min, url)

        for url in sorted(urls, key=page_rank, reverse=True):
            ranked = []
            for n, sentence in enumerate(s for block in re.split(r"\n\s*\n", view.pages[url].markdown)
                                         for s in SENTENCE.split(re.sub(r"\s+", " ", block).strip())):
                terms = set(tokens(sentence))
                if len(sentence) < 30 or sentence.startswith(("#", "|", "!", "[")):
                    continue
                if not set(names) & terms and not re.search(r"\$\d|\d%", sentence):
                    continue
                ranked.append((-len(wanted & terms), -bool(DEFINING.search(sentence)),
                               sentence.startswith(("“", '"')), n, sentence))
            if ranked:
                ranked.sort()
                chosen = [r[-1] for r in ranked[:3]]
                if not any(AMOUNT.search(c) for c in chosen):   # a program's amount is usually the point of asking
                    chosen += [r[-1] for r in ranked[3:] if AMOUNT.search(r[-1])][:1]
                return " ".join(chosen), url
        return None

    def _cite(self, text, url, view):
        return reply(text, [Source(url=url, snapshot_id=view.snapshot_id, quote=text[:200])])

    def _quote(self, passage, view):
        text = " ".join(SENTENCE.split(passage.text)[:3])
        if passage.kind == "product":   # name the product the passage belongs to
            title = view.pages[passage.url].title.split(" | ")[0].strip()
            heading = passage.heading.split(" | ")[0].strip()
            label = [x for x in dict.fromkeys((title, heading)) if x and len(x) < 60 and x.lower() not in text.lower()]
            if label:
                text = f"{' - '.join(label)}: {text}"
        return reply(text, [Source(url=passage.url, snapshot_id=view.snapshot_id, quote=text[:200])])

    def conflicts(self):
        return conflict_finder.find(self.views)
