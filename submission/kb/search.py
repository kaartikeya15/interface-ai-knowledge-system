"""Small BM25 passage index in pure Python, weighted by how much a page type can be trusted."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from . import site_profile as site

TOKEN = re.compile(r"[a-z0-9]+")
STOP = set("""a about all am an and any are as at be been but by can could did do does for from get give
had has have how i if in is it its just like me my need of on or our please provide s should so tell than that the
their them then there these they this to us was we were what when where which who why will with would you your
going today any some want looking know options option new
whats hows wheres much many hi hello hey thanks thank yes ok okay don doesn didn isn aren wasn won""".split()) | site.NAME_WORDS
FAQ_EXTRA_WORD = 0.5    # a word only the FAQ has counts half: FAQs add context ("for Apple Pay") the member leaves out
FAQ_WITHOUT_KEY = 0.67  # an FAQ missing the question's most specific word must match two thirds of everything else
TRUST = {"product": 0, "rate_sheet": 0, "landing": 1, "file": 1, "calculator": 2, "blog": 3, "news": 4}   # tie-break
WEIGHT = {"rate_sheet": 1.2, "product": 1.3, "news": 1.0, "landing": 0.7, "file": 0.7, "blog": 0.8, "calculator": 0.2}
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
NOISE = re.compile(r"^(!\[|\||- \S+ .*\+\d+$|\d+ min read|\[.*\]\(.*\)$|for more information|to learn more)", re.I)


def stem(w: str) -> str:
    if len(w) > 3 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


# Member wording -> the site's wording. Only one-to-one swaps, so coverage stays honest.
SYNONYMS = {"cd": "certificate", "eligible": "join", "eligibility": "join", "wallet": "pay", "overdraw": "overdrawn",
            "overdrawing": "overdrawn", "cashier": "official", "approved": "approval", "approve": "approval",
            "insure": "insured", "ceo": "president"}


# Words a member might use where the site uses another. Either word counts as a match; neither is required.
ALTERNATES = {"car": {"auto"}, "vehicle": {"auto"}, "auto": {"car"}, "house": {"home", "homebuying"},
              "buy": {"homebuying", "purchase"}, "buying": {"homebuying", "purchase"}, "quickly": {"long"},
              "fast": {"long"}, "daily": {"day"}, "day": {"daily"}, "fee": {"charge", "cost"}, "charge": {"fee", "cost"},
              "cost": {"fee", "charge"}, "qualify": {"eligibility", "eligible", "requirement"}}


def tokens(text: str) -> list[str]:
    text = re.sub(r"(?<=[a-z])\$(?=[a-z])", "s", text.lower())   # Home$tart
    for before, after in site.TEXT_FIXES:
        text = text.replace(before, after)
    text = re.sub(r"(?<=[a-z])['’](m|s|re|ve|ll|d|t)\b", "", text)   # "I'm" is not the word "m"
    return [stem(t) for t in TOKEN.findall(text) if t not in STOP]


OFFER = re.compile(rf"\bdo (?:you|y'all|{site.NAME.lower()}) (?:have|carry|provide)\b")


def query_tokens(text: str) -> list[str]:
    return list(dict.fromkeys(SYNONYMS.get(t, t) for t in tokens(text)))


def same_word(a: str, b: str) -> bool:
    """'deposited' ~ 'deposit', 'preapproved' ~ 'preapproval': one word in two forms, by a long shared prefix."""
    if a == b:
        return True
    short = min(len(a), len(b))
    if short < 6:
        return False
    common = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), short)
    return common >= max(5, short - 3)


@dataclass
class Passage:
    url: str
    kind: str
    heading: str
    text: str
    published: object
    path_terms: frozenset = frozenset()


def path_terms(url: str) -> frozenset:
    return frozenset(tokens(urlparse(url).path.replace("-", " ").replace("/", " ")))


FAQ_LINE = re.compile(r"^\s*- \[([^\]]{8,160}\?)\]\([^)]*\)\s*$", re.M)


def faqs_of(page) -> list[Passage]:
    """Site FAQs render as a linked question followed by its answer paragraphs."""
    marks = list(FAQ_LINE.finditer(page.markdown))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else min(len(page.markdown), m.end() + 900)
        body = LINK.sub(r"\1", page.markdown[m.end():end])
        body = re.split(r"\n#{1,6} ", body)[0]
        text = re.sub(r"\s+", " ", body).strip()
        if len(text) >= 15:
            out.append(Passage(page.url, page.kind, m.group(1), text[:700], page.published))
    return out


def passages_of(page) -> list[Passage]:
    out, heading = [], page.title
    for block in re.split(r"\n\s*\n", page.markdown):
        b = block.strip()
        if b.startswith("#"):
            heading = b.lstrip("# ").split("\n")[0]
            continue
        if NOISE.match(b):
            continue
        text = re.sub(r"\s+", " ", LINK.sub(r"\1", b)).strip(" -*_")
        if len(text) >= 25:
            out.append(Passage(page.url, page.kind, heading, text[:900], page.published, path_terms(page.url)))
    return out


class Index:
    K1, B = 1.2, 0.75

    def __init__(self, pages):
        self.passages: list[Passage] = []
        self.postings = defaultdict(list)
        self.lengths: list[int] = []
        self.faqs: list[tuple[set, Passage]] = []
        seen = set()
        for page in pages:
            if page.kind != "news":   # FAQs on learn and calculator pages are still the site answering members
                self.faqs.extend((set(query_tokens(f.heading)), f) for f in faqs_of(page))
            for p in passages_of(page):
                if (p.text, p.kind) in seen:
                    continue
                seen.add((p.text, p.kind))
                terms = tokens(p.heading + " " + p.text)
                i = len(self.passages)
                self.passages.append(p)
                self.lengths.append(len(terms))
                for term, tf in Counter(terms).items():
                    self.postings[term].append((i, tf))
        n = max(1, len(self.passages))
        self.avg = sum(self.lengths) / n
        self.idf = {t: math.log(1 + (n - len(v) + 0.5) / (len(v) + 0.5)) for t, v in self.postings.items()}
        # A word the site never uses is the most specific word in a question, not a neutral one.
        self.unseen = math.log(1 + (n + 0.5) / 0.5)

    def weight(self, term: str) -> float:
        return self.idf.get(term, self.unseen)

    def rarest(self, terms) -> Optional[str]:
        """The most specific word of a question: whatever answers it has to mention this."""
        terms = list(terms)
        return max(terms, key=lambda t: (self.weight(t), t)) if terms else None

    def search(self, query: str, limit: int = 12, kinds: Optional[set] = None):
        """Returns (score, coverage, passage); coverage = share of query weight the passage contains.
        A capitalised name in the question ("Forbes") must appear: it is who the question is about."""
        terms = query_tokens(query)
        total = sum(self.weight(t) for t in terms) or 1.0
        names = self.names_in(query)
        must = max(names, key=lambda t: self.idf[t]) if names else None
        allowed = {i for i, _ in self.postings[must]} if must else None
        scores, covered = defaultdict(float), defaultdict(float)
        for t in terms:
            for i, tf in self.postings.get(t, ()):
                norm = tf + self.K1 * (1 - self.B + self.B * self.lengths[i] / self.avg)
                scores[i] += self.idf[t] * tf * (self.K1 + 1) / norm
                covered[i] += self.idf[t]
        for i in list(scores):   # a word in the page's own path counts as covered
            covered[i] += sum(self.idf.get(t, 0) for t in self.passages[i].path_terms.intersection(terms)
                              if t not in self.seen_in(i, terms))
        ranked = []
        for i, s in scores.items():
            p = self.passages[i]
            if (kinds and p.kind not in kinds) or (allowed is not None and i not in allowed):
                continue
            on_topic = 1 + 0.5 * min(3, len(p.path_terms.intersection(terms)))   # the page is about this
            named = 1.5 if must and must in tokens(p.text) else 1.0                # not just its heading
            coverage = min(1.0, covered[i] / total)
            ranked.append((s * WEIGHT[p.kind] * on_topic * named * (0.5 + coverage), coverage, p))
        ranked.sort(key=lambda x: (-x[0], x[2].url, x[2].text))
        return ranked[:limit]

    def names_in(self, query: str) -> list:
        """Capitalised words after the first: the things a question is about."""
        return [t for w in query.split()[1:] if w[:1].isupper() for t in tokens(w) if self.postings.get(t)]

    def seen_in(self, i: int, terms: list) -> set:
        return {t for t in terms if any(j == i for j, _ in self.postings.get(t, ()))}

    def _matches(self, t: str, terms: set) -> set:
        """The FAQ words that stand for member word t: itself, a listed alternate, or another form of it."""
        alts = ALTERNATES.get(t, set())
        return {u for u in terms if u == t or u in alts or same_word(t, u)}

    def faq_ranked(self, query: str, k: int = 4) -> list:
        """The k FAQs sharing the most weight with the question, with none of faq()'s gates: candidates for the LLM."""
        q = set(query_tokens(OFFER.sub("do you offer", query.lower())))
        if not q:
            return []
        q_weight = sum(self.weight(t) for t in q)
        scored, seen = [], set()
        for terms, p in self.faqs:
            found = {t: self._matches(t, terms) for t in q}
            hit = {t for t, m in found.items() if m}
            if not hit or (p.heading, p.text) in seen:
                continue
            seen.add((p.heading, p.text))
            echoed = set().union(*found.values())
            score = sum(self.weight(t) for t in hit) / (q_weight + FAQ_EXTRA_WORD * sum(self.weight(t) for t in terms - echoed))
            scored.append((score, TRUST[p.kind], p.url, p))
        scored.sort(key=lambda x: (-x[0], x[1], x[2]))
        return [(s, p) for s, _, _, p in scored[:k]]

    def faq(self, query: str):
        """Best FAQ for the member's question. The member's words count in full; words only the FAQ has count half,
        so 'Do I need a special phone?' still fits 'do I need a special phone for apple pay'."""
        q = set(query_tokens(OFFER.sub("do you offer", query.lower())))   # FAQs ask "does Dupaco offer"
        if not q:
            return None
        best, best_score = None, 0.0
        key = self.rarest(q)
        q_weight = sum(self.weight(t) for t in q)
        for terms, p in self.faqs:
            found = {t: self._matches(t, terms) for t in q}
            hit = {t for t, m in found.items() if m}
            shared = sum(self.weight(t) for t in hit)
            if len(hit) < 2:
                continue   # one shared word is a topic, not the same question
            if not found[key] and shared < FAQ_WITHOUT_KEY * q_weight:
                continue   # missing the most specific word is fine only if nearly everything else matches
            echoed = set().union(*found.values())
            score = shared / ((q_weight + FAQ_EXTRA_WORD * sum(self.weight(t) for t in terms - echoed)) or 1.0)
            if score > best_score or (score == best_score and best and (TRUST[p.kind], p.url) < (TRUST[best.kind], best.url)):
                best, best_score = p, score
        return (best_score, best) if best else None
