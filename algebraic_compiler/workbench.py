"""Local deterministic workbench. No LLM, cloud, or implicit certificate storage."""

import argparse
import copy
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re

from .compiler import compile_reconstruction
from .ir import InputError, MAX_BYTES, canonical, digest, fields, load_json, rational, require, scalar, parse_problem, problem_schema
from .verifier import verify_run

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).with_name("web")
EXAMPLES = ("second-order-obstructed", "second-order-extends", "weak-consistent", "weak-inconsistent", "strict-point-valid")


def prepare_question(payload):
    """Scale a supplied teaching direction exactly; this is not a proof or NLP parser."""
    fields(payload, ("example", "scale"))
    name, text = payload["example"], payload["scale"]
    require(name in EXAMPLES[:2], "UNSUPPORTED", "Choose a second-order teaching model")
    require(isinstance(text, str) and re.fullmatch(r"-?[0-9]{1,8}(?:/[0-9]{1,8})?", text),
            "INVALID_SCHEMA", "Use an integer or fraction, up to 8 digits per part (for example 2 or -3/2)")
    parts = text.split("/")
    require(len(parts) == 1 or int(parts[1]) > 0, "INVALID_SCHEMA", "Denominator must be positive")
    scale = Fraction(text)
    model = load_json((ROOT / "examples/algebraic" / (name + ".json")).read_bytes())
    model["tangent_direction"]["coordinates"] = [scalar(scale * rational(q)) for q in model["tangent_direction"]["coordinates"]]
    candidate = parse_problem(model)
    return {"status": "CANDIDATE_SPEC", "specification_json": candidate.spec_json.decode(),
            "input_digest": candidate.sha256,
            "user_request": "In the supplied " + ("one-dimensional" if name == EXAMPLES[0] else "two-dimensional") +
                " dual-number teaching model, can " + str(scale) +
                " times the original direction extend through second order at this fixed point? " +
                "Calculate and independently replay an exact correction or obstruction witness." +
                (" The zero direction is trivial." if not scale else ""),
            "message": "Question prepared; no certificate yet. Only the supplied direction changes. " +
                ("The zero direction is trivial. " if not scale else "") + "Higher orders and other points are outside this question."}


def present(run):
    """Only independently replayed claims receive a mathematical explanation."""
    replay = verify_run(run)
    view = {"title": "Certificate not verified", "meaning": "No mathematical conclusion is certified.",
            "facts": [], "passes": [], "input_digest": "", "claim": "NOT_EVALUATED", "specification": ""}
    if replay["certificate_verified"]:
        claim, result = run["mathematical_status"], run["result"]
        view.update(claim=claim, specification=canonical(run["problem"]).decode(), passes=[{"name": c["pass"], "claim": c["claim"]}
                                       for c in run["certificates"]],
                    input_digest=run["certificates"][0]["input_sha256"])
        if claim == "weak.consistent":
            view.update(title="A hidden action can fit these observations",
                        meaning="This is the complete weak affine family. Strict bimodule identities have not been certified.",
                        facts=["Rank: " + str(result["rank"]), "Free parameters: " + str(result["fiber_dimension"]),
                               "Particular action: " + str([str(rational(q, 8192)) for q in result["particular_solution"]])])
        elif claim == "weak.inconsistent":
            w = result["inconsistency_witness"]
            view.update(title="These observations cannot fit any weak action",
                        meaning="An exact linear combination cancels every unknown but leaves a nonzero right-hand side. The requested weak system is inconsistent.",
                        facts=["λᵀM = 0", "λᵀr = " + str(rational(w["rhs_pairing"], 8192)),
                               "λ = " + str([str(rational(q, 8192)) for q in w["left_null_vector"]])])
        elif claim.startswith("strict.point_"):
            valid = claim == "strict.point_valid"
            view.update(title="This candidate satisfies every strict identity" if valid else "This candidate fails the strict identities",
                        meaning="Only this supplied point is certified. No global uniqueness or deformation claim is made." if valid else
                        "Only this supplied point is rejected. Other strict solutions may exist; no empty-fiber claim is made.",
                        facts=[name + ": " + str(sum(q["num"] != 0 for q in values)) + " nonzero residuals"
                               for name, values in result["residuals"].items()])
        elif claim == "tangent.analyzed":
            view.update(title="This strict point has " + str(result["dimension"]) + " tangent directions",
                        meaning="Exact kernel of the strict tangent operator. A tangent alone does not certify extension; zero dimension is local rigidity, not global uniqueness.",
                        facts=["Dₚ ξ = 0", "Tangent dimension: " + str(result["dimension"]),
                               "Rank: " + str(result["rank"]) + " / " + str(result["ambient_dimension"])])
        elif claim.startswith("obstruction."):
            dimension = next(c["result"]["dimension"] for c in run["certificates"] if c["pass"] == "tangent")
            if claim == "obstruction.not_tangent":
                view.update(title="This direction fails the first-order test",
                            meaning="Only the supplied direction is rejected. No second-order obstruction was evaluated.",
                            facts=["Dₚ ξ ≠ 0", "Nonzero tangent residuals: " + str(sum(q["num"] != 0 for q in result["tangent_residuals"]))])
            else:
                extends = claim == "obstruction.vanishes"
                facts = ["Verified strict base point", "Tangent dimension: " + str(dimension), "First order: Dₚ ξ = 0"]
                operator = next(c["result"]["operator"] for c in run["certificates"] if c["pass"] == "tangent")
                basis = run["problem"]["algebra"]["basis"]
                if extends:
                    facts += ["Second order: Dₚ η + Qₚ(ξ) = 0"]
                    correction = []
                    for action, q in zip(operator["variable_layout"], result["lift_witness"]):
                        if q["num"]:
                            correction.append("η " + action["side"] + "(" + basis[action["algebra_basis_index"]] + ")" +
                                              "[" + str(action["row"] + 1) + "," + str(action["col"] + 1) + "] = " + str(rational(q, 8192)))
                    facts += correction or ["Exact correction η = 0"]
                else:
                    witness = result["nonmembership_witness"]
                    facts += ["λᵀDₚ = 0", "λᵀQₚ(ξ) = " + str(rational(witness["pairing"], 8192))]
                    for i, q in enumerate(witness["left_null_vector"]):
                        if q["num"]:
                            equation = operator["equation_layout"][i]
                            indices = equation["indices"]
                            label = equation["family"] + " relation"
                            if equation["family"] in ("left", "right", "mixed"):
                                label += " for " + basis[indices[0]] + " × " + basis[indices[1]]
                                label += ", entry [" + str(indices[2] + 1) + "," + str(indices[3] + 1) + "]"
                            facts.append("Witness row " + str(i) + ": " + label + ", weight " + str(rational(q, 8192)))
                view.update(title="This direction extends through second order" if extends else "First order passes. Second order cannot.",
                            meaning="An exact correction certifies this supplied direction modulo t³. Higher orders and other directions are not certified." if extends else
                            "The exact dual witness rules out every second-order correction for this supplied direction at this point. Other directions or points are not ruled out.", facts=facts)
        else:
            view.update(title=claim, meaning="Verified only through the requested pass. No later result is implied.")
    return {"bundle": canonical(run).decode(), "replay": replay, "view": view}


def tamper(run):
    """Educational forgery: change a witness AND rehash; integrity is not proof."""
    altered = copy.deepcopy(run)
    cert = altered["certificates"][-1]
    result = cert["result"]
    if cert["pass"] == "weak":
        result["rank"] += 1
    elif cert["pass"] == "strict":
        result["residuals"]["unit_left"][0] = {"num": 999, "den": 1}
    elif cert["pass"] == "tangent":
        result["dimension"] += 1
    elif cert["pass"] == "obstruction":
        result["tangent_residuals"][0] = {"num": 999, "den": 1}
    else:
        cert["claim"] = "forged.claim"
    cert["certificate_sha256"] = digest({k: v for k, v in cert.items() if k != "certificate_sha256"})
    altered["result"] = result
    return altered


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def respond(self, code, content, kind="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(content)

    def local_request(self):
        expected = "127.0.0.1:" + str(self.server.server_port)
        return (self.headers.get("Host") == expected and
                self.headers.get("Origin", "http://" + expected) == "http://" + expected)

    def do_GET(self):
        if not self.local_request():
            self.respond(403, b'{"error":"Local origin required"}')
            return
        assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                  "/style.css": ("style.css", "text/css")}
        if self.path in assets:
            name, kind = assets[self.path]
            self.respond(200, (WEB / name).read_bytes(), kind)
            return
        if self.path == "/api/schema":
            self.respond(200, canonical(problem_schema()))
            return
        if self.path == "/api/examples":
            self.respond(200, canonical({name: (ROOT / "examples" / "algebraic" / (name + ".json")).read_text()
                                        for name in EXAMPLES}))
            return
        self.respond(404, b'{"error":"Not found"}')

    def do_POST(self):
        if not self.local_request():
            self.respond(403, b'{"error":"Local origin required"}')
            return
        if self.path not in ("/api/compile", "/api/verify", "/api/tamper", "/api/candidate", "/api/question"):
            self.respond(404, b'{"error":"Not found"}')
            return
        try:
            if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
                raise InputError("INVALID_SCHEMA", "Send fixed-length application/json")
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 <= length <= MAX_BYTES:
                raise InputError("RESOURCE_LIMIT", "Request exceeds byte limit")
            payload = load_json(self.rfile.read(length))
            if self.path == "/api/question":
                self.respond(200, canonical(prepare_question(payload)))
                return
            if self.path == "/api/candidate":
                candidate = parse_problem(payload)
                self.respond(200, canonical({"status": "CANDIDATE_SPEC",
                    "specification_json": candidate.spec_json.decode(), "input_digest": candidate.sha256,
                    "message": "Typed schema accepted. Algebra laws have not been verified."}))
                return
            if self.path == "/api/compile":
                run = compile_reconstruction(payload)
                if not run["certificate_verified"]:
                    self.respond(200, canonical({"error": run["status"], "code": run["code"],
                                                 "message": run.get("message", "No certified result")}))
                    return
            else:
                run = payload
            if self.path == "/api/tamper":
                if not verify_run(run)["certificate_verified"]:
                    raise InputError("CERTIFICATE_WITNESS_INVALID", "Start from a verified bundle")
                run = tamper(run)
            # Bundle is a string so browser number parsing cannot round exact integers.
            content = json.dumps(present(run), ensure_ascii=False).encode()
            self.respond(200, content)
        except (InputError, ValueError) as exc:
            self.respond(400, canonical({"error": getattr(exc, "code", "INVALID_SCHEMA"), "message": str(exc)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not (ROOT / "examples/algebraic/weak-consistent.json").is_file():
        parser.error("The workbench requires the source checkout with examples/; the installed core CLI remains available.")
    with HTTPServer(("127.0.0.1", args.port), Handler) as server:
        print("Workbench: http://127.0.0.1:" + str(args.port), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
