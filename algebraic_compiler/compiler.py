"""Exact pass producers and the public compiler facade (never LLM authority)."""

import logging
from fractions import Fraction
import time

from . import __version__
from .ir import BACKEND, InputError, canonical, digest, matrix_json, parse_problem, rational, require, scalar, vector

LOG = logging.getLogger(__name__)
SPEC_SHA256 = "29d516d0bfc12dc17ee8f29ac842d939910db73dcfbd4c303a735d21719da4e6"


def structure(p):
    """Pass 1: exact unit and associativity residuals, including negative evidence."""
    c, m = p.multiplication, p.m
    units = []
    for side in ("left", "right"):
        for i in range(m):
            for k in range(m):
                units.append(sum(p.unit[j] * (c[j][i][k] if side == "left" else c[i][j][k])
                                 for j in range(m)) - int(i == k))
    assoc = [sum(c[i][j][s] * c[s][k][r] - c[j][k][s] * c[i][s][r] for s in range(m))
             for i in range(m) for j in range(m) for k in range(m) for r in range(m)]
    code = "UNIT_INVALID" if any(units) else "NON_ASSOCIATIVE" if any(assoc) else None
    return {"dimensions": {"algebra": m, "space": p.d}, "code": code,
            "unit_residuals": vector(units), "associativity_residuals": vector(assoc)}


def observability(p):
    """Pass 2: compile both weak identities with family/i/j/k/output row order."""
    m, d, b, c = p.m, p.d, p.bilinear, p.multiplication
    variables = [{"side": side, "algebra_basis_index": i, "row": a, "col": beta}
                 for side in ("L", "R") for i in range(m) for a in range(d) for beta in range(d)]
    width = len(variables)
    rows, rhs, equations = [], [], []

    def index(side, i, a, beta):
        return (side * m + i) * d * d + a * d + beta

    for family in ("weak_left", "weak_right"):
        for i in range(m):
            for j in range(m):
                for k in range(m):
                    for a in range(d):
                        row = [Fraction(0) for _ in range(width)]
                        for beta in range(d):
                            if family == "weak_left":
                                row[index(0, i, a, beta)] += b[j][k][beta]
                                row[index(1, j, a, beta)] += b[i][k][beta]
                            else:
                                row[index(1, k, a, beta)] += b[i][j][beta]
                                row[index(0, j, a, beta)] += b[i][k][beta]
                        value = (sum(c[i][j][s] * b[s][k][a] for s in range(m)) if family == "weak_left"
                                 else sum(c[j][k][s] * b[i][s][a] for s in range(m)))
                        rows.append(row)
                        rhs.append(value)
                        equations.append({"family": family, "indices": [i, j, k, a]})
    return {"schema_version": "1", "problem_digest": p.sha256,
            "variable_layout": variables, "equation_layout": equations,
            "matrix": matrix_json(rows, width), "rhs": vector(rhs)}


def weak(ir):
    """Pass 3: deterministic exact RREF; retain row operations for a dual witness."""
    matrix = ir["matrix"]
    height, width = matrix["rows"], matrix["cols"]
    rows = [[Fraction(0) for _ in range(width)] + [rational(q, 8192)] for q in ir["rhs"]]
    for entry in matrix["entries"]:
        rows[entry["row"]][entry["col"]] = rational(entry["value"], 8192)
    combinations = [{i: Fraction(1)} for i in range(height)]
    pivots = []
    started = time.monotonic()
    for col in range(width):
        pivot_row = next((i for i in range(len(pivots), height) if rows[i][col]), None)
        if pivot_row is None:
            continue
        pivot = len(pivots)
        rows[pivot], rows[pivot_row] = rows[pivot_row], rows[pivot]
        combinations[pivot], combinations[pivot_row] = combinations[pivot_row], combinations[pivot]
        scale = rows[pivot][col]
        rows[pivot] = [x / scale for x in rows[pivot]]
        combinations[pivot] = {i: x / scale for i, x in combinations[pivot].items()}
        for i in range(height):
            if i == pivot or not rows[i][col]:
                continue
            scale = rows[i][col]
            rows[i] = [x - scale * y for x, y in zip(rows[i], rows[pivot])]
            for j, coefficient in combinations[pivot].items():
                updated = combinations[i].get(j, Fraction(0)) - scale * coefficient
                if updated:
                    combinations[i][j] = updated
                else:
                    combinations[i].pop(j, None)
            require(time.monotonic() - started <= 10, "RESOURCE_LIMIT", "Exact elimination time budget exceeded")
            for q in rows[i] + list(combinations[i].values()):
                require(max(q.numerator.bit_length(), q.denominator.bit_length()) <= 8192,
                        "RESOURCE_LIMIT", "Exact elimination coefficient budget exceeded")
        pivots.append(col)
    result = {"schema_version": "1", "problem_digest": ir["problem_digest"],
              "observability_sha256": digest(ir), "rank": len(pivots), "ambient_dimension": width,
              "fiber_dimension": None, "particular_solution": None, "kernel_basis": [],
              "inconsistency_witness": None, "status": "inconsistent", "code": "WEAK_INCONSISTENT"}
    bad_row = next((i for i, row in enumerate(rows) if not any(row[:-1]) and row[-1]), None)
    if bad_row is not None:
        result["inconsistency_witness"] = {
            "left_null_vector": vector([combinations[bad_row].get(i, Fraction(0)) for i in range(height)]),
            "rhs_pairing": scalar(rows[bad_row][-1])}
        return result
    u0 = [Fraction(0) for _ in range(width)]
    for i, col in enumerate(pivots):
        u0[col] = rows[i][-1]
    kernel = []
    for free in range(width):
        if free in pivots:
            continue
        k = [Fraction(0) for _ in range(width)]
        k[free] = Fraction(1)
        for i, col in enumerate(pivots):
            k[col] = -rows[i][free]
        kernel.append(vector(k))
    result.update(status="consistent", code=None, fiber_dimension=width - len(pivots),
                  particular_solution=vector(u0), kernel_basis=kernel)
    return result


def _actions(p, coordinates):
    actions = [[[[Fraction(0) for _ in range(p.d)] for _ in range(p.d)] for _ in range(p.m)]
               for _ in range(2)]
    for side in range(2):
        for i in range(p.m):
            for row in range(p.d):
                for col in range(p.d):
                    actions[side][i][row][col] = coordinates[p.variable_index(side, i, row, col)]
    return actions


def _multiply(left, right):
    size = len(left)
    return [[sum((left[row][k] * right[k][col] for k in range(size)), Fraction(0))
             for col in range(size)] for row in range(size)]


def strict(p, observable):
    """Pass 4: replay all strict action identities at one exact candidate point."""
    coordinates = p.strict_point
    require(coordinates is not None, "INVALID_SCHEMA", "strict_point is required")
    left, right = _actions(p, coordinates)

    weak_residuals = [-rational(q, 8192) for q in observable["rhs"]]
    for entry in observable["matrix"]["entries"]:
        weak_residuals[entry["row"]] += rational(entry["value"], 8192) * coordinates[entry["col"]]

    unit_left, unit_right = [], []
    for row in range(p.d):
        for col in range(p.d):
            identity = Fraction(int(row == col))
            unit_left.append(sum((p.unit[i] * left[i][row][col] for i in range(p.m)), Fraction(0)) - identity)
            unit_right.append(sum((p.unit[i] * right[i][row][col] for i in range(p.m)), Fraction(0)) - identity)

    left_residuals, right_residuals, mixed_residuals = [], [], []
    for i in range(p.m):
        for j in range(p.m):
            ll = _multiply(left[i], left[j])
            rr = _multiply(right[j], right[i])
            lr = _multiply(left[i], right[j])
            rl = _multiply(right[j], left[i])
            for row in range(p.d):
                for col in range(p.d):
                    product_left = sum((p.multiplication[i][j][ell] * left[ell][row][col]
                                        for ell in range(p.m)), Fraction(0))
                    product_right = sum((p.multiplication[i][j][ell] * right[ell][row][col]
                                         for ell in range(p.m)), Fraction(0))
                    left_residuals.append(ll[row][col] - product_left)
                    right_residuals.append(rr[row][col] - product_right)
                    mixed_residuals.append(lr[row][col] - rl[row][col])

    residuals = {"weak": weak_residuals, "unit_left": unit_left, "unit_right": unit_right,
                 "left": left_residuals, "right": right_residuals, "mixed": mixed_residuals}
    valid = all(value == 0 for values in residuals.values() for value in values)
    return {"schema_version": "1", "problem_digest": p.sha256,
            "coordinate_system": "action_entries", "coordinates": vector(coordinates),
            "residuals": {name: vector(values) for name, values in residuals.items()},
            "status": "valid" if valid else "invalid", "code": None if valid else "STRICT_POINT_INVALID"}


def certificate(p, pass_name, claim, result):
    """Build a content-addressed envelope; no self-referential digest."""
    cert = {
        "schema_version": "1", "compiler_version": __version__,
        "input_sha256": p.sha256, "scope": "general_fd", "backend_id": BACKEND,
        "pass": pass_name, "claim": claim, "result": result,
        "provenance": {"kind": "handoff_spec", "file": "MATH_KERNEL_SPEC.md", "sha256": SPEC_SHA256},
    }
    return {**cert, "certificate_sha256": digest(cert)}


def compile_reconstruction(problem_spec):
    """Compile supported passes, verifying each before returning trusted results.

    Mathematical rejection is a successful, certified run. Invalid input,
    unsupported scope, resource exhaustion and infrastructure errors are distinct.
    """
    from .verifier import verify_certificate

    output = {"schema_version": "1", "status": "ASSUMPTION_REQUIRED",
              "infrastructure_status": "success", "mathematical_status": "NOT_EVALUATED",
              "certificate_verified": False, "verified_through": [], "certificates": [],
              "problem": None, "result": None, "code": None}
    try:
        p = parse_problem(problem_spec)
        output["problem"] = p.to_json()
        observable = tangent_result = None
        for pass_name in p.to_json()["requested_passes"]:
            if pass_name == "structure":
                result = structure(p)
                claim = "structure.rejected" if result["code"] else "structure.valid"
            elif pass_name == "observability":
                result, claim = observability(p), "observability.compiled"
                observable = result
            elif pass_name == "weak":
                result = weak(observable)
                claim = "weak." + result["status"]
            elif pass_name == "strict":
                result = strict(p, observable)
                claim = "strict.point_valid" if result["status"] == "valid" else "strict.point_invalid"
            elif pass_name == "tangent":
                from .deformation import tangent
                tangent_result = result = tangent(p, observable)
                claim = "tangent.analyzed"
            elif pass_name == "obstruction":
                from .deformation import obstruction
                result = obstruction(p, observable, tangent_result)
                claim = "obstruction." + result["status"]
            else:
                output.update(status="UNSUPPORTED", code="UNSUPPORTED_SCOPE", mathematical_status="NOT_EVALUATED")
                return output
            cert = certificate(p, pass_name, claim, result)
            report = verify_certificate(cert, p.to_json())
            if not report["certificate_verified"]:
                limited = report.get("status") == "INCOMPLETE_RESOURCE_LIMIT"
                output.update(status="INCOMPLETE_RESOURCE_LIMIT" if limited else "INFRASTRUCTURE_ERROR",
                              infrastructure_status="success" if limited else "error", result=None,
                              mathematical_status="NOT_EVALUATED",
                              certificate_verified=False, code="RESOURCE_LIMIT" if limited else "CERTIFICATE_VERIFICATION_FAILED",
                              verification=report)
                return output
            candidate = {**output, "certificates": output["certificates"] + [cert],
                         "verified_through": output["verified_through"] + [pass_name],
                         "status": "MATHEMATICALLY_REJECTED" if result.get("code") else "VERIFIED",
                         "mathematical_status": claim, "result": result, "code": result.get("code"),
                         "certificate_verified": True}
            canonical(candidate)  # Bound the complete exported run, not just each certificate.
            output = candidate
            if result.get("code"):
                return output
        return output
    except InputError as exc:
        status = "INCOMPLETE_RESOURCE_LIMIT" if exc.code == "RESOURCE_LIMIT" else (
            "UNSUPPORTED" if exc.code.startswith("UNSUPPORTED") or exc.code == "BACKEND_SCOPE_MISMATCH"
            else "ASSUMPTION_REQUIRED")
        output.update(status=status, code=exc.code, message=str(exc), result=None, certificate_verified=False,
                      mathematical_status="NOT_EVALUATED")
        return output
    except Exception:
        LOG.exception("Compiler infrastructure failure")
        output.update(status="INFRASTRUCTURE_ERROR", infrastructure_status="error",
                      certificate_verified=False, result=None, mathematical_status="NOT_EVALUATED", code="INTERNAL_ERROR")
        return output
