"""Exact linear certificates: producer, independent verifier, explanations and adversarial replay."""

import ast
import copy
from fractions import Fraction
from itertools import product
from pathlib import Path
import random
import unittest
from unittest.mock import patch

from algebraic_compiler import linear, service
from algebraic_compiler.ir import digest
from algebraic_compiler.linear import practice, run
from algebraic_compiler.linear_ir import exact_number, normalize, unknowns_in
from algebraic_compiler.linear_verifier import verify_bundle
from algebraic_compiler.linear_view import explain

ROOT = Path(__file__).resolve().parents[1]


def q(value):
    return Fraction(value["num"], value["den"])


def result_of(raw):
    out = run(raw)
    return out, out["bundle"]["certificate"]["result"] if out["bundle"] else None


def rehash(bundle):
    cert = bundle["certificate"]
    cert["certificate_sha256"] = digest({k: v for k, v in cert.items() if k != "certificate_sha256"})
    return bundle


class ReadingTests(unittest.TestCase):
    def test_implicit_multiplication_parentheses_decimals_and_symbols(self):
        out, result = result_of({"equations": ["2(x − 3) = 1/2x + 0.25"]})
        self.assertEqual(out["claim"], "solve.unique")
        self.assertEqual(q(result["solution"][0]), Fraction(25, 6))
        out, result = result_of({"equations": ["3×a + b = 7", "a − b = 1"]})
        self.assertEqual([q(v) for v in result["solution"]], [Fraction(2), Fraction(1)])

    def test_nonlinear_and_malformed_inputs_are_distinct_statuses(self):
        for text in ("x*y = 3", "x/(y - 1) = 2", "x^2 = 4", "x² = 4", "(x + 1)(x - 1) = 0"):
            out = run({"equations": [text]})
            self.assertEqual((out["status"], out["code"]), ("UNSUPPORTED", "UNSUPPORTED_NONLINEAR"), text)
            self.assertFalse(out["certificate_verified"])
            self.assertIsNone(out["bundle"])
        for text, code in (("x + 1", "INVALID_EQUATION"), ("x = 1 = 2", "INVALID_EQUATION"),
                           ("x = 1/0", "DIVISION_BY_ZERO"), ("x = $5", "INVALID_EQUATION"), ("x = (1", "INVALID_EQUATION")):
            out = run({"equations": [text]})
            self.assertEqual((out["status"], out["code"]), ("ASSUMPTION_REQUIRED", code), text)
        self.assertEqual(run({"equations": ["x = " + "9" * 29 + "/" + "7" * 29]})["status"], "VERIFIED")
        self.assertEqual(run({"equations": ["x = " + "9" * 31]})["status"], "INCOMPLETE_RESOURCE_LIMIT")
        self.assertEqual(run({"equations": [f"x{i} = {i}" for i in range(13)]})["status"], "ASSUMPTION_REQUIRED")

    def test_multiplying_a_cancelled_unknown_is_still_structurally_nonlinear(self):
        self.assertEqual(run({"equations": ["(x - x) * x = 0"]})["code"], "UNSUPPORTED_NONLINEAR")

    def test_exact_numbers_and_canonical_input(self):
        self.assertEqual(exact_number("-3/2"), Fraction(-3, 2))
        self.assertEqual(exact_number("0.125"), Fraction(1, 8))
        self.assertEqual(exact_number({"num": 2, "den": 4}), Fraction(1, 2))
        spec, sha = normalize({"equations": ["  x   +y=3 ", "x−y = 1"]})
        self.assertEqual(spec["equations"], ["x + y = 3", "x - y = 1"])
        self.assertEqual(normalize({"equations": ["x+y=3", "x - y=1"]})[1], sha)  # spacing never changes the digest
        rng = random.Random(7)
        alphabet = ["x", "y", "2", "3.5", "+", "-", "*", "/", "(", ")", " ", "ab", "1"]
        from algebraic_compiler.linear_ir import normalize_text, tokens
        for _ in range(500):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12))) + "=" + rng.choice(["1", "x", "-2"])
            try:
                shown = normalize_text(text)
            except Exception:
                continue
            self.assertEqual(tokens(shown), tokens(text), text)
        self.assertEqual(unknowns_in(spec["equations"]), ["x", "y"])
        self.assertEqual(sha, normalize(spec)[1])


class SolveTests(unittest.TestCase):
    def test_unique_solution_with_isolating_combinations(self):
        out, result = result_of({"equations": ["a + c = 20", "12a + 7c = 200"]})
        self.assertEqual((out["status"], out["claim"]), ("VERIFIED", "solve.unique"))
        self.assertEqual([q(v) for v in result["solution"]], [12, 8])
        rows = [[q(x) for x in row] for row in result["matrix"]]
        for j, weights in enumerate(result["derivations"]):
            combined = [sum(q(w) * row[k] for w, row in zip(weights, rows)) for k in range(2)]
            self.assertEqual(combined, [int(k == j) for k in range(2)])

    def test_no_valid_answer_in_domain(self):
        out, result = result_of({"equations": ["a + c = 20", "12a + 7c = 300"], "domain": "nonnegative_integer"})
        self.assertEqual((out["status"], out["claim"]), ("MATHEMATICALLY_REJECTED", "solve.no_solution_in_domain"))
        self.assertEqual(result["domain_violation"], {"unknown": 1, "reason": "negative"})
        out, result = result_of({"equations": ["2n = 5"], "domain": "integer"})
        self.assertEqual(result["domain_violation"]["reason"], "not_integer")

    def test_contradiction_witness(self):
        out, result = result_of({"equations": ["x + y = 3", "2x + 2y = 7"]})
        self.assertEqual((out["status"], out["claim"]), ("MATHEMATICALLY_REJECTED", "solve.no_solution"))
        weights = [q(w) for w in result["combination"]]
        self.assertEqual(weights[0] * 1 + weights[1] * 2, 0)
        self.assertNotEqual(weights[0] * 3 + weights[1] * 7, 0)

    def test_complete_family_and_undecided_domain(self):
        out, result = result_of({"equations": ["x + y + z = 6", "x - y = 0"]})
        self.assertEqual(out["claim"], "solve.family")
        self.assertEqual(len(result["kernel"]), 1)
        self.assertEqual(result["free_unknowns"], [2])
        out = run({"equations": ["x + y + z = 6", "x - y = 0"], "domain": "integer"})
        self.assertEqual((out["status"], out["claim"], out["code"]), ("VERIFIED", "solve.family_domain_undecided", "DOMAIN_NOT_DECIDED"))

    def test_randomized_systems_agree_with_brute_force_and_replay(self):
        rng = random.Random(20260929)
        for _ in range(250):
            n, m = rng.randint(1, 3), rng.randint(1, 3)
            names = ["x", "y", "z"][:n]
            rows = [[rng.randint(-3, 3) for _ in range(n)] for _ in range(m)]
            rhs = [rng.randint(-5, 5) for _ in range(m)]
            eqs = [" + ".join(f"({c}){v}" for c, v in zip(row, names)) + f" = {b}" for row, b in zip(rows, rhs)]
            out = run({"equations": eqs})
            if not any(any(row) for row in rows) and not any(rhs):
                continue
            self.assertTrue(out["certificate_verified"], eqs)
            self.assertTrue(verify_bundle(out["bundle"])["certificate_verified"])
            result = out["bundle"]["certificate"]["result"]
            # Brute force over a small grid: any grid solution must be consistent with the claim.
            grid = [p for p in product(range(-6, 7), repeat=n)
                    if all(sum(c * x for c, x in zip(row, p)) == b for row, b in zip(rows, rhs))]
            if out["claim"] == "solve.no_solution":
                self.assertEqual(grid, [])
            elif out["claim"] == "solve.unique":
                solution = tuple(q(v) for v in result["solution"])
                self.assertTrue(all(tuple(Fraction(v) for v in p) == solution for p in grid))


class AnswerAndWorkTests(unittest.TestCase):
    def test_answer_checks(self):
        out, result = result_of({"equations": ["3x + 5 = 20"], "answer": {"x": "4"}})
        self.assertEqual((out["status"], out["claim"]), ("MATHEMATICALLY_REJECTED", "answer.incorrect"))
        self.assertEqual([[q(a), q(b)] for a, b in result["sides"]], [[17, 20]])
        self.assertEqual(q(result["unique_solution"]["solution"][0]), 5)
        out, _ = result_of({"equations": ["3x + 5 = 20"], "answer": {"x": "5"}})
        self.assertEqual((out["status"], out["claim"]), ("VERIFIED", "answer.correct"))
        out, result = result_of({"equations": ["2n = 5"], "answer": {"n": "5/2"}, "domain": "integer"})
        self.assertEqual(out["claim"], "answer.incorrect")
        self.assertEqual(result["failing_equations"], [])
        self.assertEqual(run({"equations": ["x + y = 1"], "answer": {"x": "1"}})["code"], "ASSUMPTION_REQUIRED")
        self.assertEqual(run({"equations": ["x = 1"], "answer": {"x": "1", "z": "2"}})["code"], "INVALID_SCHEMA")

    def test_first_error_hint_and_sound_steps(self):
        out, result = result_of({"steps": [["3x + 5 = 20"], ["3x = 25"], ["x = 25/3"]]})
        self.assertEqual((out["claim"], result["first_error"]), ("work.error_found", 0))
        self.assertEqual(result["transitions"][0]["hint"]["code"], "SIGN_WHEN_MOVING")
        self.assertEqual(q(result["transitions"][0]["point"][0]), 5)
        self.assertEqual(result["transitions"][1]["status"], "sound")
        out, result = result_of({"steps": [["3x = 15"], ["x = 15"]]})
        self.assertEqual(result["transitions"][0]["hint"]["code"], "ONE_SIDE_ONLY")
        out, result = result_of({"steps": [["x + y = 10", "x - y = 2"], ["2x = 12"], ["x = 6"]]})
        self.assertEqual(out["claim"], "work.all_steps_valid")

    def test_breaking_point_can_come_from_the_family(self):
        out, result = result_of({"steps": [["x + y = 2"], ["x = 2"]]})
        self.assertEqual(out["claim"], "work.error_found")
        point = [q(v) for v in result["transitions"][0]["point"]]
        self.assertEqual(point[0] + point[1], 2)
        self.assertNotEqual(point[0], 2)

    def test_vacuous_step_after_a_contradiction(self):
        out, result = result_of({"steps": [["x + y = 1", "x + y = 2"], ["x = 7"]]})
        self.assertEqual(result["transitions"][0]["status"], "vacuous")
        self.assertEqual(out["claim"], "work.all_steps_valid")


class AdversarialReplayTests(unittest.TestCase):
    cases = [
        {"equations": ["a + c = 20", "12a + 7c = 200"]},
        {"equations": ["a + c = 20", "12a + 7c = 300"], "domain": "nonnegative_integer"},
        {"equations": ["x + y = 3", "2x + 2y = 7"]},
        {"equations": ["x + y + z = 6", "x - y = 0"]},
        {"equations": ["3x + 5 = 20"], "answer": {"x": "4"}},
        {"equations": ["3x + 5 = 20"], "answer": {"x": "5"}},
        {"steps": [["3x + 5 = 20"], ["3x = 25"], ["x = 25/3"]]},
        {"steps": [["3x + 5 = 20"], ["3x = 15"], ["x = 5"]]},
    ]

    def test_every_claim_type_replays_and_rehashed_forgeries_fail(self):
        for raw in self.cases:
            bundle = run(raw)["bundle"]
            self.assertTrue(verify_bundle(bundle)["certificate_verified"], raw)
            forged = service.forge(bundle)
            self.assertFalse(forged["certificate_verified"], raw)
            self.assertEqual(forged["replay"]["code"], "WITNESS_REJECTED", raw)

    def test_tampered_input_claim_and_format_fail_closed(self):
        bundle = run({"equations": ["a + c = 20", "12a + 7c = 200"]})["bundle"]
        changed = copy.deepcopy(bundle)
        changed["input"]["equations"][1] = "12a + 7c = 201"
        self.assertEqual(verify_bundle(changed)["code"], "INPUT_DIGEST_MISMATCH")
        changed = copy.deepcopy(bundle)
        changed["certificate"]["claim"] = "solve.no_solution"
        self.assertEqual(verify_bundle(rehash(changed))["code"], "INVALID_SCHEMA")
        changed = copy.deepcopy(bundle)
        changed["certificate"]["claim"] = "work.all_steps_valid"
        self.assertEqual(verify_bundle(rehash(changed))["code"], "CLAIM_MISMATCH")
        changed = copy.deepcopy(bundle)
        changed["certificate"]["result"]["solution"][0]["num"] += 1
        self.assertEqual(verify_bundle(changed)["code"], "CERTIFICATE_DIGEST_MISMATCH")
        changed = copy.deepcopy(bundle)
        changed["input"]["equations"][0] = " a + c = 20"
        self.assertEqual(verify_bundle(changed)["code"], "INPUT_NOT_CANONICAL")
        self.assertFalse(verify_bundle({"format": "other"})["certificate_verified"])

    def test_wrong_answer_cannot_be_relabelled_correct(self):
        bundle = run({"equations": ["3x + 5 = 20"], "answer": {"x": "4"}})["bundle"]
        bundle["certificate"]["claim"] = "answer.correct"
        report = verify_bundle(rehash(bundle))
        self.assertFalse(report["certificate_verified"])
        self.assertEqual(report["message"], "Check failed: verdict")

    def test_verifier_is_independent_of_the_producer(self):
        source = (ROOT / "algebraic_compiler" / "linear_verifier.py").read_text()
        imported = {node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)}
        self.assertNotIn("linear", imported)
        self.assertFalse(any(name and name.endswith(".linear") for name in imported))
        bundle = run({"steps": [["x + y = 10", "x - y = 2"], ["2x = 12"]]})["bundle"]
        with patch.object(linear, "_rref", side_effect=RuntimeError("must not call")), \
             patch.object(linear, "_analyze", side_effect=RuntimeError("must not call")), \
             patch.object(linear, "read_line", side_effect=RuntimeError("must not call")):
            self.assertTrue(verify_bundle(bundle)["certificate_verified"])

    def test_verification_failure_is_infrastructure_error_without_claim(self):
        with patch("algebraic_compiler.linear_verifier.verify_bundle",
                   return_value={"certificate_verified": False, "code": "X"}):
            out = run({"equations": ["x = 1"]})
        self.assertEqual((out["status"], out["certificate_verified"], out["bundle"]), ("INFRASTRUCTURE_ERROR", False, None))


class PracticeAndViewTests(unittest.TestCase):
    def test_practice_keys_are_certified_and_reproducible(self):
        for kind in linear.PRACTICE_KINDS:
            for seed in range(12):
                generated = practice(kind, seed)
                key = generated["answer_key"]
                self.assertEqual((key["status"], key["claim"]), ("VERIFIED", "solve.unique"))
                self.assertTrue(verify_bundle(key["bundle"])["certificate_verified"])
                self.assertNotIn(0, [q(v) for v in key["bundle"]["certificate"]["result"]["solution"]][:1])
            self.assertEqual(practice(kind, 7)["equations"], practice(kind, 7)["equations"])
        self.assertEqual(practice("tickets", 3)["answer_key"]["bundle"]["input"]["domain"], "nonnegative_integer")

    def test_practice_payload_hides_the_proof(self):
        payload = service.practice("two_step", 5)
        self.assertEqual(payload["view"]["steps"], [])
        self.assertTrue(payload["view"]["reveal"].startswith("Answer key: x = "))

    def test_explanations_come_only_from_verified_runs(self):
        view = explain(run({"equations": ["a + c = 20", "12a + 7c = 300"], "domain": "nonnegative_integer",
                            "labels": {"a": "adult tickets", "c": "child tickets"}}))
        self.assertEqual(view["verdict"], "no_valid_answer")
        self.assertIn("−12 child tickets", view["spoken"])
        self.assertIn("(2) − 7·(1) gives 5a = 160, so a = 32", view["steps"])
        view = explain(run({"steps": [["3x + 5 = 20"], ["3x = 25"]]}))
        self.assertIn("should become −5", view["hint"])
        self.assertEqual(view["reveal"], "Verified answer: x = 5")
        view = explain(run({"equations": ["x*y = 1"]}))
        self.assertEqual(view["verdict"], "no_claim")
        self.assertEqual(view["steps"], [])


if __name__ == "__main__":
    unittest.main()
