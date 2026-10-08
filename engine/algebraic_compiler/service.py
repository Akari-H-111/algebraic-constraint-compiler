"""Transport-neutral facade used by the MCP server, the simulated Alexa+ host and tests.

Handlers stay thin: every mathematical statement comes from the exact producer
and is replayed by the independent verifier before it is explained or saved.
"""

import copy
import json

from .ir import InputError, digest
from . import linear, notebook as notebooks
from .linear_verifier import verify_bundle
from .linear_view import explain

EXACT_RULES = ("Only say what this result states. If certificate_verified is false, say nothing was verified "
               "and do not supply an answer of your own. For homework, prefer hints before revealing answers.")


def _payload(run, notebook=None, learner=None, kind="result"):
    replay = verify_bundle(run["bundle"]) if run.get("bundle") else None
    view = explain(run, replay)
    payload = {"kind": kind, "status": run["status"], "claim": run.get("claim"), "code": run.get("code"),
               "certificate_verified": bool(run.get("certificate_verified") and replay and replay["certificate_verified"]),
               "verdict": view["verdict"], "view": view, "bundle": run.get("bundle"),
               "replay": _replay_summary(replay), "saved": None}
    if run.get("message"):
        payload["message"] = run["message"]
    if notebook and payload["certificate_verified"] and view["verdict"] != "needs_review":
        hint = None
        for step in run["bundle"]["certificate"]["result"].get("transitions", []):
            if step.get("hint"):
                hint = step["hint"]["code"]
                break
        payload["saved"] = notebooks.shared().save(notebook, run["bundle"], view["verdict"], view["title"],
                                                   learner or "", hint)
    return payload


def _replay_summary(report):
    if report is None:
        return None
    return {"certificate_verified": report["certificate_verified"], "status": report["status"],
            "code": report["code"], "checks_passed": sum(1 for c in report["checks"] if c["ok"]),
            "checks_total": len(report["checks"]), "message": report.get("message")}


def _guarded(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except InputError as exc:
            view = {"tone": "neutral", "verdict": "no_claim", "title": "No certified result.",
                    "spoken": "I couldn't do that: " + str(exc), "steps": [], "facts": [], "translation": None}
            return {"kind": "error", "status": "ASSUMPTION_REQUIRED", "code": exc.code, "message": str(exc),
                    "certificate_verified": False, "verdict": "no_claim", "view": view, "bundle": None}
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _spec(task, body, domain, labels, question):
    raw = {"task": task, **body, "domain": domain or "rational"}
    if labels:
        raw["labels"] = labels
    if question:
        raw["question"] = question
    return raw


@_guarded
def solve(equations, domain="rational", labels=None, question=None, notebook=None, learner=None):
    return _payload(linear.run(_spec("solve", {"equations": equations}, domain, labels, question)), notebook, learner)


@_guarded
def check_answer(equations, answer, domain="rational", labels=None, question=None, notebook=None, learner=None):
    return _payload(linear.run(_spec("answer", {"equations": equations, "answer": answer}, domain, labels, question)),
                    notebook, learner)


@_guarded
def check_work(steps, domain="rational", labels=None, question=None, notebook=None, learner=None, provenance=None):
    body = {"steps": steps, **({"provenance": provenance} if provenance is not None else {})}
    return _payload(linear.run(_spec("work", body, domain, labels, question)), notebook, learner)


@_guarded
def practice(kind="two_step", seed=None, notebook=None, learner=None):
    generated = linear.practice(kind, seed)
    payload = _payload(generated["answer_key"], kind="practice")
    view = payload["view"]
    payload["practice"] = {"kind": kind, "seed": generated["seed"], "statement": generated["statement"],
                           "equations": generated["equations"]}
    checks = [line for line in view["steps"] if line.startswith("Check")]
    view["reveal"] = "Answer key: " + view["title"] + (" \u00b7 " + "; ".join(checks) if checks else "")
    view["steps"] = []  # the proof would give the answer away; it stays in the certificate
    view.update(title=generated["statement"], verdict="practice", tone="neutral",
                spoken="Here's one to try. " + generated["statement"] + " Tell me your answer when you're ready.")
    payload["verdict"] = "practice"
    if notebook and payload["certificate_verified"]:
        payload["saved"] = notebooks.shared().save(notebook, payload["bundle"], "practice",
                                                   generated["statement"], learner or "")
    return payload


@_guarded
def verify(bundle):
    """Replay a portable bundle: Show Your Work bundles or research compilation runs."""
    if isinstance(bundle, dict) and bundle.get("format") == "show-your-work/bundle":
        report = verify_bundle(bundle)
        run = {"status": "VERIFIED" if report["certificate_verified"] else "REJECTED", "claim": report["claim"],
               "certificate_verified": report["certificate_verified"], "bundle": bundle if report["certificate_verified"] else None,
               "code": report["code"], "message": report.get("message")}
        payload = _payload(run, kind="replay") if report["certificate_verified"] else {
            "kind": "replay", "status": "REJECTED", "claim": None, "code": report["code"],
            "message": report.get("message"), "certificate_verified": False, "verdict": "rejected", "bundle": None,
            "replay": _replay_summary(report),
            "view": {"tone": "danger", "verdict": "rejected", "title": "Replay rejected: not a valid certificate.",
                     "spoken": "That certificate did not survive independent replay, so it proves nothing.",
                     "steps": [report.get("message") or report["code"]], "facts": [], "translation": None}}
        return payload
    from .verifier import verify_run
    report = verify_run(bundle)
    ok = report["certificate_verified"]
    return {"kind": "replay", "status": report["status"], "claim": report.get("mathematical_status"),
            "code": report.get("code"), "certificate_verified": ok, "verdict": "verified" if ok else "rejected",
            "bundle": None, "replay": {"certificate_verified": ok, "status": report["status"], "code": report.get("code"),
                                       "checks_passed": len(report.get("reports", [])), "checks_total": len(report.get("reports", []))},
            "view": {"tone": "success" if ok else "danger", "verdict": "verified" if ok else "rejected",
                     "title": ("Research certificate replayed: " + str(report.get("mathematical_status"))) if ok
                     else "Research certificate rejected.",
                     "spoken": "The research certificate replayed independently." if ok else
                     "That research certificate was rejected by the independent verifier.",
                     "steps": [], "facts": [], "translation": None}}


WITNESS_KEYS = ("solution", "combination", "point", "derivations", "combinations", "kernel", "sides", "rank_witness")


def forge(bundle):
    """Change one witness number, recompute the certificate hash, and replay. It must fail."""
    forged = copy.deepcopy(bundle)
    cert = forged["certificate"]

    def bump(node):
        if isinstance(node, dict) and set(node) == {"num", "den"}:
            node["num"] += node["den"]
            return True
        if isinstance(node, dict):
            return any(bump(node[key]) for key in node)
        if isinstance(node, list):
            return any(bump(item) for item in node)
        return False

    def first_witness(result):
        for key in WITNESS_KEYS:
            if key in result and bump(result[key]):
                return key
        for key in ("transitions", "unique_solution", "original_solution", "lines"):
            if key in result and bump(result[key]):
                return key
        return None

    changed = first_witness(cert["result"])
    body = {k: v for k, v in cert.items() if k != "certificate_sha256"}
    cert["certificate_sha256"] = digest(body)
    report = verify_bundle(forged)
    return {"kind": "forgery", "changed_field": changed, "rehashed": True,
            "certificate_verified": report["certificate_verified"], "replay": _replay_summary(report),
            "verdict": "forgery_accepted" if report["certificate_verified"] else "forgery_rejected",
            "view": {"tone": "danger" if report["certificate_verified"] else "success",
                     "verdict": "forgery_rejected" if not report["certificate_verified"] else "forgery_accepted",
                     "title": "Forgery rejected: a fresh hash can't make a false witness true."
                     if not report["certificate_verified"] else "UNEXPECTED: forgery accepted",
                     "spoken": "I changed one number in the proof and recomputed its fingerprint. "
                               "The independent verifier still rejected it.",
                     "steps": [f"Changed one exact number in '{changed}' and recomputed certificate_sha256.",
                               f"Verifier result: {report['status']} ({report.get('message') or report['code']})"],
                     "facts": [], "translation": None}}


@_guarded
def history(notebook, learner=None, limit=10):
    entries = notebooks.shared().history(notebook, learner, limit)
    return {"kind": "history", "status": "OK", "certificate_verified": False, "verdict": "history",
            "entries": entries, "view": {"tone": "neutral", "verdict": "history", "title": f"Notebook: {len(entries)} recent entries",
                                         "spoken": f"I found {len(entries)} saved results.", "steps":
                                         [f"{e['saved_at'][:10]} · {e['learner'] or 'family'} · {e['title']}" for e in entries],
                                         "facts": [], "translation": None}}


@_guarded
def progress(notebook, learner=None, days=7):
    report = notebooks.shared().progress(notebook, learner, days)
    verdicts = report["verdicts"]
    hints = report["hint_patterns"]
    who = report["learner"].title() if report["learner"] else "your family"
    right = verdicts.get("all_steps_valid", 0) + verdicts.get("correct", 0)
    errors = verdicts.get("error_found", 0) + verdicts.get("incorrect", 0)
    solved = sum(verdicts.get(k, 0) for k in ("unique_solution", "no_solution", "no_valid_answer", "infinitely_many"))
    assigned = verdicts.get("practice", 0)
    pattern = max(hints, key=hints.get) if hints else None
    pattern_text = {"SIGN_WHEN_MOVING": "keeping the sign right when moving a term across the equals sign",
                    "ONE_SIDE_ONLY": "doing the same operation to both sides",
                    "ARITHMETIC_SLIP": "arithmetic within a step"}.get(pattern)
    plural = lambda n, word: f"{n} {word}" + ("" if n == 1 else "s")
    parts = []
    if right + errors:
        parts.append(f"{who}'s work was checked {plural(right + errors, 'time')}: {right} right and "
                     f"{plural(errors, 'mistake')} pinpointed exactly")
    if solved:
        parts.append(f"{plural(solved, 'problem')} solved with proofs")
    if assigned:
        parts.append(f"{plural(assigned, 'practice problem')} with verified answer keys")
    spoken = f"In the last {days} days: " + ("; ".join(parts) if parts else "nothing saved yet") + "."
    if pattern_text:
        spoken += f" The most common slip was {pattern_text}. Want a practice problem on that?"
    spoken += f" I re-verified all {report['replayed_and_verified']} saved certificates just now."
    if report["failed_replay"]:
        spoken += f" {len(report['failed_replay'])} stored entries failed replay and were not counted."
    steps = [f"{e['saved_at'][:10]} · {e['title']}" for e in report["latest"]]
    return {"kind": "progress", "status": "OK", "certificate_verified": not report["failed_replay"],
            "verdict": "progress", "progress": report,
            "view": {"tone": "neutral", "verdict": "progress", "title": f"{who.title()}: last {days} days",
                     "spoken": spoken, "steps": steps, "hint": None,
                     "facts": [{"label": "Work checked", "value": str(right + errors)},
                               {"label": "Right", "value": str(right)},
                               {"label": "Mistakes pinpointed", "value": str(errors)},
                               {"label": "Solved with proof", "value": str(solved)},
                               {"label": "Practice made", "value": str(assigned)},
                               {"label": "Re-verified now", "value": f"{report['replayed_and_verified']}/{report['entries']}"}],
                     "translation": None}}


def summary_text(payload):
    """Compact model-facing text; the full bundle stays in structured content."""
    view = payload.get("view") or {}
    lines = [f"status={payload.get('status')} verdict={payload.get('verdict')} "
             f"certificate_verified={str(payload.get('certificate_verified')).lower()}"]
    if payload.get("claim"):
        lines.append(f"claim={payload['claim']}")
    replay = payload.get("replay")
    if replay:
        lines.append(f"independent_replay={replay['status']} checks={replay['checks_passed']}/{replay['checks_total']}")
    if payload.get("message"):
        lines.append("message=" + payload["message"])
    translation = view.get("translation")
    if translation:
        shown = translation.get("equations") or [" ; ".join(line) for line in translation.get("steps") or []]
        lines.append("typed_model=" + " | ".join(shown) + f" (unknowns: {', '.join(translation['unknowns'])}; "
                     f"domain: {translation['domain']})")
    lines.append("title=" + str(view.get("title")))
    lines.append("suggested_speech=" + str(view.get("spoken")))
    for step in view.get("steps", [])[:8]:
        lines.append("step: " + step)
    for item in view.get("review", [])[:4]:
        lines.append(f"review: line {item['step_index'] + 1} ({item['reason']}) read as: {item['reading']}"
                     + (f" source={json.dumps(item['source'], sort_keys=True)}" if item.get("source") else ""))
    if view.get("focus") and view.get("verdict") == "error_found" and view["focus"].get("source"):
        lines.append("highlight: " + json.dumps(view["focus"], sort_keys=True))
    if view.get("verdict") == "needs_review":
        lines.append("next=ask the person to confirm or rewrite the flagged line, then call check_work again; "
                     "do not guess how it should read, and do not say the work is right or wrong yet")
    if view.get("hint"):
        lines.append(view["hint"])
    if view.get("reveal"):
        lines.append("reveal_only_if_asked=" + view["reveal"])
    if payload.get("saved"):
        lines.append(f"saved_to_notebook entry={payload['saved']['entry_id']}")
    if payload.get("bundle"):
        lines.append("certificate_sha256=" + payload["bundle"]["certificate"]["certificate_sha256"])
    lines.append(EXACT_RULES)
    return "\n".join(lines)
