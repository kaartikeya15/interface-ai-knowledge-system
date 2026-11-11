"""Shared answer helpers, and the contract types the scorer expects."""
import sys
from pathlib import Path

try:
    from contract import Answer, Conflict, Source   # the scorer puts harness/ on the path
except ImportError:
    # Run outside the scorer: use the kit's harness/ folder from a parent directory if there is one.
    harness = next((d / "harness" for d in Path(__file__).resolve().parents if (d / "harness" / "contract.py").exists()), None)
    if harness is None:
        raise ImportError("contract.py not found: run through harness/run_eval.py, or keep this folder inside the kit")
    sys.path.insert(0, str(harness))
    from contract import Answer, Conflict, Source

__all__ = ["Answer", "Conflict", "Source", "REFUSAL", "long_date", "reply", "sources_for"]

# Digit-free on purpose: a refusal must not state any value, including a phone number.
REFUSAL = ("I don't know. The website doesn't list {what}, so I won't guess. "
           "Please contact the credit union directly and a team member can help.")


def long_date(d) -> str:
    """'September 1, 2026'. Built by hand: strftime's '%-d' fails on Windows."""
    return f"{d:%B} {d.day}, {d.year}"


def reply(text, sources=(), confident=True, conflict_note=None):
    return Answer(text=text, sources=list(sources), confident=confident, conflict_note=conflict_note)


def sources_for(view, facts, limit=6):
    """Cite the winning page first, then the other pages that agree with it."""
    out, seen = [], set()
    for r in [f.row for f in facts] + [r for f in facts for r in f.agreeing]:
        if r.url not in seen and len(out) < limit:
            seen.add(r.url)
            out.append(Source(url=r.url, snapshot_id=view.snapshot_id, quote=r.quote))
    return out
