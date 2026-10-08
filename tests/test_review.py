"""Source spans and the review state for step-by-step work.

A line the reader cannot stand behind is never judged: it is carried with where it came
from, transitions touching it are withheld, and no claim of "all steps valid" can survive
replay. Certified findings elsewhere in the work are unaffected.
"""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from algebraic_compiler import notebook, service
from algebraic_compiler.ir import InputError, digest
from algebraic_compiler.linear import run
from algebraic_compiler.linear_ir import normalize
from algebraic_compiler.linear_verifier import verify_bundle

REGION = {"kind": "region", "page": 1, "box": [40, 210, 520, 262]}
STEPS = [["3x + 5 = 20"], ["3x = 15"], ["x = 5"]]


def work(steps=STEPS, notes=None, **extra):
    raw = {"steps": steps, **extra}
    if notes is not None:
        raw["provenance"] = notes
    return raw


def held(reason="AMBIGUOUS_SYMBOL", **extra):
    return {"review": {"reason": reason}, **extra}


def rehash(bundle):
    bundle["certificate"]["input_sha256"] = digest(bundle["input"])
    cert = bundle["certificate"]
    cert["certificate_sha256"] = digest({k: v for k, v in cert.items() if k != "certificate_sha256"})
    return bundle


class ProvenanceInputTests(unittest.TestCase):
    def test_work_without_notes_keeps_its_original_canonical_form(self):
        plain, _ = normalize(work())
        self.assertNotIn("provenance", plain)
        empty, _ = normalize(work(notes=[None, None, None]))
        self.assertEqual(plain, empty)

    def test_notes_are_bound_into_the_input_digest(self):
        _, bare = normalize(work())
        spec, noted = normalize(work(notes=[None, {"source": REGION, "raw": "  3x   = 15 "}, None]))
        self.assertNotEqual(bare, noted)
        self.assertEqual(spec["provenance"][1], {"source": REGION, "raw": "3x = 15"})
        self.assertEqual(normalize(spec)[0], spec)  # canonical form is stable

    def test_malformed_notes_are_rejected_not_repaired(self):
        bad = [
            [None, None],                                                                 # wrong length
            [None, {"source": {"kind": "region", "page": 1, "box": [0, 0, 1.5, 9]}}, None],  # floats
            [None, {"source": {"kind": "region", "page": 1, "box": [50, 0, 10, 9]}}, None],  # inverted box
            [None, {"source": {"kind": "text", "start": 9, "end": 9}}, None],              # empty span
            [None, {"source": {"kind": "image", "uri": "x"}}, None],                       # unknown kind
            [None, {"review": {"reason": "I_DONT_KNOW"}}, None],
            [None, {"confidence": 7}, None],
            "not a list",
        ]
        for notes in bad:
            with self.assertRaises(InputError, msg=notes) as caught:
                normalize(work(notes=notes))
            self.assertEqual(caught.exception.code, "INVALID_SCHEMA", notes)
        with self.assertRaises(InputError):
            normalize({"equations": ["x = 1"], "provenance": [None]})  # work only

    def test_an_unreviewed_line_still_needs_equations(self):
        with self.assertRaises(InputError):
            normalize(work(steps=[["3x + 5 = 20"], [], ["x = 5"]]))


class ReviewStateTests(unittest.TestCase):
    def test_source_span_travels_with_the_first_certified_error(self):
        out = run(work([["3x + 5 = 20"], ["3x = 25"], ["x = 25/3"]],
                       [None, {"source": REGION, "raw": "3x = 25"}, None]))
        self.assertEqual((out["status"], out["claim"]), ("MATHEMATICALLY_REJECTED", "work.error_found"))
        view = service.check_work([["3x + 5 = 20"], ["3x = 25"], ["x = 25/3"]],
                                  provenance=[None, {"source": REGION, "raw": "3x = 25"}, None])["view"]
        self.assertEqual(view["focus"], {"step_index": 1, "source": REGION, "raw": "3x = 25"})
        self.assertNotIn("review", view)

    def test_a_flagged_line_is_not_judged_and_nothing_is_concluded(self):
        notes = [None, held(source=REGION, raw="3x = 1S"), None]
        out = run(work(notes=notes))
        result = out["bundle"]["certificate"]["result"]
        self.assertEqual((out["status"], out["claim"], out["code"]), ("NEEDS_REVIEW", "work.needs_review", "REVIEW_REQUIRED"))
        self.assertTrue(out["certificate_verified"])
        self.assertEqual([t["status"] for t in result["transitions"]], ["needs_review", "needs_review"])
        self.assertEqual(result["review_lines"], [1])
        self.assertIsNone(result["first_error"])
        for t in result["transitions"]:
            self.assertEqual(set(t), {"from", "to", "status"})  # no witness: nothing is claimed
        payload = service.check_work(STEPS, provenance=notes)
        self.assertEqual(payload["verdict"], "needs_review")
        self.assertEqual(payload["view"]["focus"]["source"], REGION)
        self.assertEqual(payload["view"]["review"][0]["reading"], "3x = 15")
        self.assertIn("next=ask the person", service.summary_text(payload))

    def test_steps_around_a_flagged_line_are_still_certified(self):
        steps = [["3x + 5 = 20"], ["3x = 15"], ["x = 5"], ["x = 5"]]
        out = run(work(steps, [None, None, held("AMBIGUOUS_GROUPING"), None]))
        statuses = [t["status"] for t in out["bundle"]["certificate"]["result"]["transitions"]]
        self.assertEqual(statuses, ["sound", "needs_review", "needs_review"])
        self.assertEqual(out["claim"], "work.needs_review")

    def test_a_certified_error_stands_but_an_earlier_review_is_disclosed(self):
        steps = [["3x + 5 = 20"], ["3x = 15"], ["x = 5"], ["x = 6"]]
        before = run(work(steps, [None, held(), None, None]))
        self.assertEqual((before["status"], before["claim"]), ("MATHEMATICALLY_REJECTED", "work.error_found"))
        self.assertEqual(before["bundle"]["certificate"]["result"]["first_error"], 2)
        spoken = service.check_work(steps, provenance=[None, held(), None, None])["view"]["spoken"]
        self.assertIn("Line 2 still needs review, so there may be an earlier slip", spoken)
        # A review line after the broken step cannot hide anything earlier.
        steps = [["3x + 5 = 20"], ["3x = 25"], ["x = 25/3"]]
        payload = service.check_work(steps, provenance=[None, None, held()])
        self.assertEqual(payload["verdict"], "error_found")
        self.assertNotIn("earlier slip", payload["view"]["spoken"])
        self.assertEqual(payload["view"]["review"][0]["step_index"], 2)

    def test_an_unreadable_line_goes_to_review_instead_of_failing_the_run(self):
        for text in ("3x + = 15", "3x ? 15", "3x = 15 = 15", "3x 15"):
            out = run(work([["3x + 5 = 20"], [text], ["x = 5"]]))
            self.assertEqual((out["status"], out["claim"]), ("NEEDS_REVIEW", "work.needs_review"), text)
            note = out["bundle"]["input"]["provenance"][1]
            self.assertEqual((note["review"]["reason"], note["raw"]), ("UNPARSEABLE", " ".join(text.split())), text)
            self.assertEqual(out["bundle"]["input"]["steps"][1], [])
            self.assertTrue(verify_bundle(out["bundle"])["certificate_verified"], text)

    def test_the_hosts_own_note_survives_automatic_review(self):
        out = run(work([["3x + 5 = 20"], ["3x + = 15"], ["x = 5"]], [None, {"source": REGION, "raw": "3x +  = 15"}, None]))
        note = out["bundle"]["input"]["provenance"][1]
        self.assertEqual((note["source"], note["raw"]), (REGION, "3x + = 15"))
        self.assertEqual(note["review"]["reason"], "UNPARSEABLE")

    def test_other_failures_keep_their_own_statuses(self):
        nonlinear = run(work([["3x + 5 = 20"], ["x^2 = 4"], ["x = 2"]]))
        self.assertEqual((nonlinear["status"], nonlinear["code"]), ("UNSUPPORTED", "UNSUPPORTED_NONLINEAR"))
        product = run(work([["3x + 5 = 20"], ["x*x = 4"], ["x = 2"]]))
        self.assertEqual(product["status"], "UNSUPPORTED")
        zero = run(work([["3x + 5 = 20"], ["x/0 = 4"], ["x = 2"]]))
        self.assertEqual((zero["status"], zero["code"]), ("ASSUMPTION_REQUIRED", "DIVISION_BY_ZERO"))
        shape = run({"steps": "not a list"})
        self.assertEqual(shape["code"], "INVALID_SCHEMA")
        self.assertFalse(any(o["certificate_verified"] for o in (nonlinear, product, zero, shape)))
        every = run(work([["3x ? 5"], ["x ? 1"]]))  # nothing readable at all: no unknown to certify
        self.assertEqual((every["status"], every["certificate_verified"]), ("ASSUMPTION_REQUIRED", False))

    def test_first_line_review_and_old_style_inputs_still_work(self):
        out = run(work(notes=[held(), None, None]))
        self.assertEqual(out["bundle"]["certificate"]["result"]["transitions"][1]["status"], "sound")
        self.assertNotIn("original_solution", out["bundle"]["certificate"]["result"])
        self.assertIn("final_solution", out["bundle"]["certificate"]["result"])
        plain = run(work())
        self.assertEqual(plain["claim"], "work.all_steps_valid")
        self.assertNotIn("review_lines", plain["bundle"]["certificate"]["result"])


class ReviewReplayTests(unittest.TestCase):
    def bundle(self):
        return run(work(notes=[None, held(raw="3x = 1S"), None]))["bundle"]

    def test_a_needs_review_certificate_replays_and_rehashed_forgery_fails(self):
        bundle = self.bundle()
        self.assertTrue(verify_bundle(bundle)["certificate_verified"])
        self.assertEqual(service.forge(bundle)["verdict"], "forgery_rejected")
        # With no witness number anywhere, the forgery check still has something to alter and must be caught.
        bare = run(work([["x + y = 2"], ["y = 2 - x"]], [None, held()]))["bundle"]
        self.assertTrue(verify_bundle(bare)["certificate_verified"])
        forged = service.forge(bare)
        self.assertEqual((forged["verdict"], forged["changed_field"]), ("forgery_rejected", "lines"))

    def test_cannot_be_relabelled_as_all_valid_or_error(self):
        for claim in ("work.all_steps_valid", "work.error_found"):
            changed = self.bundle()
            changed["certificate"]["claim"] = claim
            report = verify_bundle(rehash(changed))
            self.assertFalse(report["certificate_verified"], claim)
            self.assertEqual(report["message"], "Check failed: verdict", claim)

    def test_cannot_pass_a_withheld_step_off_as_sound(self):
        changed = self.bundle()
        step = changed["certificate"]["result"]["transitions"][0]
        step.update(status="sound", combinations=[[{"num": 1, "den": 1}]])
        report = verify_bundle(rehash(changed))
        self.assertFalse(report["certificate_verified"])
        self.assertEqual(report["message"], "Check failed: withheld_for_review")

    def test_dropping_the_review_flag_changes_the_input_and_fails_closed(self):
        changed = self.bundle()
        changed["input"]["steps"][1] = ["3x = 15"]
        del changed["input"]["provenance"]
        report = verify_bundle(changed)
        self.assertEqual(report["code"], "INPUT_DIGEST_MISMATCH")
        report = verify_bundle(rehash(changed))  # even with fresh hashes, the old verdicts do not fit
        self.assertFalse(report["certificate_verified"])

    def test_hiding_the_review_list_is_caught(self):
        changed = self.bundle()
        del changed["certificate"]["result"]["review_lines"]
        report = verify_bundle(rehash(changed))
        self.assertEqual(report["message"], "Check failed: review_lines")

    def test_a_judgement_cannot_be_attached_to_a_review_line(self):
        changed = self.bundle()
        result = changed["certificate"]["result"]
        result["original_solution"] = {"solution": [{"num": 5, "den": 1}], "derivations": [[{"num": 1, "den": 1}]]}
        report = verify_bundle(rehash(changed))
        self.assertFalse(report["certificate_verified"])

    def test_verifier_still_never_imports_the_producer(self):
        import ast
        source = (Path(__file__).resolve().parents[1] / "algebraic_compiler" / "linear_verifier.py").read_text()
        modules = {node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)}
        self.assertNotIn("linear", modules)


class ReviewNotebookTests(unittest.TestCase):
    def test_an_unresolved_run_is_not_saved_but_its_resolution_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "nb.sqlite3")
            with patch.object(notebook, "_shared", None), patch.dict("os.environ", {"SYW_NOTEBOOK_PATH": path}):
                try:
                    pending = service.check_work(STEPS, notebook="fam", learner="maya", provenance=[None, held(), None])
                    self.assertEqual((pending["verdict"], pending["saved"]), ("needs_review", None))
                    self.assertEqual(service.history("fam")["entries"], [])
                    resolved = service.check_work(STEPS, notebook="fam", learner="maya")
                    self.assertEqual(resolved["saved"]["learner"], "maya")
                finally:
                    notebook.reset_shared()


if __name__ == "__main__":
    unittest.main()
