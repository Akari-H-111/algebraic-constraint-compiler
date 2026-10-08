"""Independent replay for linear certificates. Must not import the producer.

The verifier never trusts the producer's matrix. It re-reads each equation
with its own evaluator by plugging exact numbers into the text, recovers the
coefficients from those evaluations, and then checks each witness using only
exact multiplication and addition. It never runs elimination.
"""

from fractions import Fraction

from . import __version__
from .ir import InputError, canonical, digest, fields, require
from .linear_ir import BACKEND, SCOPE, normalize, read_exact_json, review_lines, tokens, unknowns_in, work_unknowns

CLAIMS = {
    "solve": {"solve.unique", "solve.family", "solve.family_domain_undecided", "solve.no_solution",
              "solve.no_solution_in_domain"},
    "answer": {"answer.correct", "answer.incorrect"},
    "work": {"work.all_steps_valid", "work.error_found", "work.needs_review"},
}


class _Evaluate:
    """Evaluate one equation at an exact point; structural linearity is enforced."""

    def __init__(self, text, point):
        self.items, self.at, self.point, self.text = tokens(text), 0, point, text

    def _next(self):
        item = self.items[self.at] if self.at < len(self.items) else (None, None)
        self.at += 1
        return item

    def _peek(self):
        return self.items[self.at] if self.at < len(self.items) else (None, None)

    def sides(self):
        left = self._sum()
        require(self._next() == ("op", "="), "INVALID_EQUATION", "Expected '='")
        right = self._sum()
        require(self.at == len(self.items), "INVALID_EQUATION", "Trailing symbols")
        return left[0], right[0]

    def _sum(self):
        value, symbolic = self._product()
        while self._peek() in (("op", "+"), ("op", "-")):
            sign = 1 if self._next()[1] == "+" else -1
            other, other_symbolic = self._product()
            value, symbolic = value + sign * other, symbolic or other_symbolic
        return value, symbolic

    def _product(self):
        value, symbolic = self._signed()
        while True:
            kind, item = self._peek()
            if kind == "op" and item in "*/":
                self._next()
                other = self._signed()
            elif kind == "name" or (kind, item) == ("op", "("):
                item, other = "*", self._atom()
            else:
                return value, symbolic
            if item == "*":
                require(not (symbolic and other[1]), "UNSUPPORTED_NONLINEAR", "Product of unknowns")
                value, symbolic = value * other[0], symbolic or other[1]
            else:
                require(not other[1], "UNSUPPORTED_NONLINEAR", "Unknown in a denominator")
                require(other[0] != 0, "DIVISION_BY_ZERO", "Division by zero")
                value = value / other[0]

    def _signed(self):
        if self._peek() in (("op", "+"), ("op", "-")):
            sign = 1 if self._next()[1] == "+" else -1
            value, symbolic = self._signed()
            return sign * value, symbolic
        return self._atom()

    def _atom(self):
        kind, item = self._next()
        if kind == "num":
            return item, False
        if kind == "name":
            return self.point[item], True
        require((kind, item) == ("op", "("), "INVALID_EQUATION", "Expected a value")
        value = self._sum()
        require(self._next() == ("op", ")"), "INVALID_EQUATION", "Missing ')'")
        return value


class _Line:
    """One line of equations read back by evaluation, not by symbolic parsing."""

    def __init__(self, texts, names):
        self.texts, self.names = texts, names
        zero = {n: Fraction(0) for n in names}
        self.rows, self.rhs = [], []
        for text in texts:
            base = self._difference(text, zero)
            row = []
            for n in names:
                row.append(self._difference(text, {**zero, n: Fraction(1)}) - base)
            self.rows.append(row)
            self.rhs.append(-base)

    @staticmethod
    def _difference(text, point):
        left, right = _Evaluate(text, point).sides()
        return left - right

    def sides_at(self, values):
        point = dict(zip(self.names, values))
        return [_Evaluate(text, point).sides() for text in self.texts]


def _q(value):
    return read_exact_json(value)


def _qs(values, size=None):
    require(type(values) is list and (size is None or len(values) == size), "INVALID_SCHEMA", "Vector size mismatch")
    return [_q(v) for v in values]


def _dot(u, v):
    return sum((a * b for a, b in zip(u, v)), Fraction(0))


def _combine(weights, rows):
    width = len(rows[0]) if rows else 0
    return [sum((w * row[j] for w, row in zip(weights, rows)), Fraction(0)) for j in range(width)]


class _Checks:
    def __init__(self):
        self.items = []

    def add(self, name, ok, **detail):
        self.items.append({"check": name, "ok": bool(ok), **detail})
        return bool(ok)

    @property
    def ok(self):
        return bool(self.items) and all(item["ok"] for item in self.items)


def _reading(checks, line, result_matrix, result_rhs, label):
    same = (len(result_matrix) == len(line.rows)
            and all(_qs(r, len(line.names)) == row for r, row in zip(result_matrix, line.rows))
            and _qs(result_rhs, len(line.rhs)) == line.rhs)
    checks.add("independent_reading", same, line=label)


def _unique(checks, line, block, label):
    """Every solution must equal x (left inverse rows), and x solves every equation."""
    n = len(line.names)
    solution = _qs(block["solution"], n)
    derivations = [_qs(d, len(line.rows)) for d in block["derivations"]]
    require(len(derivations) == n, "INVALID_SCHEMA", "One derivation per unknown is required")
    for j, weights in enumerate(derivations):
        isolated = _combine(weights, line.rows)
        checks.add("isolates_unknown", isolated == [Fraction(int(k == j)) for k in range(n)]
                   and _dot(weights, line.rhs) == solution[j], line=label, unknown=line.names[j])
    for index, (left, right) in enumerate(line.sides_at(solution)):
        checks.add("substitution", left == right, line=label, equation=index)
    return solution


def _domain_reason(q, domain):
    if domain == "rational":
        return None
    if q.denominator != 1:
        return "not_integer"
    return "negative" if domain == "nonnegative_integer" and q < 0 else None


def _verify_solve(checks, spec, claim, result):
    names = unknowns_in(spec["equations"])
    line = _Line(spec["equations"], names)
    checks.add("unknowns", result["unknowns"] == names)
    _reading(checks, line, result["matrix"], result["rhs"], 0)
    checks.add("domain", result["domain"] == spec["domain"])
    if claim == "solve.no_solution":
        weights = _qs(result["combination"], len(line.rows))
        pairing = _dot(weights, line.rhs)
        checks.add("cancels_every_unknown", not any(_combine(weights, line.rows)))
        checks.add("leaves_contradiction", pairing != 0 and pairing == _q(result["pairing"]))
        return
    if claim in ("solve.unique", "solve.no_solution_in_domain"):
        solution = _unique(checks, line, result, 0)
        reasons = [(j, _domain_reason(q, spec["domain"])) for j, q in enumerate(solution)]
        bad = [(j, why) for j, why in reasons if why]
        if claim == "solve.unique":
            checks.add("within_domain", not bad and "domain_violation" not in result)
        else:
            stated = result["domain_violation"]
            checks.add("outside_domain", bool(bad) and (stated["unknown"], stated["reason"]) == bad[0])
        return
    n = len(names)
    particular = _qs(result["solution"], n)
    kernel = [_qs(k, n) for k in result["kernel"]]
    free, pivots = result["free_unknowns"], result["pivot_unknowns"]
    checks.add("partition", sorted(free + pivots) == list(range(n)) and len(set(free)) == len(free))
    for index, (left, right) in enumerate(line.sides_at(particular)):
        checks.add("substitution", left == right, line=0, equation=index)
    for k in kernel:
        checks.add("kernel_direction", not any(_dot(row, k) for row in line.rows))
    checks.add("kernel_independent", len(kernel) == len(free) and all(
        k[f] == Fraction(int(i == j)) for i, k in enumerate(kernel) for j, f in enumerate(free)))
    witness = [_qs(w, len(line.rows)) for w in result["rank_witness"]]
    checks.add("rank_witness", len(witness) == len(pivots) and all(
        _combine(w, line.rows)[p] == Fraction(int(i == j)) for i, w in enumerate(witness)
        for j, p in enumerate(pivots)))
    checks.add("dimension", len(pivots) + len(kernel) == n)
    undecided = claim == "solve.family_domain_undecided"
    checks.add("domain_scope", (spec["domain"] != "rational") == undecided
               and (result.get("domain_decided") is False) == undecided)


def _verify_answer(checks, spec, claim, result):
    names = unknowns_in(spec["equations"])
    line = _Line(spec["equations"], names)
    checks.add("unknowns", result["unknowns"] == names)
    _reading(checks, line, result["matrix"], result["rhs"], 0)
    values = [_q(spec["answer"][n]) for n in names]
    checks.add("assignment", _qs(result["assignment"], len(names)) == values)
    sides = line.sides_at(values)
    checks.add("evaluated_sides", [[_q(a), _q(b)] for a, b in result["sides"]] == [list(s) for s in sides])
    failing = [i for i, (left, right) in enumerate(sides) if left != right]
    checks.add("failing_equations", result["failing_equations"] == failing)
    bad = [(j, why) for j, q in enumerate(values) if (why := _domain_reason(q, spec["domain"]))]
    stated = result.get("domain_violation")
    checks.add("domain_check", (stated is None and not bad) or (
        bool(bad) and stated is not None and (stated["unknown"], stated["reason"]) == bad[0]))
    checks.add("verdict", (claim == "answer.incorrect") == bool(failing or bad))
    if "unique_solution" in result:
        _unique(checks, line, result["unique_solution"], 0)


def _verify_work(checks, spec, claim, result):
    held = review_lines(spec)  # derived from the bound input, never from the result
    names = work_unknowns(spec)
    checks.add("unknowns", result["unknowns"] == names)
    checks.add("review_lines", result.get("review_lines", []) == held)
    lines = [_Line([] if i in held else texts, names) for i, texts in enumerate(spec["steps"])]
    require(len(result["lines"]) == len(lines), "INVALID_SCHEMA", "Line count mismatch")
    for index, (line, stated) in enumerate(zip(lines, result["lines"])):
        _reading(checks, line, stated["matrix"], stated["rhs"], index)
    transitions = result["transitions"]
    require(type(transitions) is list and len(transitions) == len(lines) - 1, "INVALID_SCHEMA",
            "One transition per consecutive pair of lines is required")
    first = None
    for index, step in enumerate(transitions):
        before, after = lines[index], lines[index + 1]
        checks.add("transition_order", (step["from"], step["to"]) == (index, index + 1))
        if index in held or index + 1 in held:
            # No witness may accompany a line nobody has confirmed, and no verdict may be implied.
            checks.add("withheld_for_review", step["status"] == "needs_review" and set(step) == {"from", "to", "status"},
                       step=index)
        elif step["status"] == "vacuous":
            weights = _qs(step["combination"], len(before.rows))
            checks.add("previous_line_contradiction", not any(_combine(weights, before.rows))
                       and _dot(weights, before.rhs) != 0, step=index)
        elif step["status"] == "sound":
            combos = [_qs(c, len(before.rows)) for c in step["combinations"]]
            checks.add("follows_from_previous", len(combos) == len(after.rows) and all(
                _combine(c, before.rows) == row and _dot(c, before.rhs) == b
                for c, row, b in zip(combos, after.rows, after.rhs)), step=index)
        elif step["status"] == "breaks":
            point = _qs(step["point"], len(names))
            holds = all(left == right for left, right in before.sides_at(point))
            failing = step["failing_equation"]
            require(type(failing) is int and 0 <= failing < len(after.rows), "INVALID_SCHEMA", "Bad equation index")
            left, right = after.sides_at(point)[failing]
            checks.add("counterexample", holds and left != right, step=index)
            if first is None:
                first = index
        else:
            raise InputError("INVALID_SCHEMA", "Unknown transition status")
    checks.add("first_error", result["first_error"] == first)
    expected = "work.error_found" if first is not None else "work.needs_review" if held else "work.all_steps_valid"
    checks.add("verdict", claim == expected)
    for key, index in (("original_solution", 0), ("final_solution", len(lines) - 1)):
        if key in result:
            checks.add("solution_line_checked", index not in held, line=index)
            if index not in held:
                _unique(checks, lines[index], result[key], index)


VERIFY = {"solve": _verify_solve, "answer": _verify_answer, "work": _verify_work}


def verify_bundle(bundle):
    """Replay one portable bundle. Any malformed or unexpected content fails closed."""
    report = {"schema_version": "1", "verifier_version": __version__, "certificate_verified": False,
              "status": "REJECTED", "code": None, "claim": None, "checks": []}
    try:
        canonical(bundle)
        fields(bundle, ("schema_version", "format", "input", "certificate"))
        require(bundle["schema_version"] == "1" and bundle["format"] == "show-your-work/bundle",
                "UNSUPPORTED_SCHEMA", "Not a Show Your Work bundle")
        spec, sha = normalize(bundle["input"])
        require(spec == bundle["input"], "INPUT_NOT_CANONICAL", "Bundle input is not in canonical form")
        cert = bundle["certificate"]
        fields(cert, ("schema_version", "compiler_version", "backend_id", "scope", "task", "input_sha256",
                      "claim", "result", "certificate_sha256"))
        body = {k: v for k, v in cert.items() if k != "certificate_sha256"}
        require(cert["certificate_sha256"] == digest(body), "CERTIFICATE_DIGEST_MISMATCH", "Certificate digest mismatch")
        require(cert["input_sha256"] == sha, "INPUT_DIGEST_MISMATCH", "Certificate is bound to a different input")
        require(cert["schema_version"] == "1" and cert["backend_id"] == BACKEND and cert["scope"] == SCOPE,
                "BACKEND_SCOPE_MISMATCH", "Unexpected backend or scope")
        require(cert["task"] == spec["task"] and cert["claim"] in CLAIMS[spec["task"]],
                "CLAIM_MISMATCH", "Claim does not match the task")
        report["claim"] = cert["claim"]
        checks = _Checks()
        try:
            VERIFY[spec["task"]](checks, spec, cert["claim"], cert["result"])
        except (KeyError, TypeError, IndexError, ValueError, ZeroDivisionError) as exc:
            if isinstance(exc, InputError):
                raise
            raise InputError("INVALID_SCHEMA", "Certificate result is malformed") from exc
        report["checks"] = checks.items
        if checks.ok:
            report.update(certificate_verified=True, status="VERIFIED")
        else:
            failed = next(item for item in checks.items if not item["ok"])
            report.update(code="WITNESS_REJECTED", message=f"Check failed: {failed['check']}")
        return report
    except InputError as exc:
        report.update(code=exc.code, message=str(exc))
        return report
