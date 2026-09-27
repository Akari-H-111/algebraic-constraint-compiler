"""Deterministic kernel tests; no LLM, AWS, or external package required."""

import copy
from fractions import Fraction
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from algebraic_compiler.compiler import compile_reconstruction
from algebraic_compiler.verifier import verify_certificate, verify_run
from algebraic_compiler.ir import InputError, canonical, digest, load_json, parse_problem, rational, scalar


def problem(m=2, d=1, b_entries=None, passes=None):
    """New exact truncated-polynomial fixture; not recovered paper data."""
    field = {"kind": "Q", "characteristic": 0}
    return {
        "schema_version": "1", "problem_id": "test", "mode": "fixed_A_V_B",
        "requested_passes": passes or ["structure"],
        "algebra": {"schema_version": "1", "id": "A", "field": field,
                    "basis": ["e" + str(i) for i in range(m)],
                    "unit": {"coordinates": [scalar(int(i == 0)) for i in range(m)]},
                    "multiplication": [{"i": i, "j": j, "k": i + j, "value": scalar(1)}
                                       for i in range(m) for j in range(m) if i + j < m]},
        "space": {"schema_version": "1", "id": "V", "field": field,
                  "basis": ["v" + str(i) for i in range(d)]},
        "B": {"schema_version": "1", "id": "B", "left_algebra_id": "A", "right_algebra_id": "A",
              "codomain_space_id": "V", "entries": b_entries or []},
    }


class ExactIRTests(unittest.TestCase):
    def test_scalar_policy(self):
        self.assertEqual(rational(scalar(Fraction(-2, 3))), Fraction(-2, 3))
        for invalid in (0.5, {"num": True, "den": 1}, {"num": 2, "den": 4},
                        {"num": 0, "den": 2}, {"num": 1, "den": -2}, {"num": 1, "den": 0}):
            with self.assertRaises(InputError):
                rational(invalid)
        for text in ('{"a":1,"a":2}', '{"a":0.5}', '{"a":NaN}'):
            with self.assertRaises(InputError):
                load_json(text)

    def test_deterministic_sparse_canonicalization(self):
        first = problem()
        second = copy.deepcopy(first)
        second["algebra"]["multiplication"].reverse()
        second["algebra"]["multiplication"] += [
            {"i": 1, "j": 1, "k": 0, "value": scalar(2)},
            {"i": 1, "j": 1, "k": 0, "value": scalar(-2)}]
        p, q = parse_problem(first), parse_problem(second)
        self.assertEqual(p.spec_json, q.spec_json)
        self.assertEqual(p.sha256, digest(p.to_json()))
        self.assertEqual(canonical(load_json(p.spec_json)), p.spec_json)

    def test_schema_scope_and_dimensions(self):
        mutations = [("mode", "deform_A"), ("backend_id", "fixed_cubic_v1"),
                     ("requested_passes", ["structure", "weak"]), ("schema_version", "2"),
                     ("requested_passes", ["strict"]), ("extra", 1)]
        for key, value in mutations:
            with self.subTest(key=key, value=value), self.assertRaises(InputError):
                parse_problem({**problem(), key: value})
        raw = problem()
        raw["B"]["codomain_space_id"] = "other"
        with self.assertRaises(InputError):
            parse_problem(raw)
        with self.assertRaises(InputError):
            parse_problem(problem(m=5))
        raw = problem()
        raw["algebra"]["multiplication"][0]["i"] = True
        with self.assertRaises(InputError):
            parse_problem(raw)


class StructureTests(unittest.TestCase):
    def test_structure_certificate(self):
        raw = problem(m=3, d=2)
        out = compile_reconstruction(raw)
        self.assertEqual(out["status"], "VERIFIED")
        self.assertTrue(verify_certificate(out["certificates"][0], raw)["certificate_verified"])
        self.assertEqual(out["result"]["dimensions"], {"algebra": 3, "space": 2})

    def test_explicit_unit_coordinates(self):
        raw = problem(m=1)
        raw["algebra"]["multiplication"][0]["value"] = scalar(2)
        raw["algebra"]["unit"]["coordinates"] = [scalar(Fraction(1, 2))]
        self.assertEqual(compile_reconstruction(raw)["status"], "VERIFIED")

    def test_unit_and_associativity_rejections_are_certified(self):
        bad_unit = problem()
        bad_unit["algebra"]["unit"]["coordinates"][0] = scalar(0)
        bad_assoc = problem(m=3)
        bad_assoc["algebra"]["multiplication"].append({"i": 2, "j": 1, "k": 0, "value": scalar(1)})
        for raw, code in ((bad_unit, "UNIT_INVALID"), (bad_assoc, "NON_ASSOCIATIVE")):
            out = compile_reconstruction(raw)
            self.assertEqual(out["status"], "MATHEMATICALLY_REJECTED")
            self.assertEqual(out["code"], code)
            self.assertTrue(out["certificate_verified"])
            self.assertEqual(out["infrastructure_status"], "success")

    def test_corruption_with_and_without_rehash(self):
        raw = problem()
        cert = compile_reconstruction(raw)["certificates"][0]
        altered = copy.deepcopy(cert)
        altered["result"]["unit_residuals"][0] = scalar(1)
        self.assertEqual(verify_certificate(altered, raw)["code"], "CERTIFICATE_DIGEST_MISMATCH")
        altered["certificate_sha256"] = digest({k: v for k, v in altered.items() if k != "certificate_sha256"})
        self.assertEqual(verify_certificate(altered, raw)["code"], "CERTIFICATE_WITNESS_INVALID")
        self.assertEqual(verify_certificate(cert, {**raw, "problem_id": "different"})["code"], "CERTIFICATE_INPUT_MISMATCH")
        for key, value in (("backend_id", "fixed_cubic_v1"), ("scope", "fixed_backend"), ("schema_version", "2")):
            self.assertFalse(verify_certificate({**cert, key: value}, raw)["certificate_verified"])

    def test_verifier_failure_never_yields_verified_result(self):
        with patch("algebraic_compiler.verifier.verify_certificate", return_value={"certificate_verified": False}):
            out = compile_reconstruction(problem())
        self.assertEqual(out["status"], "INFRASTRUCTURE_ERROR")
        self.assertFalse(out["certificate_verified"])
        self.assertIsNone(out["result"])


class ObservabilityTests(unittest.TestCase):
    def test_one_dimensional_hand_computed_matrix(self):
        raw = problem(m=1, b_entries=[{"i": 0, "j": 0, "beta": 0, "value": scalar(2)}],
                      passes=["structure", "observability"])
        out = compile_reconstruction(raw)
        self.assertEqual(out["status"], "VERIFIED")
        self.assertEqual(out["result"]["matrix"], {"rows": 2, "cols": 2, "entries": [
            {"row": i, "col": j, "value": scalar(2)} for i in range(2) for j in range(2)]})
        self.assertEqual(out["result"]["rhs"], [scalar(2), scalar(2)])

    def test_nonsymmetric_tensor_and_matrix_output_order(self):
        raw = problem(m=3, d=2, passes=["structure", "observability"], b_entries=[
            {"i": i, "j": j, "beta": beta, "value": scalar(Fraction(1 + i + 3*j + beta, 7))}
            for i in range(3) for j in range(3) for beta in range(2)])
        out = compile_reconstruction(raw)
        self.assertEqual(out["status"], "VERIFIED")
        ir = out["result"]
        self.assertEqual((ir["matrix"]["rows"], ir["matrix"]["cols"]), (108, 24))
        self.assertEqual(ir["variable_layout"][12], {"side": "R", "algebra_basis_index": 0, "row": 0, "col": 0})
        self.assertEqual(ir["equation_layout"][54], {"family": "weak_right", "indices": [0, 0, 0, 0]})
        cert = copy.deepcopy(out["certificates"][-1])
        cert["result"]["matrix"]["entries"][0]["value"] = scalar(999)
        cert["certificate_sha256"] = digest({k: v for k, v in cert.items() if k != "certificate_sha256"})
        self.assertFalse(verify_certificate(cert, raw)["certificate_verified"])

    def test_zero_b_keeps_all_nominal_rows(self):
        out = compile_reconstruction(problem(passes=["structure", "observability"]))
        self.assertEqual(out["result"]["matrix"], {"rows": 16, "cols": 4, "entries": []})
        self.assertEqual(out["result"]["rhs"], [scalar(0)] * 16)


class WeakTests(unittest.TestCase):
    def test_noncommutative_algebra(self):
        # Basis I,E12,E22 of the upper triangular 2x2 algebra, with E12 E22=E12
        # but E22 E12=0. This catches accidental commutative assumptions.
        raw = problem(m=3, d=2, passes=["structure", "observability", "weak"])
        raw["algebra"]["multiplication"] = [{"i": i, "j": j, "k": k, "value": scalar(1)}
            for i, j, k in ((0,0,0), (0,1,1), (0,2,2), (1,0,1), (2,0,2), (1,2,1), (2,2,2))]
        raw["B"]["entries"] = [{"i": i, "j": j, "beta": b, "value": scalar((i+1)*(b+1)-j)}
                                for i in range(3) for j in range(3) for b in range(2)]
        run = compile_reconstruction(raw)
        self.assertEqual(run["mathematical_status"], "weak.inconsistent")
        self.assertTrue(verify_run(run)["certificate_verified"])

    def test_timeout_and_verifier_resource_failure_are_incomplete(self):
        raw = problem(m=1, passes=["structure", "observability", "weak"],
                      b_entries=[{"i": 0, "j": 0, "beta": 0, "value": scalar(1)}])
        with patch("algebraic_compiler.compiler.time.monotonic", side_effect=[0, 11]):
            limited = compile_reconstruction(raw)
        self.assertEqual(limited["status"], "INCOMPLETE_RESOURCE_LIMIT")
        self.assertEqual(limited["verified_through"], ["structure", "observability"])
        self.assertFalse(limited["certificate_verified"])
        with patch("algebraic_compiler.verifier._rank", side_effect=InputError("RESOURCE_LIMIT", "test limit")):
            limited = compile_reconstruction(raw)
        self.assertEqual(limited["status"], "INCOMPLETE_RESOURCE_LIMIT")
        self.assertIsNone(limited["result"])

    def test_infrastructure_failure_is_not_a_mathematical_result(self):
        raw = problem(passes=["structure", "observability", "weak"])
        with patch("algebraic_compiler.compiler.weak", side_effect=RuntimeError("test failure")), \
             self.assertLogs("algebraic_compiler.compiler", level="ERROR"):
            failed = compile_reconstruction(raw)
        self.assertEqual(failed["status"], "INFRASTRUCTURE_ERROR")
        self.assertFalse(failed["certificate_verified"])
        self.assertIsNone(failed["result"])

    def test_hand_computed_affine_fiber(self):
        raw = problem(m=1, b_entries=[{"i": 0, "j": 0, "beta": 0, "value": scalar(2)}],
                      passes=["structure", "observability", "weak"])
        out = compile_reconstruction(raw)
        self.assertEqual(out["status"], "VERIFIED")
        self.assertEqual(out["verified_through"], ["structure", "observability", "weak"])
        self.assertEqual(out["result"]["rank"], 1)
        self.assertEqual(out["result"]["particular_solution"], [scalar(1), scalar(0)])
        self.assertEqual(out["result"]["kernel_basis"], [[scalar(-1), scalar(1)]])
        self.assertEqual(out, compile_reconstruction(raw))

    def test_zero_b_full_kernel_and_duplicate_basis_rejected(self):
        raw = problem(passes=["structure", "observability", "weak"])
        out = compile_reconstruction(raw)
        self.assertEqual(out["result"]["fiber_dimension"], 4)
        cert = copy.deepcopy(out["certificates"][-1])
        cert["result"]["kernel_basis"][1] = cert["result"]["kernel_basis"][0]
        cert["certificate_sha256"] = digest({k: v for k, v in cert.items() if k != "certificate_sha256"})
        self.assertEqual(verify_certificate(cert, raw)["code"], "CERTIFICATE_WITNESS_INVALID")

    def test_negative_dual_witness_from_algebraic_input(self):
        # A=Q[e]/e², B(e,1)=1, all other B entries zero.
        # Three rows assert L_1=1, R_1=1, L_1+R_1=1: contradiction.
        raw = problem(b_entries=[{"i": 1, "j": 0, "beta": 0, "value": scalar(1)}],
                      passes=["structure", "observability", "weak"])
        out = compile_reconstruction(raw)
        self.assertEqual(out["status"], "MATHEMATICALLY_REJECTED")
        self.assertEqual(out["mathematical_status"], "weak.inconsistent")
        self.assertEqual(out["infrastructure_status"], "success")
        self.assertTrue(out["certificate_verified"])
        witness = out["result"]["inconsistency_witness"]
        self.assertEqual(witness["rhs_pairing"], scalar(-1))
        self.assertEqual([i for i, q in enumerate(witness["left_null_vector"]) if q["num"]], [2, 4, 12])
        cert = copy.deepcopy(out["certificates"][-1])
        cert["result"]["inconsistency_witness"]["left_null_vector"] = [scalar(0)] * 16
        cert["certificate_sha256"] = digest({k: v for k, v in cert.items() if k != "certificate_sha256"})
        self.assertFalse(verify_certificate(cert, raw)["certificate_verified"])

    def test_rehashed_solution_and_rank_forgery_rejected(self):
        raw = problem(m=1, passes=["structure", "observability", "weak"],
                      b_entries=[{"i": 0, "j": 0, "beta": 0, "value": scalar(1)}])
        original = compile_reconstruction(raw)["certificates"][-1]
        for key, value in (("particular_solution", [scalar(0), scalar(0)]), ("rank", 0),
                           ("kernel_basis", []), ("observability_sha256", "0" * 64)):
            cert = copy.deepcopy(original)
            cert["result"][key] = value
            cert["certificate_sha256"] = digest({k: v for k, v in cert.items() if k != "certificate_sha256"})
            self.assertFalse(verify_certificate(cert, raw)["certificate_verified"])

    def test_input_failures_are_not_mathematical_rejections(self):
        raw = problem()
        raw["algebra"]["unit"]["coordinates"][0] = 1.0
        self.assertEqual(compile_reconstruction(raw)["status"], "ASSUMPTION_REQUIRED")
        self.assertEqual(compile_reconstruction(problem(m=5))["status"], "INCOMPLETE_RESOURCE_LIMIT")
        self.assertEqual(compile_reconstruction({**problem(), "mode": "deform_B"})["status"], "UNSUPPORTED")

    def test_verifier_does_not_call_compiler(self):
        raw = problem(passes=["structure", "observability", "weak"])
        cert = compile_reconstruction(raw)["certificates"][-1]
        with patch("algebraic_compiler.compiler.weak", side_effect=RuntimeError("must not call")), \
             patch("algebraic_compiler.compiler.observability", side_effect=RuntimeError("must not call")), \
             patch("algebraic_compiler.compiler.structure", side_effect=RuntimeError("must not call")):
            self.assertTrue(verify_certificate(cert, raw)["certificate_verified"])


class StrictPointTests(unittest.TestCase):
    passes = ["structure", "observability", "weak", "strict"]

    @staticmethod
    def with_point(raw, coordinates):
        raw["strict_point"] = {"schema_version": "1", "coordinate_system": "action_entries",
                               "coordinates": [scalar(q) for q in coordinates]}
        return raw

    def test_valid_exact_point_and_all_residual_families(self):
        raw = self.with_point(problem(m=1, passes=self.passes), [1, 1])
        run = compile_reconstruction(raw)
        self.assertEqual(run["status"], "VERIFIED")
        self.assertEqual(run["mathematical_status"], "strict.point_valid")
        self.assertEqual(run["verified_through"], self.passes)
        self.assertEqual(set(run["result"]["residuals"]),
                         {"weak", "unit_left", "unit_right", "left", "right", "mixed"})
        self.assertTrue(all(q["num"] == 0 for values in run["result"]["residuals"].values() for q in values))
        self.assertEqual(run, compile_reconstruction(raw))
        self.assertTrue(verify_run(run)["certificate_verified"])

    def test_weak_valid_but_strict_invalid_is_certified_rejection(self):
        raw = problem(m=1, passes=self.passes,
                      b_entries=[{"i": 0, "j": 0, "beta": 0, "value": scalar(2)}])
        self.with_point(raw, [1, 0])
        run = compile_reconstruction(raw)
        self.assertEqual(run["status"], "MATHEMATICALLY_REJECTED")
        self.assertEqual(run["code"], "STRICT_POINT_INVALID")
        self.assertTrue(all(q["num"] == 0 for q in run["result"]["residuals"]["weak"]))
        self.assertNotEqual(run["result"]["residuals"]["unit_right"], [scalar(0)])
        self.assertTrue(verify_run(run)["certificate_verified"])

    def test_left_right_and_mixed_failures_are_visible(self):
        identity = [1, 0, 0, 1]
        zero = [0, 0, 0, 0]
        e12 = [0, 1, 0, 0]
        e21 = [0, 0, 1, 0]
        cases = {
            "left": identity + identity + identity + zero,
            "right": identity + zero + identity + identity,
            "mixed": identity + e12 + identity + e21,
        }
        for family, coordinates in cases.items():
            with self.subTest(family=family):
                raw = self.with_point(problem(m=2, d=2, passes=self.passes), coordinates)
                result = compile_reconstruction(raw)["result"]
                self.assertTrue(any(q["num"] for q in result["residuals"][family]))

    def test_noncommutative_regular_bimodule_validates_right_order(self):
        raw = problem(m=3, d=3, passes=self.passes)
        products = ((0, 0, 0), (0, 1, 1), (0, 2, 2), (1, 0, 1),
                    (2, 0, 2), (1, 2, 1), (2, 2, 2))
        raw["algebra"]["multiplication"] = [
            {"i": i, "j": j, "k": k, "value": scalar(1)} for i, j, k in products]
        lookup = {(i, j, k): 1 for i, j, k in products}
        coordinates = []
        for side in range(2):
            for i in range(3):
                for row in range(3):
                    for col in range(3):
                        coordinates.append(lookup.get((i, col, row) if side == 0 else (col, i, row), 0))
        self.with_point(raw, coordinates)
        run = compile_reconstruction(raw)
        self.assertEqual(run["mathematical_status"], "strict.point_valid")
        self.assertTrue(verify_run(run)["certificate_verified"])

    def test_strict_certificate_corruption_and_compiler_reuse_are_rejected(self):
        raw = self.with_point(problem(m=1, passes=self.passes), [1, 1])
        cert = compile_reconstruction(raw)["certificates"][-1]
        altered = copy.deepcopy(cert)
        altered["result"]["residuals"]["mixed"][0] = scalar(1)
        altered["certificate_sha256"] = digest({k: v for k, v in altered.items() if k != "certificate_sha256"})
        self.assertEqual(verify_certificate(altered, raw)["code"], "CERTIFICATE_WITNESS_INVALID")
        with patch("algebraic_compiler.compiler.strict", side_effect=RuntimeError("must not call")):
            self.assertTrue(verify_certificate(cert, raw)["certificate_verified"])

    def test_strict_point_schema_and_exact_scalar_policy(self):
        missing = problem(m=1, passes=self.passes)
        self.assertEqual(compile_reconstruction(missing)["code"], "INVALID_SCHEMA")
        extra = self.with_point(problem(m=1), [1, 1])
        self.assertEqual(compile_reconstruction(extra)["code"], "INVALID_SCHEMA")
        floating = self.with_point(problem(m=1, passes=self.passes), [1, 1])
        floating["strict_point"]["coordinates"][0] = 1.0
        self.assertEqual(compile_reconstruction(floating)["code"], "NONCANONICAL_SCALAR")


class ReplayAndCLITests(unittest.TestCase):
    def test_aggregate_run_limit_preserves_only_prior_verified_passes(self):
        raw = problem(passes=["structure", "observability", "weak"])
        complete = compile_reconstruction(raw)
        budget = max(len(canonical(c)) for c in complete["certificates"]) + 100
        self.assertLess(budget, len(canonical(complete)))
        with patch("algebraic_compiler.ir.MAX_BYTES", budget):
            limited = compile_reconstruction(raw)
            self.assertEqual(limited["status"], "INCOMPLETE_RESOURCE_LIMIT")
            self.assertFalse(limited["certificate_verified"])
            self.assertLessEqual(len(canonical(limited)), budget)
        for cert in limited["certificates"]:
            self.assertTrue(verify_certificate(cert, raw)["certificate_verified"])

    def test_run_summary_and_missing_passes_fail_closed(self):
        raw = problem(passes=["structure", "observability", "weak"])
        original = compile_reconstruction(raw)
        self.assertTrue(verify_run(original)["certificate_verified"])
        for key, value in (("status", "MATHEMATICALLY_REJECTED"), ("result", {}),
                           ("certificates", original["certificates"][:-1]), ("certificates", [])):
            run = copy.deepcopy(original)
            run[key] = value
            self.assertFalse(verify_run(run)["certificate_verified"])

    def test_cli_roundtrip_and_invalid_json_exit_codes(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("weak-consistent", "weak-inconsistent", "strict-point-valid", "second-order-obstructed", "second-order-extends"):
            compiled = subprocess.run([sys.executable, "-B", "-m", "algebraic_compiler", "compile",
                                       str(root / "examples" / "algebraic" / (name + ".json"))],
                                      capture_output=True, check=False, cwd=root)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            replay = subprocess.run([sys.executable, "-B", "-m", "algebraic_compiler", "verify", "-"],
                                    input=compiled.stdout, capture_output=True, check=False, cwd=root)
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertTrue(load_json(replay.stdout)["certificate_verified"])
        invalid = subprocess.run([sys.executable, "-B", "-m", "algebraic_compiler", "compile", "-"],
                                 input=b'{"scalar":0.5}', capture_output=True, cwd=root)
        self.assertEqual(invalid.returncode, 1)
        self.assertEqual(load_json(invalid.stdout)["code"], "NONCANONICAL_SCALAR")


if __name__ == "__main__":
    unittest.main()
