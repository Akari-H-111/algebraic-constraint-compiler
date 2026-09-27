"""Independent witness replay. This module must not import compiler/solver code."""

import logging
from fractions import Fraction
import time

from . import __version__
from .ir import (BACKEND, InputError, canonical, digest, fields, matrix_json, parse_problem,
                 rational, read_vector, require, vector)

LOG = logging.getLogger(__name__)


def _structure(p):
    # Check the algebra via vector multiplication, separately from the compiler's
    # contracted structure-constant residual formula.
    m = p.m
    basis = [[Fraction(int(i == j)) for j in range(m)] for i in range(m)]

    def multiply(a, b):
        value = [Fraction(0) for _ in range(m)]
        for i, x in enumerate(a):
            for j, y in enumerate(b):
                if x and y:
                    for k, coefficient in enumerate(p.multiplication[i][j]):
                        value[k] += x * y * coefficient
        return value

    units = []
    for left in (True, False):
        for e in basis:
            product = multiply(p.unit, e) if left else multiply(e, p.unit)
            units.extend(a - b for a, b in zip(product, e))
    assoc = []
    for a in basis:
        for b in basis:
            for c in basis:
                x = multiply(multiply(a, b), c)
                y = multiply(a, multiply(b, c))
                assoc.extend(i - j for i, j in zip(x, y))
    code = "UNIT_INVALID" if any(units) else "NON_ASSOCIATIVE" if any(assoc) else None
    return {"dimensions": {"algebra": m, "space": p.d}, "code": code,
            "unit_residuals": vector(units), "associativity_residuals": vector(assoc)}


def _observability(p):
    """Regenerate columns by evaluating O_B on elementary actions, not the producer."""
    m, d = p.m, p.d
    variables = []
    for side in ("L", "R"):
        for i in range(m):
            for row in range(d):
                for col in range(d):
                    variables.append({"side": side, "algebra_basis_index": i, "row": row, "col": col})
    coordinates = [(family, i, j, k, a) for family in ("weak_left", "weak_right")
                   for i in range(m) for j in range(m) for k in range(m) for a in range(d)]
    rows = [[] for _ in coordinates]
    for action in variables:
        def apply(side, index, vec):
            result = [Fraction(0) for _ in range(d)]
            if action["side"] == side and action["algebra_basis_index"] == index:
                result[action["row"]] = vec[action["col"]]
            return result

        for row_index, (family, i, j, k, a) in enumerate(coordinates):
            if family == "weak_left":
                x = apply("L", i, p.bilinear[j][k])
                y = apply("R", j, p.bilinear[i][k])
            else:
                x = apply("R", k, p.bilinear[i][j])
                y = apply("L", j, p.bilinear[i][k])
            rows[row_index].append(x[a] + y[a])
    rhs = []
    for family, i, j, k, a in coordinates:
        product = p.multiplication[i][j] if family == "weak_left" else p.multiplication[j][k]
        image = [Fraction(0) for _ in range(d)]
        for s, coefficient in enumerate(product):
            value = p.bilinear[s][k] if family == "weak_left" else p.bilinear[i][s]
            for beta in range(d):
                image[beta] += coefficient * value[beta]
        rhs.append(image[a])
    return {"schema_version": "1", "problem_digest": p.sha256,
            "variable_layout": variables,
            "equation_layout": [{"family": f, "indices": [i, j, k, a]} for f, i, j, k, a in coordinates],
            "matrix": matrix_json(rows, len(variables)), "rhs": vector(rhs)}


def _rank(rows):
    """Incremental row-basis rank; no producer RREF/solution routine is reused."""
    basis = {}
    started = time.monotonic()
    for original in rows:
        row = list(original)
        for pivot, earlier in sorted(basis.items()):
            if row[pivot]:
                factor = row[pivot] / earlier[pivot]
                row = [a - factor*b for a, b in zip(row, earlier)]
                require(time.monotonic() - started <= 10, "RESOURCE_LIMIT", "Verifier rank time budget exceeded")
                for q in row:
                    require(max(q.numerator.bit_length(), q.denominator.bit_length()) <= 8192,
                            "RESOURCE_LIMIT", "Verifier coefficient budget exceeded")
        pivot = next((i for i, q in enumerate(row) if q), None)
        if pivot is not None:
            basis[pivot] = row
    return len(basis)


def _weak(result, ir):
    fields(result, ("schema_version", "problem_digest", "observability_sha256", "rank", "ambient_dimension",
                    "fiber_dimension", "particular_solution", "kernel_basis", "inconsistency_witness", "status", "code"))
    require(result["schema_version"] == "1" and result["problem_digest"] == ir["problem_digest"]
            and result["observability_sha256"] == digest(ir), "CERTIFICATE_WITNESS_INVALID", "Wrong weak input")
    h, w = ir["matrix"]["rows"], ir["matrix"]["cols"]
    rows = [[Fraction(0) for _ in range(w)] for _ in range(h)]
    for e in ir["matrix"]["entries"]:
        rows[e["row"]][e["col"]] = rational(e["value"], 8192)
    rhs = read_vector(ir["rhs"], h)
    rank = _rank(rows)
    require(type(result["rank"]) is int and result["rank"] == rank and
            type(result["ambient_dimension"]) is int and result["ambient_dimension"] == w,
            "CERTIFICATE_WITNESS_INVALID", "Wrong rank or ambient dimension")
    require(type(result["kernel_basis"]) is list, "CERTIFICATE_SCHEMA_MISMATCH", "Kernel must be a list")
    if result["status"] == "consistent":
        require(result["code"] is None and result["inconsistency_witness"] is None and
                type(result["fiber_dimension"]) is int and result["fiber_dimension"] == w - rank,
                "CERTIFICATE_WITNESS_INVALID", "Inconsistent positive witness fields")
        u = read_vector(result["particular_solution"], w)
        require(all(sum(a*b for a, b in zip(row, u)) == v for row, v in zip(rows, rhs)),
                "CERTIFICATE_WITNESS_INVALID", "Particular solution fails M u = r")
        require(len(result["kernel_basis"]) == w - rank, "CERTIFICATE_WITNESS_INVALID", "Incomplete kernel")
        kernel = [read_vector(v, w) for v in result["kernel_basis"]]
        require(all(sum(a*b for a, b in zip(row, k)) == 0 for k in kernel for row in rows),
                "CERTIFICATE_WITNESS_INVALID", "Kernel residual is nonzero")
        require(_rank(kernel) == w - rank, "CERTIFICATE_WITNESS_INVALID", "Dependent kernel vectors")
        return "weak.consistent"
    require(result["status"] == "inconsistent" and result["code"] == "WEAK_INCONSISTENT"
            and result["fiber_dimension"] is None and result["particular_solution"] is None and not result["kernel_basis"],
            "CERTIFICATE_WITNESS_INVALID", "Inconsistent negative witness fields")
    witness = result["inconsistency_witness"]
    fields(witness, ("left_null_vector", "rhs_pairing"))
    lam = read_vector(witness["left_null_vector"], h)
    require(all(sum(lam[i] * rows[i][j] for i in range(h)) == 0 for j in range(w)),
            "CERTIFICATE_WITNESS_INVALID", "Dual witness fails lambda^T M = 0")
    pairing = sum(x*y for x, y in zip(lam, rhs))
    require(pairing != 0 and pairing == rational(witness["rhs_pairing"], 8192),
            "CERTIFICATE_WITNESS_INVALID", "Dual witness fails lambda^T r != 0")
    return "weak.inconsistent"


def _strict(p, result):
    """Recompute strict identities from the typed input without compiler code."""
    fields(result, ("schema_version", "problem_digest", "coordinate_system", "coordinates",
                    "residuals", "status", "code"))
    require(result["schema_version"] == "1" and result["problem_digest"] == p.sha256 and
            result["coordinate_system"] == "action_entries", "CERTIFICATE_WITNESS_INVALID", "Wrong strict input")
    width = 2 * p.m * p.d * p.d
    coordinates = tuple(read_vector(result["coordinates"], width))
    require(coordinates == p.strict_point, "CERTIFICATE_WITNESS_INVALID", "Strict point differs from input")

    residuals = _strict_residuals(p, coordinates)
    valid = all(q == 0 for values in residuals.values() for q in values)
    expected = {"schema_version": "1", "problem_digest": p.sha256,
                "coordinate_system": "action_entries", "coordinates": vector(coordinates),
                "residuals": {name: vector(values) for name, values in residuals.items()},
                "status": "valid" if valid else "invalid", "code": None if valid else "STRICT_POINT_INVALID"}
    require(canonical(result) == canonical(expected), "CERTIFICATE_WITNESS_INVALID", "Strict residual replay failed")
    return "strict.point_valid" if valid else "strict.point_invalid"


def _strict_residuals(p, coordinates, observable=None):
    actions = {}
    for side in range(2):
        for i in range(p.m):
            actions[side, i] = [[coordinates[p.variable_index(side, i, row, col)]
                                 for col in range(p.d)] for row in range(p.d)]

    def compose(first, second, row, col):
        return sum((first[row][middle] * second[middle][col] for middle in range(p.d)), Fraction(0))

    observable = observable or _observability(p)
    weak_residuals = [-rational(q, 8192) for q in observable["rhs"]]
    for entry in observable["matrix"]["entries"]:
        weak_residuals[entry["row"]] += rational(entry["value"], 8192) * coordinates[entry["col"]]

    unit_left, unit_right = [], []
    for row in range(p.d):
        for col in range(p.d):
            identity = Fraction(int(row == col))
            unit_left.append(sum((p.unit[i] * actions[0, i][row][col] for i in range(p.m)), Fraction(0)) - identity)
            unit_right.append(sum((p.unit[i] * actions[1, i][row][col] for i in range(p.m)), Fraction(0)) - identity)

    left, right, mixed = [], [], []
    for i in range(p.m):
        for j in range(p.m):
            for row in range(p.d):
                for col in range(p.d):
                    left_product = sum((p.multiplication[i][j][ell] * actions[0, ell][row][col]
                                        for ell in range(p.m)), Fraction(0))
                    right_product = sum((p.multiplication[i][j][ell] * actions[1, ell][row][col]
                                         for ell in range(p.m)), Fraction(0))
                    left.append(compose(actions[0, i], actions[0, j], row, col) - left_product)
                    right.append(compose(actions[1, j], actions[1, i], row, col) - right_product)
                    mixed.append(compose(actions[0, i], actions[1, j], row, col) -
                                 compose(actions[1, j], actions[0, i], row, col))
    residuals = {"weak": weak_residuals, "unit_left": unit_left, "unit_right": unit_right,
                 "left": left, "right": right, "mixed": mixed}
    return residuals


def _deformation_operator(p):
    """Exact polarization of quadratic equations; independent of analytic producer."""
    observable = _observability(p)
    width = 2 * p.m * p.d * p.d

    def evaluate(coordinates):
        residuals = _strict_residuals(p, coordinates, observable)
        return [v for values in residuals.values() for v in values]

    base = evaluate(p.strict_point)
    require(not any(base), "CERTIFICATE_WITNESS_INVALID", "Tangent analysis requires a valid strict point")
    columns = []
    started = time.monotonic()
    for j in range(width):
        plus = evaluate([v + int(i == j) for i, v in enumerate(p.strict_point)])
        minus = evaluate([v - int(i == j) for i, v in enumerate(p.strict_point)])
        columns.append([(a - b) / 2 for a, b in zip(plus, minus)])
        require(time.monotonic() - started <= 10, "RESOURCE_LIMIT", "Verifier polarization time budget exceeded")
    rows = list(map(list, zip(*columns)))
    equations = list(observable["equation_layout"])
    for family in ("unit_left", "unit_right", "left", "right", "mixed"):
        indices = ([r, c] for r in range(p.d) for c in range(p.d)) if family.startswith("unit_") else (
            [i, j, r, c] for i in range(p.m) for j in range(p.m) for r in range(p.d) for c in range(p.d))
        equations.extend({"family": family, "indices": value} for value in indices)
    operator = {"schema_version": "1", "problem_digest": p.sha256,
                "variable_layout": observable["variable_layout"], "equation_layout": equations,
                "matrix": matrix_json(rows, width), "rhs": vector([0] * len(rows))}
    return operator, rows, evaluate


def _tangent(p, result):
    fields(result, ("schema_version", "problem_digest", "point_sha256", "operator", "rank",
                    "ambient_dimension", "dimension", "basis", "rigidity", "code"))
    operator, rows, _ = _deformation_operator(p)
    width = operator["matrix"]["cols"]
    rank = _rank(rows)
    require(result["schema_version"] == "1" and result["problem_digest"] == p.sha256 and
            result["point_sha256"] == digest(p.to_json()["strict_point"]) and
            canonical(result["operator"]) == canonical(operator),
            "CERTIFICATE_WITNESS_INVALID", "Wrong tangent input or operator")
    require(all(type(result[key]) is int for key in ("rank", "ambient_dimension", "dimension")) and
            result["rank"] == rank and result["ambient_dimension"] == width and result["dimension"] == width - rank,
            "CERTIFICATE_WITNESS_INVALID", "Wrong tangent rank or dimension")
    require(type(result["basis"]) is list and len(result["basis"]) == width - rank,
            "CERTIFICATE_WITNESS_INVALID", "Incomplete tangent basis")
    basis = [read_vector(v, width) for v in result["basis"]]
    require(all(sum(a*b for a, b in zip(row, v)) == 0 for row in rows for v in basis) and
            _rank(basis) == width - rank, "CERTIFICATE_WITNESS_INVALID", "Tangent basis replay failed")
    require(result["code"] is None and result["rigidity"] == (
        "infinitesimally_rigid" if width == rank else "nonzero_tangent"),
        "CERTIFICATE_WITNESS_INVALID", "Wrong rigidity classification")
    return "tangent.analyzed"


def _obstruction(p, result):
    fields(result, ("schema_version", "problem_digest", "point_sha256", "direction_sha256",
                    "tangent_operator_sha256", "order", "receptacle", "direction", "tangent_residuals",
                    "source_vector", "lift_witness", "nonmembership_witness", "status", "code"))
    operator, rows, evaluate = _deformation_operator(p)
    height, width = len(rows), operator["matrix"]["cols"]
    direction = read_vector(result["direction"], width)
    require(result["schema_version"] == "1" and result["problem_digest"] == p.sha256 and
            result["point_sha256"] == digest(p.to_json()["strict_point"]) and
            result["direction_sha256"] == digest(p.to_json()["tangent_direction"]) and
            result["tangent_operator_sha256"] == digest(operator) and
            tuple(direction) == p.tangent_direction and type(result["order"]) is int and result["order"] == 2 and
            result["receptacle"] == "coker_D_p", "CERTIFICATE_WITNESS_INVALID", "Wrong obstruction input")
    residuals = [sum(a*b for a, b in zip(row, direction)) for row in rows]
    require(read_vector(result["tangent_residuals"], height) == residuals,
            "CERTIFICATE_WITNESS_INVALID", "Wrong tangent membership residuals")
    if any(residuals):
        require(result["status"] == "not_tangent" and result["code"] == "NOT_TANGENT_VECTOR" and
                all(result[k] is None for k in ("source_vector", "lift_witness", "nonmembership_witness")),
                "CERTIFICATE_WITNESS_INVALID", "Wrong non-tangent result")
        return "obstruction.not_tangent"
    plus = evaluate([a+b for a, b in zip(p.strict_point, direction)])
    minus = evaluate([a-b for a, b in zip(p.strict_point, direction)])
    source = [(a+b)/2 for a, b in zip(plus, minus)]  # F(p)=0 was verified above.
    require(read_vector(result["source_vector"], height) == source,
            "CERTIFICATE_WITNESS_INVALID", "Wrong quadratic source")
    if result["status"] == "vanishes":
        require(result["code"] is None and result["nonmembership_witness"] is None,
                "CERTIFICATE_WITNESS_INVALID", "Wrong positive obstruction fields")
        lift = read_vector(result["lift_witness"], width)
        require(all(sum(a*b for a, b in zip(row, lift)) + q == 0 for row, q in zip(rows, source)),
                "CERTIFICATE_WITNESS_INVALID", "Lift fails D_p eta + Q = 0")
        return "obstruction.vanishes"
    require(result["status"] == "nonzero" and result["code"] == "OBSTRUCTED_ORDER_2" and result["lift_witness"] is None,
            "CERTIFICATE_WITNESS_INVALID", "Wrong negative obstruction fields")
    witness = result["nonmembership_witness"]
    fields(witness, ("left_null_vector", "pairing"))
    lam = read_vector(witness["left_null_vector"], height)
    require(all(sum(lam[i] * rows[i][j] for i in range(height)) == 0 for j in range(width)),
            "CERTIFICATE_WITNESS_INVALID", "Obstruction witness fails lambda D_p = 0")
    pairing = sum(a*b for a, b in zip(lam, source))
    require(pairing != 0 and pairing == rational(witness["pairing"], 8192),
            "CERTIFICATE_WITNESS_INVALID", "Obstruction witness fails lambda Q != 0")
    return "obstruction.nonzero"


def verify_certificate(cert, problem_spec):
    """Fail closed on unknown envelopes, corrupt payloads, or false witnesses."""
    report = {"schema_version": "1", "verifier_version": __version__,
              "certificate_verified": False, "status": "REJECTED", "code": None}
    try:
        canonical(cert)
        fields(cert, ("schema_version", "compiler_version", "input_sha256", "scope", "backend_id",
                      "pass", "claim", "result", "provenance", "certificate_sha256"))
        require(cert["schema_version"] == "1" and cert["compiler_version"] in ("0.1.0", __version__) and
                (cert["compiler_version"] != "0.1.0" or cert["pass"] in ("structure", "observability", "weak", "strict")),
                "CERTIFICATE_SCHEMA_MISMATCH", "Unsupported certificate/compiler version")
        require(cert["scope"] == "general_fd" and cert["backend_id"] == BACKEND,
                "BACKEND_SCOPE_MISMATCH", "Unsupported certificate scope/backend")
        require(cert["certificate_sha256"] == digest({k: v for k, v in cert.items() if k != "certificate_sha256"}),
                "CERTIFICATE_DIGEST_MISMATCH", "Certificate digest mismatch")
        p = parse_problem(problem_spec)
        require(cert["input_sha256"] == p.sha256, "CERTIFICATE_INPUT_MISMATCH", "Input digest mismatch")
        require(cert["provenance"] == {"kind": "handoff_spec", "file": "MATH_KERNEL_SPEC.md",
                                      "sha256": "29d516d0bfc12dc17ee8f29ac842d939910db73dcfbd4c303a735d21719da4e6"},
                "CERTIFICATE_SCHEMA_MISMATCH", "Unknown provenance contract")
        require(cert["pass"] in p.to_json()["requested_passes"], "CERTIFICATE_SCHEMA_MISMATCH", "Unrequested pass")
        expected = _structure(p)
        if cert["pass"] == "structure":
            claim = "structure.rejected" if expected["code"] else "structure.valid"
        elif cert["pass"] == "observability":
            require(expected["code"] is None, "CERTIFICATE_WITNESS_INVALID", "Algebra is not validated")
            expected, claim = _observability(p), "observability.compiled"
        elif cert["pass"] == "weak":
            require(expected["code"] is None, "CERTIFICATE_WITNESS_INVALID", "Algebra is not validated")
            claim = _weak(cert["result"], _observability(p))
            expected = cert["result"]
        elif cert["pass"] == "strict":
            require(expected["code"] is None, "CERTIFICATE_WITNESS_INVALID", "Algebra is not validated")
            claim = _strict(p, cert["result"])
            expected = cert["result"]
        elif cert["pass"] in ("tangent", "obstruction"):
            require(expected["code"] is None, "CERTIFICATE_WITNESS_INVALID", "Algebra is not validated")
            claim = _tangent(p, cert["result"]) if cert["pass"] == "tangent" else _obstruction(p, cert["result"])
            expected = cert["result"]
        else:
            raise InputError("CERTIFICATE_SCHEMA_MISMATCH", "Unknown certificate pass")
        require(cert["claim"] == claim and canonical(cert["result"]) == canonical(expected),
                "CERTIFICATE_WITNESS_INVALID", "Pass result/claim does not match input")
        return {**report, "certificate_verified": True, "status": "VERIFIED", "claim": cert["claim"]}
    except InputError as exc:
        return {**report, "status": "INCOMPLETE_RESOURCE_LIMIT" if exc.code == "RESOURCE_LIMIT" else "REJECTED",
                "code": exc.code, "message": str(exc)}
    except (KeyError, TypeError, ValueError, IndexError, RecursionError) as exc:
        return {**report, "code": "CERTIFICATE_SCHEMA_MISMATCH", "message": str(exc)}
    except Exception:
        LOG.exception("Verifier infrastructure failure")
        return {**report, "status": "INFRASTRUCTURE_ERROR", "code": "INTERNAL_ERROR"}


def verify_run(run):
    """Replay every certificate and check that the facade summary is not forged."""
    try:
        canonical(run)
        fields(run, ("schema_version", "status", "infrastructure_status", "mathematical_status",
                     "certificate_verified", "verified_through", "certificates", "problem", "result", "code"))
        p = parse_problem(run["problem"])
        certs = run["certificates"]
        require(type(certs) is list and 1 <= len(certs) <= 6,
                "CERTIFICATE_SCHEMA_MISMATCH", "Expected nonempty pass certificate sequence")
        reports = [verify_certificate(c, p.to_json()) for c in certs]
        if any(r["status"] == "INCOMPLETE_RESOURCE_LIMIT" for r in reports):
            return {"status": "INCOMPLETE_RESOURCE_LIMIT", "certificate_verified": False,
                    "code": "RESOURCE_LIMIT", "reports": reports}
        if any(r["status"] == "INFRASTRUCTURE_ERROR" for r in reports):
            return {"status": "INFRASTRUCTURE_ERROR", "certificate_verified": False,
                    "code": "INTERNAL_ERROR", "reports": reports}
        require(all(r["certificate_verified"] for r in reports), "CERTIFICATE_WITNESS_INVALID", "A certificate failed replay")
        names = [c["pass"] for c in certs]
        requested = p.to_json()["requested_passes"]
        rejected_claims = ("structure.rejected", "weak.inconsistent", "strict.point_invalid", "obstruction.nonzero", "obstruction.not_tangent")
        require(names == requested[:len(names)] and all(c["claim"] not in rejected_claims for c in certs[:-1]),
                "CERTIFICATE_SCHEMA_MISMATCH", "Invalid pass sequence")
        last = certs[-1]
        rejected = last["claim"] in rejected_claims
        require(rejected or names == requested, "CERTIFICATE_SCHEMA_MISMATCH", "Incomplete certificate sequence")
        require(run["schema_version"] == "1" and run["certificate_verified"] is True and
                run["infrastructure_status"] == "success" and run["verified_through"] == names and
                run["status"] == ("MATHEMATICALLY_REJECTED" if rejected else "VERIFIED") and
                run["mathematical_status"] == last["claim"] and run["code"] == last["result"].get("code") and
                canonical(run["result"]) == canonical(last["result"]),
                "CERTIFICATE_WITNESS_INVALID", "Run summary differs from certified result")
        return {"status": "VERIFIED", "certificate_verified": True, "verifier_version": __version__,
                "mathematical_status": last["claim"], "verified_through": names, "reports": reports}
    except (InputError, KeyError, TypeError) as exc:
        return {"status": "INCOMPLETE_RESOURCE_LIMIT" if getattr(exc, "code", None) == "RESOURCE_LIMIT" else "REJECTED",
                "certificate_verified": False,
                "code": getattr(exc, "code", "CERTIFICATE_SCHEMA_MISMATCH"), "message": str(exc)}
