"""Shared answer helpers."""
import sys
from pathlib import Path

try:
    from contract import Answer, Conflict, Source
except ImportError:  # run from outside the scorer
    sys.path.insert(0, str(next(d / "harness" for d in Path(__file__).resolve().parents if (d / "harness").exists())))
    from contract import Answer, Conflict, Source  # noqa: F401

# Digit-free on purpose: a refusal must not state any value, including a phone number.
REFUSAL = ("I don't know. The website doesn't list {what}, so I won't guess. "
           "Please contact the credit union directly and a team member can help.")


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
