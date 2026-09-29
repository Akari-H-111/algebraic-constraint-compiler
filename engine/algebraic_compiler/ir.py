"""Schema 1: canonical rational JSON and finite-dimensional typed input."""

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
from typing import Optional

SCHEMA = "1"
BACKEND = "general_fd_v1"
PASSES = ("structure", "observability", "weak", "strict", "tangent", "obstruction")
MAX_BYTES = 2_000_000
MAX_BITS = 256


class InputError(ValueError):
    """A structured boundary failure, never a mathematical proof."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise InputError(code, message)


def canonical(value):
    """UTF-8 JSON; reject non-JSON objects, floats and excessive nesting/size."""
    def check(item, depth=0):
        require(depth <= 24, "RESOURCE_LIMIT", "JSON nesting exceeds 24")
        if item is None or type(item) in (str, bool, int):
            if type(item) is int:
                require(item.bit_length() <= 8192, "RESOURCE_LIMIT", "Integer too large")
            return
        if type(item) is list:
            for child in item:
                check(child, depth + 1)
            return
        if type(item) is dict:
            require(all(type(k) is str for k in item), "INVALID_SCHEMA", "JSON keys must be strings")
            for child in item.values():
                check(child, depth + 1)
            return
        raise InputError("NONCANONICAL_SCALAR", "Trusted JSON cannot contain floats or Python objects")

    check(value)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (UnicodeError, ValueError) as exc:
        raise InputError("INVALID_SCHEMA", str(exc)) from exc
    require(len(encoded) <= MAX_BYTES, "RESOURCE_LIMIT", "JSON payload exceeds byte limit")
    return encoded


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def load_json(text):
    """Strict wire decoder; duplicate keys and floating literals are rejected."""
    require(len(text) <= MAX_BYTES, "RESOURCE_LIMIT", "JSON payload exceeds byte limit")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "INVALID_SCHEMA", "Duplicate JSON key")
            result[key] = value
        return result

    def bad_number(_):
        raise InputError("NONCANONICAL_SCALAR", "Use {num,den} exact rationals")

    try:
        result = json.loads(text, object_pairs_hook=pairs, parse_float=bad_number, parse_constant=bad_number)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError("INVALID_SCHEMA", str(exc)) from exc
    canonical(result)
    return result


def fields(value, required, optional=()):
    require(type(value) is dict, "INVALID_SCHEMA", "Expected an object")
    require(set(required) <= set(value) <= set(required) | set(optional),
            "INVALID_SCHEMA", "Missing or unknown object fields")


def rational(value, max_bits=MAX_BITS):
    fields(value, ("num", "den"))
    n, d = value["num"], value["den"]
    require(type(n) is int and type(d) is int, "NONCANONICAL_SCALAR", "Rational components must be integers")
    require(d > 0 and math.gcd(n, d) == 1, "NONCANONICAL_SCALAR", "Rational must be reduced with positive denominator")
    require(max(abs(n).bit_length(), d.bit_length()) <= max_bits, "RESOURCE_LIMIT", "Rational exceeds bit limit")
    return Fraction(n, d)


def scalar(value):
    require(type(value) in (int, Fraction), "NONCANONICAL_SCALAR", "Exact scalar required")
    q = Fraction(value)
    return {"num": q.numerator, "den": q.denominator}


def vector(values):
    return [scalar(v) for v in values]


def read_vector(values, size):
    require(type(values) is list and len(values) == size, "INVALID_SCHEMA", "Vector dimension mismatch")
    return [rational(v, 8192) for v in values]


def matrix_json(rows, cols):
    """Canonical sparse COO matrix; vectors use ordered dense rational arrays."""
    return {"rows": len(rows), "cols": cols,
            "entries": [{"row": i, "col": j, "value": scalar(v)}
                        for i, row in enumerate(rows) for j, v in enumerate(row) if v]}


def named_basis(value):
    require(type(value) is list and len(value) > 0, "INVALID_DIMENSION", "Basis must be nonempty")
    require(all(type(x) is str and 0 < len(x) <= 128 for x in value), "INVALID_SCHEMA", "Invalid basis label")
    require(len(set(value)) == len(value), "INVALID_SCHEMA", "Repeated basis labels")
    return tuple(value)


def entries(value, axes, shape):
    require(type(value) is list, "INVALID_SCHEMA", "Sparse tensor entries must be a list")
    require(len(value) <= 4096, "RESOURCE_LIMIT", "Too many tensor entries")
    merged = {}
    for entry in value:
        fields(entry, (*axes, "value"))
        key = tuple(entry[a] for a in axes)
        require(all(type(i) is int and 0 <= i < n for i, n in zip(key, shape)),
                "INVALID_DIMENSION", "Tensor index out of range")
        merged[key] = merged.get(key, Fraction(0)) + rational(entry["value"])
    return [{**dict(zip(axes, key)), "value": scalar(q)} for key, q in sorted(merged.items()) if q]


@dataclass(frozen=True)
class ReconstructionProblem:
    """Immutable typed IR. Structure validation is a distinct compiler pass."""

    spec_json: bytes
    algebra_basis: tuple
    space_basis: tuple
    unit: tuple
    multiplication: tuple
    bilinear: tuple
    strict_point: Optional[tuple]
    tangent_direction: Optional[tuple]

    @property
    def m(self):
        return len(self.algebra_basis)

    @property
    def d(self):
        return len(self.space_basis)

    @property
    def sha256(self):
        return hashlib.sha256(self.spec_json).hexdigest()

    def to_json(self):
        return load_json(self.spec_json)

    def variable_index(self, side, algebra_index, row, col):
        return (side * self.m + algebra_index) * self.d * self.d + row * self.d + col


def parse_problem(raw):
    """Validate the schema and normalize sparse order/duplicates, not algebra laws."""
    canonical(raw)
    fields(raw, ("schema_version", "problem_id", "mode", "algebra", "space", "B", "requested_passes"),
           ("scope", "backend_id", "strict_point", "tangent_direction"))
    require(raw["schema_version"] == SCHEMA, "UNSUPPORTED_SCHEMA", "Expected schema 1")
    require(raw["mode"] == "fixed_A_V_B" and raw.get("scope", "general_fd") == "general_fd",
            "UNSUPPORTED_SCOPE", "Only fixed A,V,B general finite-dimensional reconstruction is supported")
    require(raw.get("backend_id", BACKEND) == BACKEND, "BACKEND_SCOPE_MISMATCH", "No fixed backend is installed")
    requested = raw["requested_passes"]
    require(type(requested) is list and any(requested == list(PASSES[:n]) for n in range(1, len(PASSES) + 1)),
            "UNSUPPORTED_SCOPE", "Requested passes must be a prefix of structure, observability, weak, strict, tangent, obstruction")
    a, v, b = raw["algebra"], raw["space"], raw["B"]
    fields(a, ("schema_version", "id", "field", "basis", "unit", "multiplication"))
    fields(v, ("schema_version", "id", "field", "basis"))
    fields(b, ("schema_version", "id", "left_algebra_id", "right_algebra_id", "codomain_space_id", "entries"))
    for obj in (a, v, b):
        require(obj["schema_version"] == SCHEMA, "UNSUPPORTED_SCHEMA", "Nested schema must be 1")
    for name in (raw["problem_id"], a["id"], v["id"], b["id"]):
        require(type(name) is str and 0 < len(name) <= 128, "INVALID_SCHEMA", "Invalid identifier")
    for field in (a["field"], v["field"]):
        fields(field, ("kind", "characteristic"))
        require(field["kind"] == "Q" and type(field["characteristic"]) is int and field["characteristic"] == 0,
                "UNSUPPORTED_FIELD", "Only exact Q is supported")
    require(b["left_algebra_id"] == b["right_algebra_id"] == a["id"] and b["codomain_space_id"] == v["id"],
            "INVALID_SCHEMA", "B references the wrong algebra or space")
    ab, vb = named_basis(a["basis"]), named_basis(v["basis"])
    m, d = len(ab), len(vb)
    require(m <= 4 and d <= 3, "RESOURCE_LIMIT", "Initial runtime limit: dim A <= 4, dim V <= 3")
    fields(a["unit"], ("coordinates",))
    require(type(a["unit"]["coordinates"]) is list and len(a["unit"]["coordinates"]) == m,
            "INVALID_DIMENSION", "Unit coordinates have wrong dimension")
    unit = tuple(rational(q) for q in a["unit"]["coordinates"])
    c_entries = entries(a["multiplication"], ("i", "j", "k"), (m, m, m))
    b_entries = entries(b["entries"], ("i", "j", "beta"), (m, m, d))
    # Ensure sums of duplicate entries still satisfy the input bit policy.
    for e in c_entries + b_entries:
        rational(e["value"])
    c_lookup = {(e["i"], e["j"], e["k"]): rational(e["value"]) for e in c_entries}
    b_lookup = {(e["i"], e["j"], e["beta"]): rational(e["value"]) for e in b_entries}
    c = tuple(tuple(tuple(c_lookup.get((i, j, k), Fraction(0)) for k in range(m)) for j in range(m)) for i in range(m))
    bt = tuple(tuple(tuple(b_lookup.get((i, j, k), Fraction(0)) for k in range(d)) for j in range(m)) for i in range(m))
    strict_requested = "strict" in requested
    strict_supplied = "strict_point" in raw
    require(strict_requested == strict_supplied, "INVALID_SCHEMA",
            "strict_point is required exactly when requested_passes includes strict")
    strict_point = None
    if strict_supplied:
        point = raw["strict_point"]
        fields(point, ("schema_version", "coordinate_system", "coordinates"))
        require(point["schema_version"] == SCHEMA, "UNSUPPORTED_SCHEMA", "strict_point.schema_version must be 1")
        require(point["coordinate_system"] == "action_entries", "INVALID_SCHEMA",
                "strict_point.coordinate_system must be action_entries")
        strict_point = tuple(read_vector(point["coordinates"], 2 * m * d * d))
    direction_supplied = "tangent_direction" in raw
    require(("obstruction" in requested) == direction_supplied, "INVALID_SCHEMA",
            "tangent_direction is required exactly when requested_passes includes obstruction")
    tangent_direction = None
    if direction_supplied:
        direction = raw["tangent_direction"]
        fields(direction, ("schema_version", "coordinate_system", "coordinates"))
        require(direction["schema_version"] == SCHEMA, "UNSUPPORTED_SCHEMA", "tangent_direction.schema_version must be 1")
        require(direction["coordinate_system"] == "action_entries", "INVALID_SCHEMA",
                "tangent_direction.coordinate_system must be action_entries")
        tangent_direction = tuple(read_vector(direction["coordinates"], 2 * m * d * d))
    normalized = {**raw, "scope": "general_fd", "backend_id": BACKEND,
                  "algebra": {**a, "multiplication": c_entries}, "B": {**b, "entries": b_entries}}
    if strict_point is not None:
        normalized["strict_point"] = {**raw["strict_point"], "coordinates": vector(strict_point)}
    if tangent_direction is not None:
        normalized["tangent_direction"] = {**raw["tangent_direction"], "coordinates": vector(tangent_direction)}
    return ReconstructionProblem(canonical(normalized), ab, vb, unit, c, bt, strict_point, tangent_direction)


def problem_schema():
    """Discovery schema; parse_problem additionally enforces exactness and typing."""
    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}

    label = {"type": "string", "minLength": 1, "maxLength": 128}
    version = {"const": "1"}
    q = obj({"num": {"type": "integer"}, "den": {"type": "integer", "minimum": 1}})
    q["description"] = "Reduced exact rational; gcd(abs(num),den)=1. No floats or booleans."
    field = obj({"kind": {"const": "Q"}, "characteristic": {"const": 0}})
    basis = {"type": "array", "items": label, "minItems": 1, "uniqueItems": True}

    def tensor(axes):
        return {"type": "array", "maxItems": 4096, "items": obj({
            **{axis: {"type": "integer", "minimum": 0} for axis in axes}, "value": q})}

    schema = obj({
        "schema_version": version, "problem_id": label, "mode": {"const": "fixed_A_V_B"},
        "scope": {"const": "general_fd"}, "backend_id": {"const": BACKEND},
        "requested_passes": {"enum": [list(PASSES[:n]) for n in range(1, len(PASSES) + 1)]},
        "algebra": obj({"schema_version": version, "id": label, "field": field,
                        "basis": {**basis, "maxItems": 4},
                        "unit": obj({"coordinates": {"type": "array", "items": q, "minItems": 1, "maxItems": 4}}),
                        "multiplication": tensor(("i", "j", "k"))}),
        "space": obj({"schema_version": version, "id": label, "field": field, "basis": {**basis, "maxItems": 3}}),
        "B": obj({"schema_version": version, "id": label, "left_algebra_id": label,
                  "right_algebra_id": label, "codomain_space_id": label, "entries": tensor(("i", "j", "beta"))}),
        "strict_point": obj({"schema_version": version, "coordinate_system": {"const": "action_entries"},
                             "coordinates": {"type": "array", "items": q, "minItems": 1}}),
    })
    schema["properties"]["tangent_direction"] = schema["properties"]["strict_point"]
    schema["required"] = [key for key in schema["required"] if key not in ("scope", "backend_id", "strict_point", "tangent_direction")]
    return schema
