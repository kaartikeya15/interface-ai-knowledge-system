# Knowledge system: what's true *now* on a credit union's website

Answers member questions from five crawls of the website, picking the current, correctly tiered value. It cites the source, answers as of any date, says "I don't know" when the site doesn't say, and lists where the site contradicts itself.

- **No packages, no API keys.** Python standard library, 3.9 to 3.13. An optional LLM selector switches on with a free Groq key (`GROQ_API_KEY`).
- **Fast.** Ingests five snapshots in about 3.5 s; the slowest answer takes 0.05 s.
- **Deterministic.** The same answers and conflicts on every run.
- **Cannot invent a number.** Answers quote the site; they are never generated.

How it decides what is current, and why: [NOTES.md](NOTES.md).

## Run it

The crawled snapshots are not in this repository (the data may not be redistributed). Copy the kit's `snapshots_zip/` folder next to `harness/`, then from the kit's top folder:

```bash
python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl   # practice set: 0.969
python -m unittest discover -s submission/tests -v                                 # 39 unit tests, made-up pages only
```

## How it works

```
 ingest(snapshot)                              answer(question, as_of)
       |                                              |
 parse.py   tables -> typed rows                 pick the View for as_of
            (product, tier, term, metric,              |
             value, effective date, page kind)   handlers: rates, routing, branches, hours,
       |                                           holidays, fees, organization, links ...
 view.py    one View per snapshot;                     |  none claimed it
            same fact on many pages -> one winner  site FAQs -> passage search (BM25)
            (newest effective date, then           with refusal gates
             rate sheet > product > news > blog)       |
       |                                         verbatim quote + source, or "I don't know"
 conflicts.py  across Views: values that moved,
               stale copies, drifting claims
```

| Module | Job |
|---|---|
| `kb/parse.py` | Page kinds, rate-table rows, sentence-stated rates, byline dates |
| `kb/view.py` | One snapshot as one resolved picture of the site |
| `kb/system.py` | The contract object: as-of routing, "did it change?" history, free-text answers |
| `kb/rates.py` | Rate questions to the exact product, tier and term |
| `kb/site.py` | Non-rate facts: routing number, branches, hours, fees, contact |
| `kb/search.py` | BM25 passage index and FAQ matching |
| `kb/conflicts.py` | Contradictions and their winners |
| `kb/site_profile.py` | Everything specific to this website, in one place |
| `kb/llm.py` | Optional passage selector: picks among retrieved passages, never writes text (off without a key; see NOTES) |

## Results

| Set | Score |
|---|---|
| Public practice set (40) | 0.969, no stale values repeated |
| Own held-out sets (171 questions in four sets) | 0.77 to 1.0; see NOTES for first-run scores |
| Eight fresh test rounds (319 questions), first runs | Last three in a row: 0.859, 0.854, 0.895 (no stale values) |
| Kit self-test | 0.83 (conflict F1 1.0) |
| With the optional LLM (Groq free tier) | fresh2 0.954 to 0.996, fresh3 0.769 to 0.938; no question worse |
