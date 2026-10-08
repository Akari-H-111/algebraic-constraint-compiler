"""Exact producer for linear systems, answer checks and step-by-step work.

Every positive or negative conclusion carries a witness that the independent
verifier can check with multiplication and addition alone:

* unique solution: for each unknown, a combination of the equations that
  isolates it (y . A = e_j), plus substitution of the solution;
* infinitely many: particular solution, independent kernel basis and a rank
  witness, so the family is complete;
* no solution: a combination that cancels every unknown but leaves 0 = c != 0;
* a broken step: a point that satisfies one line of work but not the next;
* a sound step: each new equation as an exact combination of the previous line.

No language model output is ever used as a mathematical witness.
"""

import logging
from fractions import Fraction
import random
import secrets
import time

from . import __version__
from .ir import InputError, canonical, digest, require
from .linear_ir import (BACKEND, MAX_NOTE, MAX_TEXT, SCOPE, Reading, exact_json, normalize, normalize_text, read_exact_json,
                        review_lines, tokens, unknowns_in, work_unknowns)

LOG = logging.getLogger(__name__)
NEGATIVE_CLAIMS = {"solve.no_solution", "solve.no_solution_in_domain", "answer.incorrect", "work.error_found"}


# --- Symbolic reading: text -> exact linear form -----------------------------------------------

class _Form:
    """sum(coefficients[name] * name) + constant, with a structural 'has unknown' flag."""

    __slots__ = ("coefficients", "constant", "symbolic")

    def __init__(self, coefficients=None, constant=Fraction(0), symbolic=False):
        self.coefficients = {k: v for k, v in (coefficients or {}).items() if v}
        self.constant = Fraction(constant)
        self.symbolic = symbolic

    def plus(self, other, sign=1):
        merged = dict(self.coefficients)
        for name, value in other.coefficients.items():
            merged[name] = merged.get(name, Fraction(0)) + sign * value
        return _Form(merged, self.constant + sign * other.constant, self.symbolic or other.symbolic)

    def scaled(self, factor):
        return _Form({k: v * factor for k, v in self.coefficients.items()}, self.constant * factor, self.symbolic)


class _Reader:
    def __init__(self, text):
        self.items, self.index, self.text = tokens(text), 0, text

    def peek(self):
        return self.items[self.index] if self.index < len(self.items) else (None, None)

    def take(self):
        item = self.peek()
        self.index += 1
        return item

    def fail(self, message):
        raise InputError("INVALID_EQUATION", f"{message} in '{self.text}'")

    def equation(self):
        left = self.expression()
        if self.take() != ("op", "="):
            self.fail("Expected '='")
        right = self.expression()
        if self.index != len(self.items):
            self.fail("Unexpected symbol after the equation")
        return left, right

    def expression(self):
        value = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            sign = 1 if self.take()[1] == "+" else -1
            value = value.plus(self.term(), sign)
        return value

    def term(self):
        value = self.unary()
        while True:
            kind, item = self.peek()
            if kind == "op" and item in "*/":
                self.take()
                value = self._combine(value, self.unary(), item)
            elif kind == "name" or (kind, item) == ("op", "("):
                value = self._combine(value, self.primary(), "*")
            else:
                return value

    def unary(self):
        if self.peek() in (("op", "+"), ("op", "-")):
            sign = 1 if self.take()[1] == "+" else -1
            return self.unary().scaled(sign)
        return self.primary()

    def primary(self):
        kind, item = self.take()
        if kind == "num":
            return _Form(constant=item)
        if kind == "name":
            return _Form({item: Fraction(1)}, symbolic=True)
        if (kind, item) == ("op", "("):
            value = self.expression()
            if self.take() != ("op", ")"):
                self.fail("Missing ')'")
            return value
        self.fail("Expected a number, an unknown or '('")

    def _combine(self, left, right, op):
        if op == "*":
            if left.symbolic and right.symbolic:
                raise InputError("UNSUPPORTED_NONLINEAR",
                                 f"'{self.text}' multiplies unknowns together; only linear equations are supported")
            if left.symbolic:
                return left.scaled(right.constant)
            return _Form({k: v * left.constant for k, v in right.coefficients.items()},
                         left.constant * right.constant, right.symbolic)
        if right.symbolic:
            raise InputError("UNSUPPORTED_NONLINEAR", f"'{self.text}' divides by an unknown; only linear equations are supported")
        if right.constant == 0:
            raise InputError("DIVISION_BY_ZERO", f"'{self.text}' divides by zero")
        return left.scaled(1 / right.constant)


def read_line(texts, names):
    """Rows and right-hand sides over the shared unknown order, plus both sides."""
    rows, rhs, sides = [], [], []
    for text in texts:
        left, right = _Reader(text).equation()
        difference = left.plus(right, -1)
        rows.append(tuple(difference.coefficients.get(n, Fraction(0)) for n in names))
        rhs.append(-difference.constant)
        sides.append((left, right))
    for q in [x for row in rows for x in row] + rhs:
        require(max(abs(q.numerator).bit_length(), q.denominator.bit_length()) <= 1024,
                "RESOURCE_LIMIT", "Coefficient exceeds the exact size limit")
    return Reading(tuple(rows), tuple(rhs), tuple(sides))


# --- Exact elimination with row-operation tracking ------------------------------------------------

def _rref(rows, rhs):
    """Reduced row echelon form of [A|b] with combos[i] . [A|b] == reduced[i]."""
    height, width = len(rows), len(rows[0]) if rows else 0
    reduced = [list(row) + [b] for row, b in zip(rows, rhs)]
    combos = [[Fraction(int(i == j)) for j in range(height)] for i in range(height)]
    pivots, started = [], time.monotonic()
    for col in range(width):
        found = next((i for i in range(len(pivots), height) if reduced[i][col]), None)
        if found is None:
            continue
        top = len(pivots)
        reduced[top], reduced[found] = reduced[found], reduced[top]
        combos[top], combos[found] = combos[found], combos[top]
        scale = reduced[top][col]
        reduced[top] = [x / scale for x in reduced[top]]
        combos[top] = [x / scale for x in combos[top]]
        for i in range(height):
            if i != top and reduced[i][col]:
                factor = reduced[i][col]
                reduced[i] = [x - factor * y for x, y in zip(reduced[i], reduced[top])]
                combos[i] = [x - factor * y for x, y in zip(combos[i], combos[top])]
        require(time.monotonic() - started <= 5, "RESOURCE_LIMIT", "Exact elimination time budget exceeded")
        pivots.append(col)
    return reduced, combos, pivots


def _dot(u, v):
    return sum((a * b for a, b in zip(u, v)), Fraction(0))


def _analyze(rows, rhs):
    """Classify A x = b with verifiable witnesses."""
    width = len(rows[0])
    reduced, combos, pivots = _rref(rows, rhs)
    bad = next((i for i, row in enumerate(reduced) if not any(row[:-1]) and row[-1]), None)
    if bad is not None:
        return {"kind": "no_solution", "combination": combos[bad], "pairing": reduced[bad][-1]}
    particular = [Fraction(0)] * width
    for i, col in enumerate(pivots):
        particular[col] = reduced[i][-1]
    if len(pivots) == width:
        derivations = [None] * width
        for i, col in enumerate(pivots):
            derivations[col] = combos[i]
        return {"kind": "unique", "solution": particular, "derivations": derivations}
    free = [c for c in range(width) if c not in pivots]
    kernel = []
    for f in free:
        k = [Fraction(0)] * width
        k[f] = Fraction(1)
        for i, col in enumerate(pivots):
            k[col] = -reduced[i][f]
        kernel.append(k)
    return {"kind": "family", "solution": particular, "kernel": kernel, "free": free, "pivots": pivots,
            "rank_witness": [combos[i] for i in range(len(pivots))], "reduced": reduced}


def _in_domain(q, domain):
    if domain == "rational":
        return None
    if q.denominator != 1:
        return "not_integer"
    if domain == "nonnegative_integer" and q < 0:
        return "negative"
    return None


def _vec(values):
    return [exact_json(v) for v in values]


def _matrix(rows):
    return [_vec(row) for row in rows]


# --- Tasks -----------------------------------------------------------------------------------------

def _solve(spec):
    names = unknowns_in(spec["equations"])
    reading = read_line(spec["equations"], names)
    found = _analyze(reading.rows, reading.rhs)
    result = {"unknowns": names, "matrix": _matrix(reading.rows), "rhs": _vec(reading.rhs),
              "domain": spec["domain"]}
    if found["kind"] == "no_solution":
        result.update(combination=_vec(found["combination"]), pairing=exact_json(found["pairing"]))
        return "solve.no_solution", result
    if found["kind"] == "unique":
        result.update(solution=_vec(found["solution"]), derivations=_matrix(found["derivations"]))
        violation = next(((j, why) for j, q in enumerate(found["solution"])
                          if (why := _in_domain(q, spec["domain"]))), None)
        if violation:
            result["domain_violation"] = {"unknown": violation[0], "reason": violation[1]}
            return "solve.no_solution_in_domain", result
        return "solve.unique", result
    result.update(solution=_vec(found["solution"]), kernel=_matrix(found["kernel"]),
                  free_unknowns=found["free"], pivot_unknowns=found["pivots"],
                  rank_witness=_matrix(found["rank_witness"]))
    if spec["domain"] != "rational":
        result["domain_decided"] = False
        return "solve.family_domain_undecided", result
    return "solve.family", result


def _answer(spec):
    names = unknowns_in(spec["equations"])
    reading = read_line(spec["equations"], names)
    values = [read_exact_json(spec["answer"][n]) for n in names]
    sides = [[_value(left, names, values), _value(right, names, values)] for left, right in reading.sides]
    failing = [i for i, (l, r) in enumerate(sides) if l != r]
    violation = next(((j, why) for j, q in enumerate(values) if (why := _in_domain(q, spec["domain"]))), None)
    result = {"unknowns": names, "matrix": _matrix(reading.rows), "rhs": _vec(reading.rhs),
              "assignment": _vec(values), "sides": [_vec(s) for s in sides], "failing_equations": failing,
              "domain": spec["domain"]}
    if violation:
        result["domain_violation"] = {"unknown": violation[0], "reason": violation[1]}
    found = _analyze(reading.rows, reading.rhs)
    if found["kind"] == "unique":
        result["unique_solution"] = {"solution": _vec(found["solution"]), "derivations": _matrix(found["derivations"])}
    return ("answer.incorrect" if failing or violation else "answer.correct"), result


def _value(form, names, values):
    return form.constant + sum((form.coefficients.get(n, Fraction(0)) * v for n, v in zip(names, values)), Fraction(0))


def _transition(before, after):
    """Is every solution of `before` also a solution of `after`? Return a witness either way."""
    reduced, combos, pivots = _rref(before.rows, before.rhs)
    bad = next((i for i, row in enumerate(reduced) if not any(row[:-1]) and row[-1]), None)
    if bad is not None:
        return {"status": "vacuous", "combination": _vec(combos[bad])}
    found = _analyze(before.rows, before.rhs)
    particular = found["solution"]
    kernel = found.get("kernel", [])
    combinations = []
    for row, b in zip(after.rows, after.rhs):
        target = list(row) + [b]
        remainder = list(target)
        coefficients = [Fraction(0)] * len(before.rows)
        for i, col in enumerate(pivots):
            factor = remainder[col]
            if factor:
                remainder = [x - factor * y for x, y in zip(remainder, reduced[i])]
                coefficients = [x + factor * y for x, y in zip(coefficients, combos[i])]
        if any(remainder):
            point = particular
            if _dot(row, point) == b:
                direction = next(k for k in kernel if _dot(row, k))
                point = [x + y for x, y in zip(particular, direction)]
            failing = next(i for i, (r, c) in enumerate(zip(after.rows, after.rhs)) if _dot(r, point) != c)
            return {"status": "breaks", "point": _vec(point), "failing_equation": failing}
        combinations.append(coefficients)
    return {"status": "sound", "combinations": _matrix(combinations)}


def _hint(before, after, names):
    """Uncertified, rule-based guess at the kind of slip for one-unknown, one-equation lines."""
    if len(names) != 1 or len(before.sides) != 1 or len(after.sides) != 1:
        return None
    (l0, r0), (l1, r1) = before.sides[0], after.sides[0]
    pair = lambda form: (form.coefficients.get(names[0], Fraction(0)), form.constant)
    (lx0, lc0), (rx0, rc0), (lx1, lc1), (rx1, rc1) = pair(l0), pair(r0), pair(l1), pair(r1)
    if (lx0, rx0) == (lx1, rx1):
        # A constant left one side and reappeared on the other with the same sign.
        for moved, arrived in ((lc0 - lc1, rc1 - rc0), (rc0 - rc1, lc1 - lc0)):
            if moved and arrived == moved:
                return {"code": "SIGN_WHEN_MOVING", "term": exact_json(moved)}
    for (a0, a1), (b0, b1) in (((pair(l0), pair(l1)), (pair(r0), pair(r1))),
                               ((pair(r0), pair(r1)), (pair(l0), pair(l1)))):
        ratios = {q1 / q0 for q0, q1 in zip(a0, a1) if q0}
        zeros_kept = all(q1 == 0 for q0, q1 in zip(a0, a1) if not q0)
        if b0 == b1 and zeros_kept and len(ratios) == 1 and ratios != {1}:
            return {"code": "ONE_SIDE_ONLY", "factor": exact_json(ratios.pop())}
    return {"code": "ARITHMETIC_SLIP"}


def _work(spec):
    held = set(review_lines(spec))
    names = work_unknowns(spec)
    readings = [read_line([] if i in held else line, names) for i, line in enumerate(spec["steps"])]
    transitions, first_error = [], None
    for index, (before, after) in enumerate(zip(readings, readings[1:])):
        if index in held or index + 1 in held:
            # Nothing is claimed either way: a person must confirm the line first.
            transitions.append({"from": index, "to": index + 1, "status": "needs_review"})
            continue
        step = {"from": index, "to": index + 1, **_transition(before, after)}
        if step["status"] == "breaks":
            hint = _hint(before, after, names)
            if hint:
                step["hint"] = hint
            if first_error is None:
                first_error = index
        transitions.append(step)
    result = {"unknowns": names, "lines": [{"matrix": _matrix(r.rows), "rhs": _vec(r.rhs)} for r in readings],
              "transitions": transitions, "first_error": first_error, "domain": spec["domain"]}
    if held:
        result["review_lines"] = sorted(held)
    for key, index in (("original_solution", 0), ("final_solution", len(readings) - 1)):
        found = _analyze(readings[index].rows, readings[index].rhs) if index not in held else None
        if found and found["kind"] == "unique":
            result[key] = {"solution": _vec(found["solution"]), "derivations": _matrix(found["derivations"])}
    claim = ("work.error_found" if first_error is not None else
             "work.needs_review" if held else "work.all_steps_valid")
    return claim, result


TASKS = {"solve": _solve, "answer": _answer, "work": _work}


def certificate(spec_sha, task, claim, result):
    cert = {"schema_version": "1", "compiler_version": __version__, "backend_id": BACKEND, "scope": SCOPE,
            "task": task, "input_sha256": spec_sha, "claim": claim, "result": result}
    return {**cert, "certificate_sha256": digest(cert)}


def _hold_unreadable(raw):
    """Work only: a line that cannot be read as an equation goes to review instead of failing the run.

    Only plain misreadings (INVALID_EQUATION) are held. Nonlinear input, division by zero, size limits
    and malformed shapes keep their own distinct statuses and still stop the whole run.
    """
    steps = raw.get("steps") if type(raw) is dict else None
    if type(steps) is not list or not steps or raw.get("task", "work") != "work":
        return raw
    notes = raw.get("provenance")
    notes = [None] * len(steps) if notes is None else notes
    if type(notes) is not list or len(notes) != len(steps):
        return raw  # normalize() reports the shape problem
    steps, notes, changed = list(steps), list(notes), False
    for index, line in enumerate(steps):
        texts = [line] if type(line) is str else line
        if (type(notes[index]) is dict and "review" in notes[index]) or type(texts) is not list or not texts \
                or not all(type(text) is str for text in texts):
            continue
        try:
            for text in texts:
                _Reader(normalize_text(text)).equation()
        except InputError as exc:
            if exc.code != "INVALID_EQUATION":
                continue
            note = dict(notes[index]) if type(notes[index]) is dict else {}
            note.setdefault("raw", " ".join(" ; ".join(texts).split())[:MAX_TEXT])
            note["review"] = {"reason": "UNPARSEABLE", "note": str(exc)[:MAX_NOTE]}
            steps[index], notes[index], changed = [], note, True
    return {**raw, "steps": steps, "provenance": notes} if changed else raw


def run(raw):
    """Compile one task and verify it independently before anything is trusted.

    A certified negative (no solution, wrong answer, broken step) is a
    successful computation. Bad input, unsupported scope, resource limits and
    infrastructure failure remain distinct statuses with no claim.
    """
    from .linear_verifier import verify_bundle

    output = {"schema_version": "1", "status": "ASSUMPTION_REQUIRED", "certificate_verified": False,
              "claim": None, "code": None, "bundle": None}
    try:
        spec, sha = normalize(_hold_unreadable(raw))
        claim, result = TASKS[spec["task"]](spec)
        bundle = {"schema_version": "1", "format": "show-your-work/bundle", "input": spec,
                  "certificate": certificate(sha, spec["task"], claim, result)}
        canonical(bundle)
        report = verify_bundle(bundle)
        if not report["certificate_verified"]:
            output.update(status="INFRASTRUCTURE_ERROR", code="CERTIFICATE_VERIFICATION_FAILED", verification=report)
            return output
        undecided, held = claim.endswith("domain_undecided"), claim == "work.needs_review"
        output.update(status="NEEDS_REVIEW" if held else
                      "MATHEMATICALLY_REJECTED" if claim in NEGATIVE_CLAIMS else "VERIFIED",
                      certificate_verified=True, claim=claim, input_sha256=sha, bundle=bundle,
                      code="DOMAIN_NOT_DECIDED" if undecided else "REVIEW_REQUIRED" if held else None)
        return output
    except InputError as exc:
        status = ("INCOMPLETE_RESOURCE_LIMIT" if exc.code == "RESOURCE_LIMIT" else
                  "UNSUPPORTED" if exc.code.startswith("UNSUPPORTED") else "ASSUMPTION_REQUIRED")
        output.update(status=status, code=exc.code, message=str(exc))
        return output
    except Exception:
        LOG.exception("Linear compiler infrastructure failure")
        output.update(status="INFRASTRUCTURE_ERROR", code="INTERNAL_ERROR")
        return output


# --- Practice problems with independently verified answer keys ------------------------------------

PRACTICE_KINDS = ("one_step", "two_step", "both_sides", "distribute", "system", "tickets")


def _nonzero(rng, low, high):
    value = 0
    while value == 0:
        value = rng.randint(low, high)
    return value


def _term(coefficient, name):
    if coefficient == 1:
        return name
    if coefficient == -1:
        return "-" + name
    return f"{coefficient}{name}"


def _signed(text, value):
    return f"{text} + {value}" if value >= 0 else f"{text} - {-value}"


def practice(kind="two_step", seed=None):
    """Generate a fresh exercise; its answer key is certified by run(), never assumed."""
    require(kind in PRACTICE_KINDS, "UNSUPPORTED_SCOPE", "kind must be one of " + ", ".join(PRACTICE_KINDS))
    if seed is None:
        seed = secrets.randbelow(10 ** 9)
    require(type(seed) is int and 0 <= seed < 10 ** 12, "INVALID_SCHEMA", "seed must be a non-negative integer")
    rng = random.Random(f"{kind}:{seed}")
    x = _nonzero(rng, -9, 12)
    statement, labels, domain = None, None, "rational"
    if kind == "one_step":
        a = _nonzero(rng, 2, 9)
        equations = [f"{_term(a, 'x')} = {a * x}"]
    elif kind == "two_step":
        a, b = rng.choice([-5, -4, -3, -2, 2, 3, 4, 5, 6, 7, 8, 9]), _nonzero(rng, -15, 15)
        equations = [f"{_signed(_term(a, 'x'), b)} = {a * x + b}"]
    elif kind == "both_sides":
        a, c = _nonzero(rng, 2, 9), _nonzero(rng, -5, 7)
        while c == a:
            c = _nonzero(rng, -5, 7)
        b = rng.randint(-12, 12)
        d = a * x + b - c * x
        equations = [f"{_signed(_term(a, 'x'), b)} = {_signed(_term(c, 'x'), d)}"]
    elif kind == "distribute":
        a, b = _nonzero(rng, 2, 7), _nonzero(rng, -8, 8)
        equations = [f"{a}({_signed('x', b)}) = {a * (x + b)}"]
    elif kind == "system":
        y = rng.randint(-8, 10)
        while True:
            a, b, c, d = (_nonzero(rng, -5, 6) for _ in range(4))
            if a * d - b * c:
                break
        equations = [f"{_term(p, 'x')} {'+' if q > 0 else '-'} {_term(abs(q), 'y')} = {p * x + q * y}"
                     for p, q in ((a, b), (c, d))]
    else:
        adults, students = rng.randint(20, 140), rng.randint(20, 160)
        price_a, price_s = rng.randint(8, 16), rng.randint(3, 7)
        total = adults + students
        money = price_a * adults + price_s * students
        equations = [f"a + s = {total}", f"{price_a}a + {price_s}s = {money}"]
        labels = {"a": "adult tickets", "s": "student tickets"}
        domain = "nonnegative_integer"
        statement = (f"The school play sold {total} tickets. Adult tickets cost ${price_a} and student "
                     f"tickets cost ${price_s}. Ticket sales were ${money}. How many of each ticket were sold?")
    raw = {"equations": equations, "domain": domain}
    if labels:
        raw["labels"] = labels
    if statement:
        raw["question"] = statement
    answer_key = run(raw)
    require(answer_key["status"] == "VERIFIED" and answer_key["claim"] == "solve.unique",
            "INTERNAL_ERROR", "Generated practice problem failed certification")
    return {"kind": kind, "seed": seed, "statement": statement or "Solve: " + "; ".join(equations),
            "equations": equations, "answer_key": answer_key}
