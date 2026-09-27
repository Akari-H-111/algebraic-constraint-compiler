"""Relation-level tangent and order-two witnesses; no higher-order claims."""

from fractions import Fraction
import time

from .compiler import _actions, _multiply, weak
from .ir import digest, matrix_json, rational, scalar, vector, require


def _terms(p, observable, coordinates, quadratic=False):
    """Analytic derivative D_p(xi), or the homogeneous quadratic term Q(xi)."""
    left, right = _actions(p, p.strict_point)
    x, y = _actions(p, coordinates)
    values = [Fraction(0) for _ in observable["rhs"]]
    if not quadratic:
        for e in observable["matrix"]["entries"]:
            values[e["row"]] += rational(e["value"], 8192) * coordinates[e["col"]]
    for actions in (x, y):
        values.extend(Fraction(0) if quadratic else
                      sum(p.unit[i] * actions[i][row][col] for i in range(p.m))
                      for row in range(p.d) for col in range(p.d))
    families = {name: [] for name in ("left", "right", "mixed")}
    for i in range(p.m):
        for j in range(p.m):
            if quadratic:
                ll, rr = _multiply(x[i], x[j]), _multiply(y[j], y[i])
                lr, rl = _multiply(x[i], y[j]), _multiply(y[j], x[i])
            else:
                ll1, ll2 = _multiply(x[i], left[j]), _multiply(left[i], x[j])
                rr1, rr2 = _multiply(y[j], right[i]), _multiply(right[j], y[i])
                lr1, lr2 = _multiply(x[i], right[j]), _multiply(left[i], y[j])
                rl1, rl2 = _multiply(y[j], left[i]), _multiply(right[j], x[i])
            for row in range(p.d):
                for col in range(p.d):
                    if quadratic:
                        a, b, c = ll[row][col], rr[row][col], lr[row][col] - rl[row][col]
                    else:
                        a = ll1[row][col] + ll2[row][col] - sum(p.multiplication[i][j][k] * x[k][row][col] for k in range(p.m))
                        b = rr1[row][col] + rr2[row][col] - sum(p.multiplication[i][j][k] * y[k][row][col] for k in range(p.m))
                        c = lr1[row][col] + lr2[row][col] - rl1[row][col] - rl2[row][col]
                    for name, value in zip(families, (a, b, c)):
                        families[name].append(value)
    return values + [v for family in families.values() for v in family]


def tangent(p, observable):
    width = 2 * p.m * p.d * p.d
    columns = []
    started = time.monotonic()
    for j in range(width):
        columns.append(_terms(p, observable, [Fraction(int(i == j)) for i in range(width)]))
        require(time.monotonic() - started <= 10, "RESOURCE_LIMIT", "Tangent construction time budget exceeded")
    equations = observable["equation_layout"] + [
        {"family": family, "indices": [row, col]}
        for family in ("unit_left", "unit_right") for row in range(p.d) for col in range(p.d)] + [
        {"family": family, "indices": [i, j, row, col]}
        for family in ("left", "right", "mixed") for i in range(p.m) for j in range(p.m)
        for row in range(p.d) for col in range(p.d)]
    operator = {"schema_version": "1", "problem_digest": p.sha256,
                "variable_layout": observable["variable_layout"], "equation_layout": equations,
                "matrix": matrix_json(list(map(list, zip(*columns))), width),
                "rhs": vector([0] * len(equations))}
    solved = weak(operator)
    return {"schema_version": "1", "problem_digest": p.sha256,
            "point_sha256": digest(p.to_json()["strict_point"]), "operator": operator,
            "rank": solved["rank"], "ambient_dimension": width,
            "dimension": solved["fiber_dimension"], "basis": solved["kernel_basis"],
            "rigidity": "infinitesimally_rigid" if not solved["fiber_dimension"] else "nonzero_tangent",
            "code": None}


def obstruction(p, observable, tangent_result):
    operator = tangent_result["operator"]
    direction = p.tangent_direction
    residuals = _terms(p, observable, direction)
    result = {"schema_version": "1", "problem_digest": p.sha256,
              "point_sha256": tangent_result["point_sha256"],
              "direction_sha256": digest(p.to_json()["tangent_direction"]),
              "tangent_operator_sha256": digest(operator), "order": 2, "receptacle": "coker_D_p",
              "direction": vector(direction), "tangent_residuals": vector(residuals),
              "source_vector": None, "lift_witness": None, "nonmembership_witness": None,
              "status": "not_tangent", "code": "NOT_TANGENT_VECTOR"}
    if any(residuals):
        return result
    source = _terms(p, observable, direction, quadratic=True)
    result["source_vector"] = vector(source)
    solved = weak({**operator, "rhs": vector([-v for v in source])})
    if solved["status"] == "consistent":
        result.update(status="vanishes", code=None, lift_witness=solved["particular_solution"])
    else:
        w = solved["inconsistency_witness"]
        result.update(status="nonzero", code="OBSTRUCTED_ORDER_2", nonmembership_witness={
            "left_null_vector": w["left_null_vector"], "pairing": scalar(-rational(w["rhs_pairing"], 8192))})
    return result
