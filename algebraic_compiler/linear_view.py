"""Plain-language explanations built only from independently verified results.

Nothing here decides mathematics. It formats witnesses that the verifier has
already accepted: which combination isolates an unknown, which point breaks a
step, which values were substituted. Hints about the *kind* of slip are
rule-based guesses and are labelled as not certified.
"""

from fractions import Fraction
from math import gcd

from .linear_ir import display, read_exact_json, tokens, unknowns_in

MINUS = "−"


def _q(value):
    return read_exact_json(value)


def _lcm(a, b):
    return a * b // gcd(a, b)


def _integer_combination(weights):
    """Scale rational weights to coprime integers w with weights = w / k (k > 0)."""
    denominator = 1
    for q in weights:
        denominator = _lcm(denominator, q.denominator)
    scaled = [int(q * denominator) for q in weights]
    common = 0
    for w in scaled:
        common = gcd(common, abs(w))
    common = common or 1
    return [w // common for w in scaled], Fraction(denominator, common)


def _combination_text(integers):
    parts = []
    for index, w in sorted(enumerate(integers), key=lambda item: item[1] < 0):
        if not w:
            continue
        magnitude = "" if abs(w) == 1 else f"{abs(w)}·"
        term = f"{magnitude}({index + 1})"
        if not parts:
            parts.append(term if w > 0 else MINUS + term)
        else:
            parts.append(("+ " if w > 0 else MINUS + " ") + term)
    return " ".join(parts) or "0"


def _linear_text(coefficients, names, constant=None):
    parts = []
    if constant is not None and constant:
        parts.append((MINUS if constant < 0 else "+", display(abs(constant))))
    for c, n in zip(coefficients, names):
        if not c:
            continue
        magnitude = "" if abs(c) == 1 else display(abs(c)) + ("\u00b7" if abs(c).denominator != 1 else "")
        sign = MINUS if c < 0 else "+"
        parts.append((sign, magnitude + n))
    if not parts:
        return "0"
    first_sign, first = parts[0]
    text = (MINUS if first_sign == MINUS else "") + first
    for sign, body in parts[1:]:
        text += f" {sign} {body}"
    return text


def substituted(equation, names, values):
    """Show an equation with exact values plugged in, e.g. 3·5 + 5 = 20."""
    point = dict(zip(names, values))
    text, previous = "", None  # previous: None, "value", "open", "op", "op_unary"
    for kind, item in tokens(equation):
        if kind in ("name", "num"):
            q = point[item] if kind == "name" else item
            shown = _literal(q)
            if kind == "name" and (q < 0 or q.denominator != 1):
                shown = f"({shown})"
            text += ("·" if previous == "value" else "") + shown
            previous = "value"
        elif item == "(":
            text += ("·" if previous == "value" else "") + "("
            previous = "open"
        elif item == ")":
            text += ")"
            previous = "value"
        elif item in "+-" and previous in (None, "open", "op"):
            text += MINUS if item == "-" else "+"
            previous = "op_unary"
        elif item in "*/":
            text += "·" if item == "*" else "/"
            previous = "op"
        else:
            text += f" {MINUS if item == '-' else item} "
            previous = "op"
    return text


def _literal(q):
    """Exact display that keeps finite decimals readable: 12.5 rather than 25/2."""
    q = Fraction(q)
    d = q.denominator
    while d % 2 == 0:
        d //= 2
    while d % 5 == 0:
        d //= 5
    if q.denominator == 1 or d != 1:
        return display(q)
    places = 0
    while (q * 10 ** places).denominator != 1:
        places += 1
    digits = str(abs(q.numerator * 10 ** places // q.denominator)).rjust(places + 1, "0")
    return (MINUS if q < 0 else "") + digits[:-places] + "." + digits[-places:]


def _named(names, labels):
    return [f"{n} ({labels[n]})" if n in labels else n for n in names]


def _solution_text(names, solution, labels=None):
    labels = labels or {}
    return ", ".join(f"{n} = {display(q)}" + (f" {labels[n]}" if n in labels else "") for n, q in zip(names, solution))


def _derivation_lines(names, matrix, rhs, derivations, solution):
    if len(names) == 1 and len(matrix) == 1:
        # One equation, one unknown: the verified row is a*x = b; say it like a teacher would.
        a, b, x = matrix[0][0], rhs[0], names[0]
        lines = [f"Collect {x} on one side and numbers on the other: {_linear_text([a], names)} = {display(b)}"]
        if a != 1:
            lines.append(f"Divide both sides by {display(a)}: {x} = {display(solution[0])}")
        return lines
    lines = []
    for j, weights in enumerate(derivations):
        integers, k = _integer_combination([_q(w) for w in weights])
        combined_rhs = sum((w * b for w, b in zip(integers, rhs)), Fraction(0))
        if len(matrix) == 1 and k == 1 and integers == [1]:
            lines.append(f"(1) simplifies to {names[j]} = {display(solution[j])}")
            continue
        coefficient = display(k) if k != 1 else ""
        lines.append(f"{_combination_text(integers)} gives {coefficient}{names[j]} = {display(combined_rhs)}"
                     + (f", so {names[j]} = {display(solution[j])}" if k != 1 else ""))
    return lines


def _translation(spec):
    equations = spec.get("equations") or [e for line in spec["steps"] for e in line]
    names = unknowns_in(equations)
    labels = spec.get("labels", {})
    domain = {"rational": "any exact number", "integer": "whole numbers (may be negative)",
              "nonnegative_integer": "whole numbers, 0 or more"}[spec["domain"]]
    return {"equations": spec.get("equations"), "steps": spec.get("steps"),
            "unknowns": _named(names, labels), "domain": domain, "question": spec.get("question")}


def explain(run_output, replay=None):
    """Return a view for a verified run, or a clear no-claim view otherwise."""
    status = run_output.get("status")
    if not run_output.get("certificate_verified"):
        reason = run_output.get("message") or run_output.get("code") or status
        titles = {"UNSUPPORTED": "That kind of problem is outside what I can certify yet.",
                  "ASSUMPTION_REQUIRED": "I need a clearer version of the problem.",
                  "INCOMPLETE_RESOURCE_LIMIT": "That problem is too large to certify here.",
                  "INFRASTRUCTURE_ERROR": "Verification is unavailable right now."}
        return {"tone": "neutral", "verdict": "no_claim", "title": titles.get(status, "No certified result."),
                "spoken": titles.get(status, "I couldn't certify that.") + " Nothing was verified, so I won't guess.",
                "detail": reason, "steps": [], "facts": [], "translation": None}
    bundle = run_output["bundle"]
    spec, cert = bundle["input"], bundle["certificate"]
    claim, result = cert["claim"], cert["result"]
    labels = spec.get("labels", {})
    view = {"translation": _translation(spec), "steps": [], "facts": [], "hint": None, "reveal": None}
    checks = len(replay["checks"]) if replay else None
    names = result["unknowns"]

    if cert["task"] == "solve":
        matrix = [[_q(x) for x in row] for row in result["matrix"]]
        rhs = [_q(x) for x in result["rhs"]]
        if claim in ("solve.unique", "solve.no_solution_in_domain"):
            solution = [_q(x) for x in result["solution"]]
            view["steps"] = _derivation_lines(names, matrix, rhs, result["derivations"], solution)
            view["steps"] += [f"Check ({i + 1}): {substituted(t, names, solution)}" for i, t in enumerate(spec["equations"])]
            if claim == "solve.unique":
                view.update(tone="success", verdict="unique_solution", title=_solution_text(names, solution, labels),
                            spoken=f"The answer is {_spoken_solution(names, solution, labels)}. "
                                   "I checked it independently, and it's the only solution.")
            else:
                bad = result["domain_violation"]
                who = names[bad["unknown"]]
                why = "isn't a whole number" if bad["reason"] == "not_integer" else "is negative"
                label = f" {labels[who]}" if who in labels else ""
                view.update(tone="warning", verdict="no_valid_answer",
                            title="No valid answer: this problem can't happen as stated.",
                            spoken=f"This problem has no valid answer. The only exact solution gives "
                                   f"{display(solution[bad['unknown']])}{label}, which {why}. "
                                   "The numbers in the problem may have a typo.")
                must = "a whole number" if spec["domain"] == "integer" else "a whole number, 0 or more"
                view["steps"].append(f"{who} = {display(solution[bad['unknown']])} {why}, but {who} must be "
                                     f"{must}. So no valid answer exists.")
        elif claim == "solve.no_solution":
            integers, k = _integer_combination([_q(w) for w in result["combination"]])
            constant = sum((w * b for w, b in zip(integers, rhs)), Fraction(0))
            view.update(tone="warning", verdict="no_solution", title="No solution: the equations contradict each other.",
                        spoken="These equations can't all be true at once. Combining them cancels every "
                               f"unknown and leaves 0 = {display(constant)}, which is impossible.")
            view["steps"] = [f"{_combination_text(integers)}: every unknown cancels, leaving 0 = {display(constant)}.",
                             "0 can never equal a nonzero number, so no values satisfy all equations."]
        else:
            particular = [_q(x) for x in result["solution"]]
            kernel = [[_q(x) for x in k] for k in result["kernel"]]
            free = result["free_unknowns"]
            free_names = [names[f] for f in free]
            lines = []
            for p in result["pivot_unknowns"]:
                coefficients = [Fraction(0)] * len(names)
                for f, k in zip(free, kernel):
                    coefficients[f] = k[p]
                lines.append(f"{names[p]} = {_linear_text(coefficients, names, particular[p])}")
            view["steps"] = [f"{', '.join(free_names)} can be any number."] + lines
            view["steps"].append(f"Example: {_solution_text(names, particular)} (checked in every equation).")
            undecided = claim == "solve.family_domain_undecided"
            view.update(tone="neutral", verdict="infinitely_many",
                        title="Infinitely many solutions." + (" Whole-number answers not decided." if undecided else ""),
                        spoken="There isn't just one answer: " + ", ".join(free_names) +
                               (" can be any number, and the others follow from it." if len(free_names) == 1
                                else " can be any numbers, and the others follow.") +
                               (" I can't yet certify which of those are whole numbers." if undecided else "")
                               + " Is some information missing from the problem?")
    elif cert["task"] == "answer":
        values = [_q(x) for x in result["assignment"]]
        sides = [[_q(a), _q(b)] for a, b in result["sides"]]
        view["steps"] = [f"({i + 1}) {substituted(t, names, values)}: left {display(l)}, right {display(r)} "
                         + ("✓" if l == r else "✗") for i, (t, (l, r)) in enumerate(zip(spec["equations"], sides))]
        unique = result.get("unique_solution")
        if claim == "answer.correct":
            view.update(tone="success", verdict="correct", title=f"Correct: {_solution_text(names, values, labels)}",
                        spoken=f"Yes, {_spoken_solution(names, values, labels)} is correct"
                               + (", and it's the only solution." if unique else ". It satisfies every equation."))
        else:
            if result["failing_equations"]:
                i = result["failing_equations"][0]
                l, r = sides[i]
                spoken = (f"Not quite. With {_spoken_solution(names, values, labels)}, equation {i + 1} gives "
                          f"{display(l)} on the left but {display(r)} on the right.")
            else:
                bad = result["domain_violation"]
                spoken = f"Those values work in the equations, but {names[bad['unknown']]} has to be a whole number."
            view.update(tone="warning", verdict="incorrect", title="Not quite. One check fails.", spoken=spoken)
            if unique:
                solution = [_q(x) for x in unique["solution"]]
                view["reveal"] = f"Verified answer: {_solution_text(names, solution, labels)}"
    else:
        steps = spec["steps"]
        transitions = result["transitions"]
        first = result["first_error"]
        for t in transitions:
            before, after = " ; ".join(steps[t["from"]]), " ; ".join(steps[t["to"]])
            if t["status"] == "sound":
                view["steps"].append(f"✓ Line {t['from'] + 1} → {t['to'] + 1}: {after} follows from {before}")
            elif t["status"] == "vacuous":
                view["steps"].append(f"• Line {t['from'] + 1} already has no solution, so anything follows from it")
            else:
                point = [_q(x) for x in t["point"]]
                e = t["failing_equation"]
                view["steps"].append(f"✗ Line {t['from'] + 1} → {t['to'] + 1}: with {_solution_text(names, point)}, "
                                     f"{before} holds but {substituted(steps[t['to']][e], names, point)} does not")
        if first is None:
            final = result.get("final_solution")
            original = result.get("original_solution")
            same = final and original and final["solution"] == original["solution"]
            ending = (f" and the last line, {_solution_text(names, [_q(x) for x in final['solution']])}, is the verified answer"
                      if same else "")
            view.update(tone="success", verdict="all_steps_valid", title="Every step checks out.",
                        spoken="Great work. Every step follows from the one before" + ending + ".")
        else:
            t = transitions[first]
            point = [_q(x) for x in t["point"]]
            e = t["failing_equation"]
            wrong = steps[t["to"]][e]
            view.update(tone="warning", verdict="error_found",
                        title=f"Line {t['from'] + 1} → {t['to'] + 1} changes the answer.",
                        spoken=f"Almost! The mistake is going from line {t['from'] + 1} to line {t['to'] + 1}. "
                               f"{_solution_text(names, point)} works before that step, but in {wrong} it gives "
                               f"{_side_values(steps[t['to']][e], names, point)}.")
            view["hint"] = _hint_text(t.get("hint"))
            original = result.get("original_solution")
            if original:
                view["reveal"] = f"Verified answer: {_solution_text(names, [_q(x) for x in original['solution']], labels)}"
    view["facts"] = [{"label": "Claim", "value": claim},
                     {"label": "Independent checks", "value": f"{checks} passed" if checks is not None else "replayed"},
                     {"label": "Input SHA-256", "value": cert["input_sha256"][:16] + "…"},
                     {"label": "Certificate", "value": cert["certificate_sha256"][:16] + "…"}]
    return view


def _side_values(text, names, point):
    from .linear_verifier import _Evaluate  # evaluation only; the verdict is already certified
    left, right = _Evaluate(text, dict(zip(names, point))).sides()
    return f"{display(left)} on the left and {display(right)} on the right"


def _spoken_solution(names, values, labels):
    parts = []
    for n, q in zip(names, values):
        parts.append(f"{display(q)} {labels[n]}" if n in labels else f"{n} = {display(q)}")
    return " and ".join(parts)


def _hint_text(hint):
    if not hint:
        return None
    if hint["code"] == "SIGN_WHEN_MOVING":
        term = _q(hint["term"])
        return (f"Hint (a guess, not certified): when {display(term)} moves to the other side, "
                f"it should become {display(-term)}.")
    if hint["code"] == "ONE_SIDE_ONLY":
        return ("Hint (a guess, not certified): it looks like only one side was multiplied by "
                f"{display(_q(hint['factor']))}. Do the same thing to both sides.")
    return "Hint (a guess, not certified): re-check the arithmetic in this step."
