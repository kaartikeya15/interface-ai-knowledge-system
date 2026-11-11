"""Unit tests on small made-up pages (no crawled data). Run from the submission folder:

    python -m unittest discover -s tests -v
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb.common  # noqa: E402,F401  (puts the scorer's contract module on the path)
from contract import Page, Snapshot  # noqa: E402
from kb.llm import Selector  # noqa: E402
from kb.parse import extract_line_facts, extract_rows, page_kind  # noqa: E402
from kb.system import KnowledgeBase, parse_as_of  # noqa: E402

SITE = "https://example-cu.test"


def cert_table(twelve_apy: str, effective: str) -> str:
    return f"""# Rates

Regular Certificates

| Certificate | Dividend Rate | Annual Percentage Yield (APY) | Min. to Earn |
| --- | --- | --- | --- |
| 6 Month | 2.96% | 3.00% | $1,000.00 |
| 12 Month | 3.00% | {twelve_apy} | $1,000.00 |

Rates are effective as of {effective} and are subject to change without notice.
"""


def auto_table(sixty_apr: str, effective: str) -> str:
    return f"""Auto Loans

| Loan Program | Term | Annual Percentage Rate (As Low As) |
| --- | --- | --- |
| 2024-27 Model Year | 60 Months | {sixty_apr} APR |
| 2021-23 Model Year | 60 Months | 5.64% APR |

Rates are effective as of {effective}. All loans are subject to approval.
"""


def page(path: str, markdown: str, title: str = "Example CU") -> Page:
    return Page(url=SITE + path, title=title, markdown=markdown, fetched_at="2026-01-01T00:00:00Z")


def snapshot(sid: str, day: str, pages: list) -> Snapshot:
    return Snapshot(snapshot_id=sid, taken_on=day, pages=pages)


def system(*snaps) -> KnowledgeBase:
    ks = KnowledgeBase()
    for s in snaps:
        ks.ingest(s)
    return ks


MAY = snapshot("01_2026-05-12", "2026-05-12", [
    page("/rates", cert_table("3.05%", "05/01/2026") + auto_table("5.34%", "05/01/2026")),
])
SEP = snapshot("02_2026-09-28", "2026-09-28", [
    page("/rates", cert_table("3.40%", "09/01/2026") + auto_table("5.39%", "09/01/2026")),
    # A calculator nobody updated still carries the May auto table.
    page("/auto-loan-calculator", auto_table("5.34%", "05/01/2026")),
])


class ParseTests(unittest.TestCase):
    def test_page_kinds(self):
        self.assertEqual(page_kind(SITE + "/rates"), "rate_sheet")
        self.assertEqual(page_kind(SITE + "/auto-loan-calculator"), "calculator")
        self.assertEqual(page_kind(SITE + "/newsroom/new-branch"), "news")
        self.assertEqual(page_kind(SITE + "/personal/loans/auto-loans"), "product")

    def test_table_rows_are_typed_facts(self):
        rows = extract_rows(SITE + "/rates", cert_table("3.40%", "09/01/2026"))
        self.assertEqual(len(rows), 2)
        twelve = next(r for r in rows if r.label.startswith("12"))
        self.assertEqual(twelve.family, "certificate")
        self.assertEqual(twelve.values, {"dividend rate": "3.00%", "APY": "3.40%"})
        self.assertEqual(str(twelve.effective), "2026-09-01")

    def test_tiers_get_different_keys(self):
        rows = extract_rows(SITE + "/rates", auto_table("5.39%", "09/01/2026"))
        self.assertEqual(len({r.key for r in rows}), 2, "two model-year tiers are two facts, not one")

    def test_calculators_are_never_a_rate_source(self):
        self.assertEqual(extract_rows(SITE + "/auto-loan-calculator", auto_table("5.34%", "05/01/2026")), [])

    def test_sentence_rates_and_expired_offers(self):
        facts = extract_line_facts(SITE + "/promo", "6-month certificate APY: 3.50%\n\n"
                                                    "9-month special APY: 4.10% (offer ended)")
        self.assertEqual([(f.value, f.expired) for f in facts], [("3.50%", False), ("4.10%", True)])


class CurrentValueTests(unittest.TestCase):
    def setUp(self):
        self.ks = system(MAY, SEP)

    def test_latest_value_and_no_stale_copy(self):
        text = self.ks.answer("What's the 12-month certificate APY?").text
        self.assertIn("3.40%", text)
        self.assertNotIn("3.05%", text)

    def test_stale_calculator_loses_to_rate_sheet(self):
        a = self.ks.answer("What is the APR for a 60-month auto loan on a 2025 model year car?")
        self.assertIn("5.39%", a.text)
        self.assertNotIn("5.34%", a.text)
        self.assertTrue(a.sources[0].url.endswith("/rates"))

    def test_right_tier(self):
        text = self.ks.answer("What is the 60-month auto loan APR for a 2022 model year car?").text
        self.assertIn("5.64%", text)
        self.assertNotIn("5.39%", text)

    def test_newer_effective_date_beats_page_authority(self):
        newer_product = snapshot("03_2026-10-01", "2026-10-01", [
            page("/rates", cert_table("3.40%", "09/01/2026")),
            page("/personal/certificates", cert_table("3.55%", "09/20/2026")),
        ])
        self.assertIn("3.55%", system(newer_product).answer("What's the 12-month certificate APY?").text)

    def test_same_date_goes_to_the_rate_sheet(self):
        tie = snapshot("03_2026-10-01", "2026-10-01", [
            page("/rates", cert_table("3.40%", "09/01/2026")),
            page("/personal/certificates", cert_table("3.45%", "09/01/2026")),
        ])
        self.assertIn("3.40%", system(tie).answer("What's the 12-month certificate APY?").text)


class AsOfTests(unittest.TestCase):
    def setUp(self):
        self.ks = system(MAY, SEP)

    def test_as_of_picks_the_snapshot_on_or_before_the_date(self):
        self.assertIn("3.05%", self.ks.answer("What's the 12-month certificate APY?", as_of="2026-06-30").text)
        self.assertIn("3.40%", self.ks.answer("What's the 12-month certificate APY?", as_of="2026-10-01").text)

    def test_before_the_first_snapshot_is_unknown(self):
        self.assertFalse(self.ks.answer("What's the 12-month certificate APY?", as_of="2026-01-01").confident)

    def test_date_written_in_the_question(self):
        self.assertIn("3.05%", self.ks.answer("What was the 12-month certificate APY in June 2026?").text)

    def test_as_of_formats(self):
        self.assertEqual(str(parse_as_of("2026-05")), "2026-05-31")
        self.assertEqual(str(parse_as_of("2026-05-20T10:00:00Z")), "2026-05-20")
        self.assertIsNone(parse_as_of("next week"))

    def test_change_history(self):
        text = self.ks.answer("Did the 12-month certificate rate change?").text
        self.assertTrue(text.startswith("Yes"))
        self.assertIn("3.40%", text)


class HonestyTests(unittest.TestCase):
    def setUp(self):
        self.ks = system(MAY, SEP)

    def assert_refusal(self, answer):
        self.assertFalse(answer.confident)
        self.assertIsNone(re.search(r"\d", answer.text), "a refusal must not state any value")

    def test_missing_term_is_refused(self):
        self.assert_refusal(self.ks.answer("What's the 7-month certificate APY?"))

    def test_unknown_topic_is_refused(self):
        self.assert_refusal(self.ks.answer("What's your SWIFT code?"))

    def test_odd_input_never_crashes(self):
        for question, as_of in [("", None), ("?", None), ("x" * 5000, None), ("Rate on February 30, 2026?", None),
                                ("What's the 12-month certificate APY?", "not a date")]:
            self.assertIsNotNone(self.ks.answer(question, as_of=as_of).text)

    def test_internal_error_becomes_a_refusal(self):
        def broken(*_):
            raise RuntimeError("simulated bug")
        self.ks._route = broken
        self.assert_refusal(self.ks.answer("What's the 12-month certificate APY?"))

    def test_no_snapshots_yet(self):
        self.assert_refusal(KnowledgeBase().answer("What's the 12-month certificate APY?"))


class ConflictTests(unittest.TestCase):
    def test_changed_value_is_a_conflict_with_the_newest_winner(self):
        found = system(MAY, SEP).conflicts()
        twelve = next(c for c in found if "12 Month" in c.topic)
        self.assertIn("3.05%", twelve.values)
        self.assertIn("3.40%", twelve.resolution)

    def test_unchanged_tiers_are_not_conflicts(self):
        self.assertFalse([c for c in system(MAY, SEP).conflicts() if "6 Month" in c.topic])

    def test_stale_calculator_is_reported(self):
        auto = [c for c in system(MAY, SEP).conflicts() if "auto" in c.topic.lower()]
        self.assertTrue(any("5.34%" in c.values and "5.39%" in (c.resolution or "") for c in auto))


class DeterminismTests(unittest.TestCase):
    def test_same_answers_and_conflicts_every_time(self):
        questions = ["What's the 12-month certificate APY?", "What is the 60-month auto loan APR?", "What's your SWIFT code?"]
        runs = []
        for _ in range(2):
            ks = system(MAY, SEP)
            runs.append(([ks.answer(q).text for q in questions], [(c.topic, c.values) for c in ks.conflicts()]))
        self.assertEqual(runs[0], runs[1])


class LLMLayerTests(unittest.TestCase):
    KEYS = ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "KB_LLM_PROVIDER", "KB_LLM_OFF")

    def setUp(self):
        self.saved = {k: os.environ.pop(k) for k in self.KEYS if k in os.environ}

    def tearDown(self):
        for k in self.KEYS:
            os.environ.pop(k, None)
        os.environ.update(self.saved)

    def test_off_without_a_key(self):
        selector = Selector()
        self.assertFalse(selector.enabled)
        self.assertIsNone(selector.pick("question", ["a passage"]))

    def test_an_anthropic_key_alone_does_not_switch_it_on(self):
        os.environ["ANTHROPIC_API_KEY"] = "test"   # e.g. set for the scorer's judge
        self.assertFalse(Selector().enabled)

    def test_a_failed_call_falls_back_and_stays_fallen_back(self):
        os.environ["GROQ_API_KEY"] = "test"
        selector = Selector()
        replies = iter([OSError("429 Too Many Requests"), '{"pick": 1}'])

        def call(_prompt):
            r = next(replies)
            if isinstance(r, Exception):
                raise r
            return r
        selector._call = call
        self.assertIsNone(selector.pick("question", ["a passage"]))   # rules answer
        self.assertIsNone(selector.pick("question", ["a passage"]))   # same again, though the API would now reply

    def test_reply_parsing(self):
        os.environ["GROQ_API_KEY"] = "test"
        selector = Selector()
        selector._call = lambda _p: 'Sure: {"pick": 2}'
        self.assertEqual(selector.pick("q", ["a", "b"]), 1)
        selector._call = lambda _p: '{"pick": 0}'
        self.assertEqual(selector.pick("q2", ["a", "b"]), -1)
        selector._call = lambda _p: '{"pick": 9}'
        self.assertEqual(selector.pick("q3", ["a", "b"]), -1)   # out of range means none


if __name__ == "__main__":
    unittest.main()
