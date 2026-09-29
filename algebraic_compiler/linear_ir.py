"""Exact linear-equation input shared by the producer and the verifier.

This module only validates and normalizes what the person (or agent) wrote. It
does not solve anything. It reads a deliberately small language: integers,
exact decimals, named unknowns, + - * / and parentheses. Products of unknowns,
unknowns in a denominator and powers are rejected as UNSUPPORTED_NONLINEAR,
never approximated. Decimals such as 0.25 are read as the exact rational 1/4.
"""

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import re

from .ir import InputError, canonical, fields, require

SCHEMA = "1"
BACKEND = "exact_linear_q_v1"
SCOPE = "linear_q"
DOMAINS = ("rational", "integer", "nonnegative_integer")
MAX_EQUATIONS = 12
MAX_UNKNOWNS = 12
MAX_STEPS = 16
MAX_TEXT = 300
MAX_BITS = 256

_TRANSLATE = str.maketrans({"−": "-", "–": "-", "×": "*", "·": "*", "⋅": "*",
                            "÷": "/", "⁄": "/", " ": " "})
_TOKEN = re.compile(r"\s*(?:(\d+\.\d+|\d+|\.\d+)|([A-Za-z][A-Za-z0-9_]{0,23})|(\S))")
_NUMBER = re.compile(r"^[+-]?(\d+(\.\d+)?|\.\d+)(/\d+)?$")


def normalize_text(text):
    """Canonical display text: mapped symbols and standard spacing (3x+5=20 -> 3x + 5 = 20)."""
    require(type(text) is str, "INVALID_SCHEMA", "Each equation must be a string")
    text = " ".join(text.translate(_TRANSLATE).split())
    require(0 < len(text) <= MAX_TEXT, "INVALID_SCHEMA", f"Equation text must be 1-{MAX_TEXT} characters")
    require(text.count("=") == 1, "INVALID_EQUATION", f"'{text}' must contain exactly one '='")
    tokens(text)  # reject unsupported symbols before re-spacing
    return _respace(text)


def _respace(text):
    """Re-join the same tokens with standard spacing; never merges or splits a token."""
    out, previous, after_divisor = "", None, False  # previous: None, "num", "name", "close", "open", "op"
    for number, name, op in _TOKEN.findall(text):
        if number or name:
            kind = "num" if number else "name"
            if previous in ("num", "name"):
                glue = "" if previous == "num" and kind == "name" and not after_divisor else " "
            else:
                glue = ""
            out += glue + (number or name)
            after_divisor = after_divisor and kind == "num" and previous == "op"
            previous = kind
            continue
        if op in "+-" and previous in ("num", "name", "close"):
            out += f" {op} "
            previous, after_divisor = "op", False
        elif op in "+-":
            out += op
            previous, after_divisor = "open", False
        elif op == "=":
            out += " = "
            previous, after_divisor = "op", False
        elif op == "*":
            out += " * "
            previous, after_divisor = "op", False
        elif op == "/":
            out += "/"
            previous, after_divisor = "op", True
        elif op == "(":
            out += "("
            previous, after_divisor = "open", False
        else:
            out += ")"
            previous, after_divisor = "close", False
    return " ".join(out.split())


def tokens(text):
    """Tokens are (kind, value): num, name or op. Unknown characters are rejected."""
    out = []
    for number, name, op in _TOKEN.findall(text):
        if number:
            require(len(number) <= 30, "RESOURCE_LIMIT", "Number literal too long")
            out.append(("num", Fraction(number)))
        elif name:
            out.append(("name", name))
        elif op in "+-*/()=":
            out.append(("op", op))
        elif op in "^\u00b2\u00b3":
            raise InputError("UNSUPPORTED_NONLINEAR", "Powers such as x^2 are not supported yet; only linear equations can be certified")
        else:
            raise InputError("INVALID_EQUATION", f"Unsupported symbol '{op}'. Use numbers, letters, + - * / ( ) and =")
    return out


def unknowns_in(texts):
    """Unknown names in order of first appearance across all texts."""
    seen = []
    for text in texts:
        for kind, value in tokens(text):
            if kind == "name" and value not in seen:
                seen.append(value)
    return seen


def exact_number(value):
    """Read an exact user-facing number: 5, -3/2, 0.25, or {num, den}."""
    if type(value) is int:
        return Fraction(value)
    if type(value) is dict:
        fields(value, ("num", "den"))
        n, d = value["num"], value["den"]
        require(type(n) is int and type(d) is int and d > 0, "NONCANONICAL_SCALAR", "Use integer num and positive den")
        return Fraction(n, d)
    require(type(value) is str, "NONCANONICAL_SCALAR", "Give exact numbers as strings like '5', '-3/2' or '0.25'")
    text = value.translate(_TRANSLATE).replace(" ", "")
    require(_NUMBER.match(text) is not None, "NONCANONICAL_SCALAR", f"'{value}' is not an exact number")
    numerator, _, denominator = text.partition("/")
    require(not denominator or int(denominator) != 0, "DIVISION_BY_ZERO", "Denominator is zero")
    q = Fraction(numerator) / (Fraction(denominator) if denominator else 1)
    require(max(abs(q.numerator).bit_length(), q.denominator.bit_length()) <= MAX_BITS,
            "RESOURCE_LIMIT", "Number exceeds the exact size limit")
    return q


def display(q):
    """Human display of an exact rational: 5, -12, 25/3."""
    q = Fraction(q)
    text = str(q.numerator) if q.denominator == 1 else f"{q.numerator}/{q.denominator}"
    return text.replace("-", "−")


def exact_json(q):
    q = Fraction(q)
    return {"num": q.numerator, "den": q.denominator}


def read_exact_json(value):
    fields(value, ("num", "den"))
    n, d = value["num"], value["den"]
    require(type(n) is int and type(d) is int and d > 0, "NONCANONICAL_SCALAR", "Invalid exact rational")
    require(max(abs(n).bit_length(), d.bit_length()) <= 8192, "RESOURCE_LIMIT", "Rational exceeds bit limit")
    q = Fraction(n, d)
    require(q.numerator == n and q.denominator == d, "NONCANONICAL_SCALAR", "Rational must be reduced")
    return q


def _labels(value, names):
    require(type(value) is dict and len(value) <= MAX_UNKNOWNS, "INVALID_SCHEMA", "labels must be an object")
    for key, label in value.items():
        require(key in names, "INVALID_SCHEMA", f"Label for unknown '{key}' that does not appear in the equations")
        require(type(label) is str and 0 < len(label) <= 80, "INVALID_SCHEMA", "Labels must be 1-80 characters")
    return {key: " ".join(value[key].split()) for key in sorted(value)}


def _system(value, what="equations"):
    if type(value) is str:
        value = [value]
    require(type(value) is list and 0 < len(value) <= MAX_EQUATIONS, "INVALID_SCHEMA",
            f"{what} must be a list of 1-{MAX_EQUATIONS} equations")
    return [normalize_text(text) for text in value]


def _common(raw, names):
    spec = {"schema_version": SCHEMA}
    domain = raw.get("domain", "rational")
    require(domain in DOMAINS, "UNSUPPORTED_DOMAIN", "domain must be rational, integer or nonnegative_integer")
    spec["domain"] = domain
    if "labels" in raw:
        spec["labels"] = _labels(raw["labels"], names)
    if "question" in raw:
        require(type(raw["question"]) is str and len(raw["question"]) <= 600, "INVALID_SCHEMA",
                "question must be at most 600 characters")
        question = " ".join(raw["question"].split())
        if question:
            spec["question"] = question
    return spec


def normalize_solve(raw):
    fields(raw, ("equations",), ("schema_version", "task", "domain", "labels", "question"))
    require(raw.get("schema_version", SCHEMA) == SCHEMA, "UNSUPPORTED_SCHEMA", "Expected schema 1")
    require(raw.get("task", "solve") == "solve", "INVALID_SCHEMA", "task must be solve")
    equations = _system(raw["equations"])
    names = unknowns_in(equations)
    require(0 < len(names) <= MAX_UNKNOWNS, "INVALID_SCHEMA", f"Use 1-{MAX_UNKNOWNS} unknowns")
    return {**_common(raw, names), "task": "solve", "equations": equations}


def normalize_answer(raw):
    fields(raw, ("equations", "answer"), ("schema_version", "task", "domain", "labels", "question"))
    require(raw.get("schema_version", SCHEMA) == SCHEMA, "UNSUPPORTED_SCHEMA", "Expected schema 1")
    require(raw.get("task", "answer") == "answer", "INVALID_SCHEMA", "task must be answer")
    equations = _system(raw["equations"])
    names = unknowns_in(equations)
    require(0 < len(names) <= MAX_UNKNOWNS, "INVALID_SCHEMA", f"Use 1-{MAX_UNKNOWNS} unknowns")
    answer = raw["answer"]
    require(type(answer) is dict, "INVALID_SCHEMA", "answer must map each unknown to an exact value")
    missing = [n for n in names if n not in answer]
    extra = [k for k in answer if k not in names]
    require(not missing, "ASSUMPTION_REQUIRED", "The answer needs a value for: " + ", ".join(missing))
    require(not extra, "INVALID_SCHEMA", "The answer names unknowns not in the equations: " + ", ".join(extra))
    values = {n: exact_json(exact_number(answer[n])) for n in names}
    return {**_common(raw, names), "task": "answer", "equations": equations, "answer": values}


def normalize_work(raw):
    fields(raw, ("steps",), ("schema_version", "task", "domain", "labels", "question"))
    require(raw.get("schema_version", SCHEMA) == SCHEMA, "UNSUPPORTED_SCHEMA", "Expected schema 1")
    require(raw.get("task", "work") == "work", "INVALID_SCHEMA", "task must be work")
    steps = raw["steps"]
    require(type(steps) is list and 2 <= len(steps) <= MAX_STEPS, "INVALID_SCHEMA",
            f"steps must list 2-{MAX_STEPS} lines of work, starting with the original problem")
    lines = [_system(step, "each step") for step in steps]
    names = unknowns_in([text for line in lines for text in line])
    require(0 < len(names) <= MAX_UNKNOWNS, "INVALID_SCHEMA", f"Use 1-{MAX_UNKNOWNS} unknowns")
    return {**_common(raw, names), "task": "work", "steps": lines}


NORMALIZERS = {"solve": normalize_solve, "answer": normalize_answer, "work": normalize_work}


def normalize(raw):
    """Validate and canonicalize any supported task. Returns (spec, sha256)."""
    require(type(raw) is dict, "INVALID_SCHEMA", "Expected an object")
    task = raw.get("task")
    if task is None:
        task = "work" if "steps" in raw else "answer" if "answer" in raw else "solve"
    require(task in NORMALIZERS, "UNSUPPORTED_SCOPE", "task must be solve, answer or work")
    spec = NORMALIZERS[task]({**raw, "task": task})
    return spec, hashlib.sha256(canonical(spec)).hexdigest()


@dataclass(frozen=True)
class Reading:
    """Typed interpretation of one line: rows @ x = rhs, plus separate sides."""

    rows: tuple
    rhs: tuple
    sides: tuple
