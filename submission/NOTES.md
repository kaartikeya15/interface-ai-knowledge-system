# How this system works

It needs **no API keys and no packages** (an optional LLM layer, described below, switches on only with a Groq key). It runs on the Python standard library, ingests all five snapshots in about 3.5 seconds, and answers in under 0.05 seconds. I chose this on purpose: the facts that matter sit in tables, and a deterministic parser cannot invent a rate, gives the same answer every time, and leaves a trail you can inspect. The cost is weaker matching of everyday wording, covered under Limits.

## Pipeline

1. **Parse** (`kb/parse.py`). Every rate table row becomes a typed record: product family, tier, term, values, source URL and stated effective date. Table layouts changed between crawls, so tables are classified from headers and nearby titles together. Rates stated in sentences ("6-month certificate APY: 3.50%") are kept as line facts, with expired offers marked.
2. **Resolve** (`kb/view.py`). Each snapshot becomes a `View` of the site on that date. Rows with the same product, tier and term are one fact, and one row wins.
3. **Answer** (`kb/system.py`). Questions go to the most specific handler first: rates, routing number, branches, hours, holidays, links, organization facts, fees. Anything else goes to site FAQs, then passage search.
4. **Conflicts** (`kb/conflicts.py`). One entry per product and term, so tiers never look like contradictions.

Everything specific to this website (page paths, the press-release boilerplate, account names, the calculator's table layout) sits in `kb/site_profile.py`. The rest is a general engine; another credit union needs a new profile, though its handlers would still need checking against that site's wording.

## How I decide what is current

In order:

1. **The as-of date picks the snapshot:** the latest snapshot taken on or before that date. A date written in the question ("on May 15, 2026", "in May 2026") counts as an as-of date if it is before the newest snapshot; a month means its last day. Before the first snapshot, the answer is "I don't know".
2. **The newest stated effective date wins.** Crawl date proves nothing here: a stale calculator page and a stale mortgage widget were recrawled every time. `/homebuying-guidance` still showed April rates in the September crawl.
3. **Then page authority:** rate sheet, then product page, then press release or file, then blog, then calculator. Calculators are never a source for a rate.
4. **A tier found only on a promotional page is retired.** `/switch-and-earn` still lists a `2023-26` auto tier the rate sheet dropped.
5. **For claims that drift, the newest dated release wins, not the majority.** "23 branch office locations" appears on more pages than 22, but releases switch from 23 to 22 around the Hy-Vee branch closing on March 31, 2026. A vote gives the wrong answer.
6. **Table rates beat sentences.** Sentence-stated rates are used only for a product that has no rate table, so marketing copy can never override the rate sheet.

## How answers are chosen

- **"Did it change?"** questions are answered at every snapshot. The reply says whether the value moved, and when. Wording changes such as "$0.00" becoming "$0" do not count as a change. The same route takes "what was it before it changed?", "when did it last change?" and "is it higher now than in June 2026?". A change question runs up to today, or to the later of two dates ("between May 2026 and August 2026"), so a date in it is not mistaken for an as-of date.
- **Counts from annual releases** ("how many employees used VTO") give the newest figure with its year, plus the previous release's figure. Each release reports a different year, so both are facts, not a hedge.
- **An answer must mention the question's most specific word.** For a short question that is its rarest word; for a wordy one, two of its four rarest. A word the site never uses counts as the most specific of all, so "What's your SWIFT code?" is refused instead of matched to a page about "code entry".
- **Product pages speak for Dupaco.** When a product page and an article both match, the product page wins. A member asking about a service is not answered from a loosely related article about someone else.
- **Cost questions need a price.** A passage without a dollar amount, a percentage or "free" cannot answer "how much".
- **FAQs are matched question to question.** The member's words count in full and words only the FAQ has count half. Word forms are merged ("deposited" and "deposit"), and a few member terms map to site terms ("overdraw" to "overdrawn", "cashier's check" to "official check").

## When it says "I don't know"

- A question names a product variant that no published rate row mentions ("construction loan", "7/1 ARM").
- The term or tier does not exist.
- A fee is not on the schedule and nothing else on the site prices it. The schedule calls itself non-inclusive, so a miss there sends the question on to the rest of the site.
- Hours are requested for a branch, or a holiday, with no published schedule. A blog mention is not enough.
- "Do you offer X?" appears only in news or blogs. A scam warning naming Zelle does not mean Dupaco offers Zelle.
- A town has no branch on the locator page and no mention elsewhere. The locator lists nearby branches only, so the site is searched for the town before refusing.

Refusals contain no digits on purpose: they must not state any value.

## Testing

Answer keys in my own sets were read from the raw snapshot files. "First run" means the set was scored before I looked at any of its failures. That is the only clean estimate, because every set was used for fixes afterwards.

| Set | Questions | First run | Now | What it covers |
|---|---|---|---|---|
| Public practice set | 40 | 0.946 | **0.969** | One item cannot score, explained below; no stale values repeated |
| `eval_own/held_out.jsonl` | 85 | not clean | 1.0 | Paraphrases, as-of dates and refusals |
| `eval_own/fresh.jsonl` | 35 | 0.857 | 1.0 | Mixed |
| `eval_own/fresh2.jsonl` | 35 | 0.571 before the second round of fixes, **0.796** after | 0.954 | Fees, site FAQs, past dates, refusals |
| `eval_own/fresh3.jsonl` | 16 | **0.706** | 0.769 | Everyday questions, mostly site FAQs |
| Kit self-test (synthetic site) | 5 + 1 conflict | 0.17 overall | **0.83** overall | Sentence-stated rates and an expired offer; conflict F1 1.0 |

**My estimate for unseen questions:** about 0.95 on table questions like the public set, and 0.70 to 0.80 on everyday questions answered by site FAQs. The hidden set's mix decides where it lands.

**Conflicts:** the kit ships no gold list, so I built a stand-in from the 7 public questions whose trap value really appears in the snapshots. The system lists all 7 with the right winner when they are scored together. Scored alone, the 84-month auto entry loses its match to the 60-month one, because the scorer takes the first entry that shares a value. 7 of its 17 entries match the stand-in, which is a floor on precision, since the real list is longer. Several public traps (a routing number of 271176899, a 9.9% card APR, a 6.7% mortgage rate, `info.dupaco.com` links) appear in no snapshot, so I treated them as errors by an older system, not as site contradictions.

**Why conflicts are one entry per product and term:** I tried one entry per tier (34 entries instead of 17). On the stand-in it scored worse: F1 fell from 0.58 to 0.34, and the right winner from 1.0 to 0.86, because the scorer matched the 84-month entry to the wrong tier. Merged entries say plainly that tiers are separate facts, give each tier's current value in the resolution, and list the superseded values. A scan of the newest snapshot found no sentence-stated rate that contradicts a rate table and goes unreported. The one mismatch is a separate program's rate in a press release.

**Unit tests** (`tests/test_kb.py`, 31 tests, stdlib `unittest`). They use made-up pages, so no crawled data is copied. They cover:
- table parsing into typed rows;
- tiers as separate facts;
- calculators never being a source;
- a newer effective date beating page authority, and the rate sheet winning a tie;
- as-of selection, including a date written in the question;
- other ways of asking about change, and a change window ending at its later date;
- refusals with no digits;
- odd input, conflicts, determinism, and the LLM layer (off without a key, falls back and stays fallen back after an error, reads replies safely).

To check the tests catch real bugs, I broke the code five ways (authority before date, calculators as sources, a digit in refusals, no crash guard, as-of ignored). Each break failed at least one test.

**Robustness:**
- `answer()` never raises. An internal error becomes an honest refusal.
- `as_of` accepts "2026-05" (the end of May), "2026" and full timestamps. An unreadable value means "now".
- Before this fix, `as_of="2026-05"` and a question containing "February 30, 2026" crashed. A crash scores zero.
- One malformed page cannot stop a snapshot from loading, and one failing conflict detector loses only its own entries.
- On all 268 questions I have, none of these guards fires: they protect, they do not hide errors.
- Dates in answers are written without `strftime("%-d")`, which fails on Windows.
- If the scorer's `contract` module cannot be found, the import fails with a clear message instead of a bare `StopIteration`.

**Other checks:**
- Identical answers and conflicts across separate processes with different hash seeds.
- Python 3.9, 3.11 and 3.13.
- Worst answer 0.05 seconds; ingest about 3.5 seconds.
- The cleanups above (site profile, named thresholds, splitting the free-text path into steps) changed no answer: the output hash is identical before and after.

## The optional LLM layer

Everyday wording that differs from the site's ("will mobile payments cost me extra?" against "is there a fee to use Apple Pay") is the main remaining gap. `kb/llm.py` adds an optional selector for it.

**What it may do:**
- It only picks which retrieved FAQ or passage answers the question, or none. The answer is still a verbatim quote, so it cannot state a number of its own.
- **It is off unless `GROQ_API_KEY` is set**, and every score above this section is without it. Groq's free tier is enough. An Anthropic key alone never switches it on, because the kit's scorer may hold one for its judge, and that path is untested.
- Rate questions that the rate tables answer never reach it. For any that fall through, it may only choose passages from the rate sheet or product pages that state a rate.
- **It runs after the rules, not before them.** The rules answer first. When they answer from a named subject's newest release ("Forbes named Dupaco a top employer for the fifth time") or from yearly releases (two years of a count), that answer stands. Those routes apply "newest dated release wins", which a model shown one passage at a time cannot. My first version asked the model first, and it lost three public questions this way.
- Calls time out after 1.8 seconds, inside the 2-second speed target. With no key, an error, a timeout or a rate limit, the rules answer. A question that fell back keeps the rules answer for the rest of the run, so asking twice gives the same answer.

**Model:** `qwen/qwen3.8-27b` on Groq, at temperature 0. `openai/gpt-oss-120b` gave identical scores on every set but answers more slowly (median 0.48 s against 0.31 s).

**Measured, scored with the kit's scorer:**

| Set | Rules only | With LLM, paced | With LLM, unpaced |
|---|---|---|---|
| Public practice set | 0.969 | 0.969 | 0.969 |
| `held_out` | 1.0 | 1.0 | n/a |
| `fresh` | 1.0 | 1.0 | n/a |
| `fresh2` | 0.954 | **0.996** | 0.987 |
| `fresh3` | 0.769 | **0.938** | 0.822 |

- "Paced" spaces the calls under the free tier's limit of 8,000 tokens a minute, about six LLM questions a minute. "Unpaced" is how a scorer would run it: some calls hit the limit and fall back to the rules.
- With the LLM on, no question scored lower than rules-only, 7 scored higher, and no stale value appeared. Unpaced runs had no errors, consistency stayed at 1.0, and the slowest answer took 0.8 s.
- The LLM was not tuned on these sets. The routing rule above was fixed after seeing the public losses, so the public numbers are not a clean test of it.

**On 57 messy questions with no answer key** (`eval_own/probe_messy.txt`), it changed 17 answers. By my reading, 12 got better, 2 got worse, and 3 were about the same.
- Better: "what credit score do I need for a mortgage" now gives the 620 minimum instead of a debt-consolidation FAQ. It also refuses three questions where the rules quoted something irrelevant: password reset, ATM limit, mortgage approval time.
- Worse: it refuses "do you do business loans" and "how do I order checks", which the site does cover.
- I added two general rules to the prompt: "offers it" counts as an answer, and personal is the default unless the question mentions a business. They fixed the two personal-vs-business mistakes and changed nothing else. Answers that did not change were identical across separate runs.

## Limits

- **Everyday wording is the weak spot.** Matching is lexical. When a member's words share nothing with the site's, the system refuses or picks a weaker passage.
- **Answers are extractive.** They quote the site rather than summarise it, so they can read awkwardly.
- **Snapshots are treated as full copies of the site.** A page missing from a later snapshot counts as removed. That is right for these Dupaco crawls; the self-test's partial second snapshot loses one fee question because of it.
- **Rule coverage is English question patterns.** Unusual phrasing can miss a handler and fall through to search.
- **As-of uses `taken_on`, not `fetched_at`.** A page fetched a few days before a snapshot is treated as seen on the snapshot date.

## Public items that will not score

- `dup-005` has an empty `must_include`, which the scorer marks 0 unless the LLM judge is on. The system returns the full certificate list.
- `dup-088` expects 294 employees, which is the 2023 figure. The newest release says 256 for 2024. The answer now gives both with their years, which scores without breaking the newest-source rule.
