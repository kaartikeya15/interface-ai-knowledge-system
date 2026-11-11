# interface.ai take-home: a knowledge system that knows what's true *now*

My solution is in **[`submission/`](submission/)**:
- [`submission/README.md`](submission/README.md) is the overview: architecture, module map and results.
- [`submission/NOTES.md`](submission/NOTES.md) explains how the system decides what is current, and how it was tested.

- **No packages and no API keys.** It runs on the Python standard library. An optional LLM selector switches on with a free Groq key (`GROQ_API_KEY`).
- **Practice set: 0.969**, with no stale values repeated.
- **Fast:** the slowest answer takes 0.05 s.
- **Deterministic**, with 28 unit tests.

## Run from a fresh clone

The website snapshots are not in this repository, because the kit's data may not be redistributed. Copy the kit's `snapshots_zip/` folder into the repository root, then:

```bash
python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl
python -m unittest discover -s submission/tests -v
```

## What else is here

| Path | What it is |
|---|---|
| `harness/`, `eval/`, `starter/`, `snapshots/README.md`, `KIT_README.md` | Unchanged from the kit: the scorer, practice questions, starter solution and the brief |
| `eval_own/` | My own question sets with answer keys read from the raw snapshots, used for the held-out scores in NOTES |
