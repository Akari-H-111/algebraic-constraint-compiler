"""Family notebook: only verified results persist, and every stored bundle is replayed before counting."""

from datetime import datetime, timedelta, timezone
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from algebraic_compiler import notebook, service
from algebraic_compiler.ir import InputError
from algebraic_compiler.linear import run


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "nb.sqlite3")
        self.book = notebook.Notebook(self.path)

    def tearDown(self):
        self.book.close()
        self.tmp.cleanup()

    def test_saves_only_verified_bundles_and_deduplicates(self):
        bundle = run({"equations": ["x + 1 = 3"]})["bundle"]
        first = self.book.save("Rivera Family", bundle, "unique_solution", "x = 2", "Maya")
        again = self.book.save("rivera family", bundle, "unique_solution", "x = 2", "maya")
        self.assertEqual(first["entry_id"], again["entry_id"])
        self.assertEqual((first["notebook"], first["learner"]), ("rivera family", "maya"))
        forged = service.forge(bundle)
        tampered = json.loads(json.dumps(bundle))
        tampered["certificate"]["result"]["solution"][0]["num"] = 9
        with self.assertRaises(InputError) as caught:
            self.book.save("rivera family", tampered, "unique_solution", "x = 9")
        self.assertEqual(caught.exception.code, "UNVERIFIED_BUNDLE")
        self.assertFalse(forged["certificate_verified"])
        self.assertEqual(len(self.book.history("rivera family")), 1)

    def test_names_are_validated(self):
        bundle = run({"equations": ["x = 1"]})["bundle"]
        for bad in ("", "../etc", "x" * 41, "semi;colon"):
            with self.assertRaises(InputError):
                self.book.save(bad, bundle, "unique_solution", "t")

    def test_progress_replays_storage_and_excludes_corrupted_rows(self):
        now = datetime(2026, 9, 29, tzinfo=timezone.utc)
        good = run({"steps": [["3x + 5 = 20"], ["3x = 25"]]})["bundle"]
        self.book.save("fam", good, "error_found", "Line 1 -> 2", "maya", "SIGN_WHEN_MOVING", now=now)
        other = run({"equations": ["2x = 4"], "answer": {"x": "2"}})["bundle"]
        saved = self.book.save("fam", other, "correct", "Correct", "maya", now=now - timedelta(days=1))
        old = run({"equations": ["x = 3"]})["bundle"]
        self.book.save("fam", old, "unique_solution", "old", "maya", now=now - timedelta(days=30))
        # Corrupt one stored row directly: it must not be counted.
        corrupted = json.loads(json.dumps(other))
        corrupted["certificate"]["result"]["sides"][0][0]["num"] += 1
        self.book._db.execute("UPDATE entries SET bundle = ? WHERE id = ?", (json.dumps(corrupted), saved["entry_id"]))
        self.book._db.commit()
        report = self.book.progress("fam", "maya", days=7, now=now)
        self.assertEqual(report["entries"], 2)
        self.assertEqual(report["replayed_and_verified"], 1)
        self.assertEqual(report["failed_replay"], [saved["entry_id"]])
        self.assertEqual(report["hint_patterns"], {"SIGN_WHEN_MOVING": 1})

    def test_service_progress_summary_speaks_from_replayed_entries(self):
        with patch.object(notebook, "_shared", None), patch.dict("os.environ", {"SYW_NOTEBOOK_PATH": self.path}):
            service.check_work([["3x + 5 = 20"], ["3x = 25"]], notebook="fam", learner="Maya")
            service.check_answer(["2x - 7 = 1"], {"x": "4"}, notebook="fam", learner="Maya")
            service.solve(["x + y = 10", "x - y = 2"], notebook="fam", learner="Maya")
            service.practice("two_step", 3, notebook="fam", learner="Maya")
            payload = service.progress("fam", "Maya")
            notebook.reset_shared()
        spoken = payload["view"]["spoken"]
        self.assertIn("Maya's work was checked 2 times: 1 right and 1 mistake pinpointed exactly", spoken)
        self.assertIn("1 problem solved with proofs", spoken)
        self.assertIn("1 practice problem with verified answer keys", spoken)
        self.assertIn("keeping the sign right", spoken)
        self.assertTrue(payload["certificate_verified"])


if __name__ == "__main__":
    unittest.main()
