"""Answer rate questions from resolved table facts: pick the family, then the exact tier."""
from __future__ import annotations

import re
from typing import Optional

from .common import REFUSAL, long_date, reply, sources_for
from . import site_profile as site
from .parse import MONTHS
from .search import stem

RATE_WORD = re.compile(r"\b(rates?|apy|apr|dividends?|interest|yields?|pays?|paying|earn|earns?|earning|percent)\b|%")
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
            "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
        "hundred": 100}
# A make names a vehicle as surely as the word "car" does ("a used 2019 Honda").
CAR_MAKES = (r"\b(honda|toyota|ford|chevy|chevrolet|nissan|hyundai|kia|subaru|jeep|dodge|ram|gmc|buick|cadillac|"
             r"chrysler|mazda|volkswagen|vw|bmw|audi|lexus|acura|tesla|volvo|mitsubishi|lincoln|infiniti)\b")
FAMILIES = [
    ("ira", r"\b(ira|iras|roth)\b|traditional ira"),
    ("certificate", r"\b(certificates?|cds?|share certs?|month (fixed )?special)\b|\bjumbo\b(?! (mortgage|loan|home))"),
    ("card", r"\b(credit cards?|visa|platinum|rewards card|cards?)\b"),
    ("home_equity", r"\b(heloc|home equity)\b"),
    ("home_improvement", r"\bhome improvement\b"),
    ("recreational", r"\b(boat|rv|camper|motor ?home|atv|snowmobile|jet ?ski|recreational)\b"),
    ("auto", r"\b(autos?|cars?|vehicles?|motorcycles?|trucks?|suvs?|minivans?|pickups?|sedans?)\b|" + CAR_MAKES),
    ("personal", r"\bpersonal loans?\b"),
    ("mortgage", r"\b(mortgages?|home loans?|refinance|refi|\d+[- ]year fixed|(?:10|15|20|25|30)[- ]year)\b"),
    ("deposit", r"\b(checking|savings?|money market|hsa|health savings|club|launchpad)\b"),
]
# Words that can sit in front of a product noun without naming a product variant.
GENERIC_MODIFIER = set("""a an the my your our any this that of for on in to your new used fixed standard regular current
home house car auto personal savings share business checking term rate interest online money
my all do does is are what which how year years month months yr pay best highest top lowest
lower higher better cheaper cheapest good great low high same other another typical average usual
credit debit visa""".split()) | site.NAME_WORDS
PRODUCT_NOUN = re.compile(r"\b([a-z0-9/-]+) (?:rate )?(loans?|mortgages?|accounts?|savings|certificates?|cds?|checking|cards?)\b")
ACCOUNTS = site.QUESTION_ACCOUNT_NAMES


def asks_for_rate(q: str) -> bool:
    """'Apple Pay' and 'bill pay' name services; they are not questions about what an account pays."""
    return bool(RATE_WORD.search(re.sub(r"\b(apple|google|samsung|bill|mobile) pay\b", " ", q)))


def _terms(q: str) -> tuple[Optional[int], Optional[int]]:
    """(months, years) mentioned in the question."""
    # "eighty-four month", "thirty six month", "sixty-month", "twelve month"
    t = re.sub(r"\b(" + "|".join(TENS) + r")(?:[- ](" + "|".join(list(WORD_NUM)[:9]) + r"))?\b",
               lambda m: str(TENS[m.group(1)] + (WORD_NUM[m.group(2)] if m.group(2) else 0)), q)
    t = re.sub(r"\b(" + "|".join(WORD_NUM) + r")\b", lambda m: str(WORD_NUM[m.group(1)]), t)
    m = re.search(r"(\d+)[- ]?(?:months?|mos?)\b", t)
    y = re.search(r"(\d+)[- ]?(?:years?|yrs?)\b", t)
    return (int(m.group(1)) if m else None, int(y.group(1)) if y else None)


SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mil": 1e6, "million": 1e6}


def _amounts(q: str) -> list[float]:
    """'$30,000', '$45k', '$1.2 million'."""
    return [float(a.replace(",", "")) * SCALE.get(k, 1) - (0.01 if below else 0)   # "under $5,000" is $4,999.99
            for below, a, k in re.findall(r"(\b(?:under|below|less than)\s+)?\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|million|mil|m)?\b", q)]


def _range(tier: str) -> Optional[tuple[float, float]]:
    nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", tier)]
    if not nums:
        return None
    return (nums[0], nums[1]) if len(nums) > 1 else (nums[0], float("inf"))


def _months_of(label: str) -> Optional[int]:
    m = re.search(r"(\d+)[\s-]*(?:month|mo\b)", label, re.I)
    if m:
        return int(m.group(1))
    y = re.search(r"(\d+)\s*year", label, re.I)
    return int(y.group(1)) * 12 if y else None


def _pct(v: str) -> float:
    return float(re.findall(r"\d+(?:\.\d+)?", v)[-1])


def _top(row) -> float:
    return _pct(row.values.get("APY") or row.values.get("APR") or next(iter(row.values.values())))


def _describe(row) -> str:
    v = row.values
    if "APY" in v:
        return f"{v.get('dividend rate', '')} dividend rate, {v['APY']} APY" if "dividend rate" in v else f"{v['APY']} APY"
    if "rate" in v:
        return f"{v['rate']} rate / {v.get('APR', '')} APR"
    return f"{v['APR']} APR"


def _effective(facts) -> str:
    dates = sorted({f.row.effective for f in facts if f.row.effective})
    return f" (rates effective {long_date(dates[-1])})" if dates else ""


def _finish(view, facts, lines, note=None):
    return reply("; ".join(lines) + _effective(facts) + ".", sources_for(view, facts), conflict_note=note or _beaten(facts))


def _beaten(facts) -> Optional[str]:
    stale = [(f, b) for f in facts for b in f.beaten]
    if not stale:
        return None
    f, b = stale[0]
    return (f"{len(stale)} older copies disagree, e.g. {b.url} still shows {_describe(b)}"
            f" (effective {b.effective}); used the newest effective date instead.")


def _refuse_missing(view, what="that rate"):
    return reply(REFUSAL.format(what=what), [], confident=False)


def family_of(q: str) -> Optional[str]:
    fam = _family_named(q)
    # "a 2025 Civic on a 60 month loan": a model year and a loan, with no other product named, is a vehicle
    if not fam and re.search(r"\b(19[5-9]\d|20[0-4]\d) [a-z]+", q) and re.search(r"\bloans?\b|\bfinanc", q):
        return "auto"
    return fam


def _family_named(q: str) -> Optional[str]:
    if re.search(r"\b(savings|checking|money market)( account)?\b", q) and not re.search(r"\b(visa|credit cards?|ira|iras|roth|certificates?|cds?)\b", q):
        return "deposit"
    for fam, pat in FAMILIES:
        if re.search(pat, q):
            return fam
    return None


LENDING = {"mortgage", "auto", "personal", "home_equity", "home_improvement", "recreational"}


def _unlisted_product(q: str, view) -> bool:
    """'construction loan', 'bitcoin savings': a named variant no published rate row mentions."""
    catalogue = {stem(w) for f in view.facts.values() for w in re.findall(r"[a-z0-9/-]+", f"{f.row.title} {f.row.label}".lower())}
    for word, _ in PRODUCT_NOUN.findall(q):
        if word in GENERIC_MODIFIER or re.fullmatch(r"\d.*|.*\d", word) or word.endswith(("-month", "-year")):
            continue
        if stem(word) not in catalogue:   # "IRA certificate" against a table titled "IRAs"
            return True
    return False


def answer_minimum(q: str, view):
    """Opening minimums sit in the same rows as the rates."""
    joining = re.search(r"\b(how much|minimum)\b.*\b(join|become a member|membership)\b", q)
    if not joining and not (re.search(r"\bminimum\b|\bhow much\b.*\bopen\b", q) and re.search(r"\b(open|deposit)\b", q)):
        return None
    name = "Share Savings" if joining else next(_named_accounts(q), None)
    facts = [f for f in view.rows("deposit", "business" in q) if name and name.lower() in f.row.label.lower()]
    values = {f.row.cells.get("Min. to Open") for f in facts} - {None, "", "—", "None"}
    if not values:
        return None
    return reply(f"The minimum to open {name} is {sorted(values)[0].replace('.00', '')}.", sources_for(view, facts))


def answer_rate(q: str, view):
    q = DATE_PHRASE.sub(" ", q)   # "in May 2026" is when, not a model year
    fam = family_of(q)
    months, years = _terms(q)
    # "what are you charging on mortgages": for a loan, the charge is its rate (a fee question never reaches a loan family)
    asks_rate = asks_for_rate(q) or bool(fam in LENDING and (months or years or re.search(r"\b(how much|charging)\b", q)))
    # "6 month cd" or "bright savings apy?": a product and a term with no other words is asking its rate.
    asks_rate = asks_rate or bool(fam and (months or years) and len(re.findall(r"[a-z]+", q)) <= 5)
    asks_rate = asks_rate or bool(fam in ("certificate", "ira") and re.search(r"\b(best|highest|top|most)\b", q))
    asks_rate = asks_rate or bool(fam in ("certificate", "ira") and (months or years))   # "the 10 month special"
    asks_rate = asks_rate or bool(fam in ("certificate", "ira") and re.search(r"\b(terms?|lengths?|options|choices|kinds|types|specials|available)\b", q))
    specific = next((n for n in _named_accounts(q) if n != "Share Savings" or "share savings" in q), None)   # not bare "savings"
    if asks_rate and _unlisted_product(q, view) and not (fam == "deposit" and specific):
        return _refuse_missing(view)
    if not fam or not asks_rate and not re.search(r"\bcards?\b.*\boffer|\boffer\b.*\bcards?\b", q):
        return None
    business = bool(re.search(r"\bbusiness\b|\bcommercial\b", q))
    if not view.rows(fam) and view.line_facts:
        return _prose_rate(q, view, fam)
    handler = {"certificate": _certificate, "ira": _certificate, "mortgage": _mortgage, "auto": _auto, "card": _card,
               "deposit": _deposit}.get(fam, _term_loan)
    return handler(q, view, fam, business)


QUALIFIERS = ("jumbo", "special", "business", "ira", "roth", "traditional", "apy", "apr")


def prose_facts(view, fam):
    """Live sentence-stated rates for a product with no rate table. Expired offers are history, not answers."""
    pattern = dict(FAMILIES)[fam]
    return [f for f in view.line_facts if not f.expired and re.search(pattern, f.label.lower())]


def _prose_rate(q, view, fam):
    from .common import Source
    from .parse import AUTHORITY
    months, years = _terms(q)
    months = months or (years * 12 if years else None)
    facts = prose_facts(view, fam)
    if months:
        facts = [f for f in facts if _months_of(f.label) == months]
    for word in QUALIFIERS:
        if re.search(rf"\b{word}\b", q) and word not in ("apy", "apr"):
            facts = [f for f in facts if word in f.label.lower()]
    if not facts:
        return _refuse_missing(view)
    best = {}
    for f in sorted(facts, key=lambda f: (AUTHORITY[f.kind], f.url), reverse=True):
        best.setdefault(f.label.lower(), f)
    chosen = sorted(best.values(), key=lambda f: (_months_of(f.label) or 0, f.label))
    return reply("; ".join(f"{f.label}: {f.value}" for f in chosen) + ".",
                 [Source(url=f.url, snapshot_id=view.snapshot_id, quote=f.quote) for f in chosen])


def _negated(word: str, q: str) -> bool:
    """'not jumbo', 'non-jumbo', 'no jumbo', 'without the jumbo': the member rules the qualifier out."""
    return bool(re.search(rf"\b(?:not|non|no|without|excluding|don't want|isn't)\b[- ]?(?:[a-z']+ ){{0,3}}?{word}\b", q))


def _minimum(row) -> Optional[float]:
    m = re.search(r"\$([\d,]+(?:\.\d+)?)", row.cells.get("Min. to Earn", "") or "")
    return float(m.group(1).replace(",", "")) if m else None


def _by_term(facts, months):
    return [f for f in facts if _months_of(f.row.label) == months]


def _certificate(q, view, fam, business):
    facts = view.rows(fam, business)
    months, years = _terms(q)
    months = months or (years * 12 if years else None)
    if months:
        facts = _by_term(facts, months)
    for word in ("jumbo", "special", "roth", "traditional"):
        if _negated(word, q) or (word == "jumbo" and word not in q and re.search(r"\b(regular|standard)\b.*\bspecial\b", q)):
            facts = [f for f in facts if word not in f.row.label.lower()]
        elif word in q:
            facts = [f for f in facts if word in f.row.label.lower()]
    amounts = _amounts(q)
    if amounts and fam == "certificate":
        budget = amounts[0]
        short = re.search(r"\b(don't have|do not have|less than|under|below|only have|can't afford)\b", q)
        fits = [f for f in facts if (m := _minimum(f.row)) is None or (m < budget if short else m <= budget)]
        facts = fits or facts
    if fam == "ira" and (amounts or "variable" in q):
        tiered = [f for f in facts if "$" in f.row.label]
        fits = [f for f in tiered if amounts and (r := _range(f.row.label.split(": ")[-1])) and r[0] <= amounts[0] <= r[1]]
        facts = fits or tiered
    if not facts:
        return _refuse_missing(view)
    if re.search(r"\b(best|highest|top|most)\b", q):
        best = max(_top(f.row) for f in facts)
        facts = [f for f in facts if _top(f.row) == best]
    facts.sort(key=lambda f: (_months_of(f.row.label) or 0, f.row.label))
    noun = "" if fam == "ira" else " certificate"
    return _finish(view, facts, [f"{f.row.label.replace(' Certificate Special', ' Special')}{noun}: {_describe(f.row)}"
                                 f" ({f.row.cells.get('Min. to Earn', '').replace('.00', '')} minimum)" for f in facts])


def _loan_cap(label: str) -> float:
    m = re.search(r"\$([\d,]+)", label)
    return float(m.group(1).replace(",", "")) if m else float("inf")


def _mortgage(q, view, fam, business):
    kind = "refinance" if re.search(r"\brefi", q) else "purchase"
    facts = [f for f in view.rows(fam) if f.row.label.lower().startswith(kind)]
    # A program named in its own row ("Mortgage Rate Relief Program"): the question's distinctive word picks it.
    asked = set(re.findall(r"[a-z]{4,}", q)) - MORTGAGE_WORDS
    named = [f for f in view.rows(fam) if not re.match(r"(purchase|refinance)", f.row.label.lower())
             and asked & set(re.findall(r"[a-z]{4,}", f.row.label.lower()))]
    if named:
        return _finish(view, named, [f"{f.row.label}: {_describe(f.row)}" for f in named])
    _, years = _terms(q)
    if years:
        facts = [f for f in facts if f"{years}-year" in f.row.label.lower()]
    amounts = _amounts(q)
    if amounts and facts:
        fits = [f for f in facts if _loan_cap(f.row.label) >= amounts[0]]
        if not fits:
            return _refuse_missing(view, "a rate for a loan that size")
        cap = min(_loan_cap(f.row.label) for f in fits)
        facts = [f for f in fits if _loan_cap(f.row.label) == cap]
    if not facts:
        return _refuse_missing(view)

    def order(f):
        y = re.search(r"(\d+)-year", f.row.label.lower())
        return (-int(y.group(1)) if y else 0, _loan_cap(f.row.label))
    facts.sort(key=order)
    return _finish(view, facts, [f"{re.sub(r'(?i)conventional ', '', f.row.label)}: {_describe(f.row)}" for f in facts])


MORTGAGE_WORDS = {"mortgage", "mortgages", "rate", "rates", "fixed", "year", "years", "conventional", "loan", "loans",
                  "purchase", "refinance", "program", "programs", "home", "homes", "what", "there", "with", "that",
                  "this", "your", "have", "does", "current", "qualified", "borrowers", "apr"}


def _model_years(label: str) -> tuple[int, int]:
    m = re.search(r"(20\d\d)(?:[-–](\d\d))?", label)
    first = int(m.group(1))
    if "older" in label.lower():
        return (0, first)
    second = 2000 + int(m.group(2)) if m.group(2) else first
    return (min(first, second), max(first, second))   # the site writes both 2024-27 and 2026-23


DATE_PHRASE = re.compile(r"\b(?:in|on|as of|during|since)?\s*(?:" + MONTHS + r")\.?(?: \d{1,2})?,? \d{4}\b", re.I)


def _auto(q, view, fam, business):
    facts = view.rows(fam)
    months, years = _terms(q)
    months = months or (years * 12 if years else None)
    year = re.search(r"\b(19\d\d|20\d\d)\b", q)
    if year:
        y = int(year.group(1))
        facts = [f for f in facts if _model_years(f.row.label)[0] <= y <= _model_years(f.row.label)[1]]
    elif facts:
        newest = max(_model_years(f.row.label)[1] for f in facts)
        if not months:
            facts = [f for f in facts if _model_years(f.row.label)[1] == newest]
    if months:
        facts = _by_term(facts, months)
        if not year and facts:
            facts = [max(facts, key=lambda f: _model_years(f.row.label)[1])]
    if not facts:
        return _refuse_missing(view)
    facts.sort(key=lambda f: (-_model_years(f.row.label)[1], _months_of(f.row.label) or 0))
    return _finish(view, facts, [f"{f.row.label}: {_describe(f.row)}" for f in facts])


def _card(q, view, fam, business):
    facts = view.rows(fam)
    named = [w for w in ("platinum", "rewards") if w in q]
    if business:
        facts = [f for f in facts if f.row.business]
    elif named or "personal" in q:
        facts = [f for f in facts if not f.row.business]
    if named:
        facts = [f for f in facts if any(w in f"{f.row.title} {f.row.label}".lower() for w in named)]
    # "after the intro period ends" asks for the rate that follows the intro
    wants_intro = re.search(r"\b(intro|introductory|balance transfer)\b", q) and not re.search(r"\bafter\b.*\bintro|\bintro\w* (period )?(ends|is over)", q)
    facts = [f for f in facts if bool(wants_intro) == ("intro" in f.row.label.lower())] or facts
    if not facts:
        return _refuse_missing(view)
    facts.sort(key=lambda f: (f.row.business, f.row.title, f.row.label))
    return _finish(view, facts, [f"{_card_name(f.row)}: {f.row.values['APR']} APR" for f in facts])


def _card_name(row) -> str:
    if row.title.lower() in row.label.lower():
        return row.label
    return f"{row.title} ({row.label})"


def _named_accounts(q):
    for name, pat in ACCOUNTS:
        if re.search(pat, q):
            yield name


def _deposit(q, view, fam, business):
    facts = view.rows(fam, business)
    specific = next((n for n in _named_accounts(q) if n != "Share Savings" or "share savings" in q), None)
    if not specific and re.search(r"\b(which|what)\b.*\b(account|savings)\b", q) and re.search(r"\b(highest|best|top|most)\b", q):
        chosen = [f for f in facts if "savings" in f.row.label.lower() or "money market" in f.row.label.lower()]
    elif "checking" in q and not re.search(r"(1st|first) rate|free", q):
        chosen = [f for f in facts if "checking" in f.row.label.lower()]
    else:
        name = next(_named_accounts(q), None)
        chosen = [f for f in facts if name and name.lower() in f.row.label.lower()]
    if not chosen:
        return _refuse_missing(view)
    claimed = re.search(r"(\d+(?:\.\d+)?)\s*%", q)
    if claimed and re.match(r"(do|does|is|are|can)\b", q):
        top = max(chosen, key=lambda f: _top(f.row))
        hit = [f for f in chosen if abs(_top(f.row) - float(claimed.group(1))) < 1e-9]
        if not hit:
            return reply(f"No. {_account(top.row)} tiers top out at {top.row.values['APY']} APY"
                         f" ({top.row.tier}){_effective(chosen)}.", sources_for(view, chosen))
        chosen = hit
    chosen = [f for f in chosen if f.row.tier] or chosen
    amounts = _amounts(q)
    if amounts:
        fits = [f for f in chosen if (r := _range(f.row.tier)) and r[0] <= amounts[0] <= r[1]]
        chosen = fits or chosen
    if re.search(r"\b(highest|best|top|most|maximum|max)\b", q):
        chosen = [max(chosen, key=lambda f: _top(f.row))]
    elif re.search(r"\b(lowest|minimum|least)\b", q):
        chosen = [min(chosen, key=lambda f: _top(f.row))]
    chosen.sort(key=lambda f: (_account(f.row), -_top(f.row), -(_range(f.row.tier) or (0, 0))[0]))
    return _finish(view, chosen, [f"{_account(f.row)} {f.row.tier}: {_describe(f.row)}".replace("  ", " ") for f in chosen])


def _account(row) -> str:
    return row.account or row.title


def _term_loan(q, view, fam, business):
    facts = view.rows(fam)
    if fam == "home_equity":
        want = "heloc" if re.search(r"heloc|line of credit", q) else ("loan" if "loan" in q else "")
        facts = [f for f in facts if want in f"{f.row.title} {f.row.label}".lower()]
    if fam == "recreational":
        subject = re.search(r"boat|rv|camper|motor ?home|atv|snowmobile|jet ?ski", q)
        if subject:
            word = {"camper": "rv", "motorhome": "rv", "motor home": "rv"}.get(subject.group(0), subject.group(0))
            facts = [f for f in facts if word.replace(" ", "") in f.row.title.lower().replace(" ", "")]
    months, years = _terms(q)
    months = months or (years * 12 if years else None)
    if months:
        facts = _by_term(facts, months)
    model = re.search(r"\b(20\d\d)\b", q)
    if model and fam == "recreational":
        y = int(model.group(1))
        facts = [f for f in facts if "Model Year" not in f.row.label or
                 _model_years(f.row.label)[0] <= y <= _model_years(f.row.label)[1]]
    amounts = _amounts(q)
    if amounts:
        fits = [f for f in facts if (r := _range(f.row.label.split(" Months")[0].rsplit(" ", 1)[0])) and r[0] <= amounts[0] <= r[1]]
        facts = fits or facts
    if not facts:
        return _refuse_missing(view)
    if re.search(r"\b(best|lowest|cheapest)\b", q) and not months:   # for a loan, the best rate is the lowest
        facts = [min(facts, key=lambda f: (_top(f.row), f.row.label))]
    facts.sort(key=lambda f: (f.row.title, _months_of(f.row.label) or 0, f.row.label))
    return _finish(view, facts, [f"{f.row.title}, {f.row.label}: {_describe(f.row)}" for f in facts])
