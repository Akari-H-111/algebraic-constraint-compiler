"""Exercise the real local HTTP boundary and portable exact bundles."""

from http.client import HTTPConnection
from http.server import HTTPServer
import json
import threading
import unittest
from unittest.mock import patch

from algebraic_compiler.ir import MAX_BYTES, canonical, load_json
from algebraic_compiler.verifier import verify_run
from algebraic_compiler.workbench import Handler, ROOT, EXAMPLES


class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def request(self, path, body=None, headers=None):
        client = HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            client.request("POST" if body is not None else "GET", path, body,
                           headers or {"Content-Type": "application/json"})
            response = client.getresponse()
            return response.status, response.read()
        finally:
            client.close()

    def test_compile_export_replay_and_rehashed_forgery(self):
        for name in EXAMPLES:
            raw = (ROOT / "examples" / "algebraic" / (name + ".json")).read_bytes()
            code, content = self.request("/api/compile", raw)
            self.assertEqual(code, 200)
            data = json.loads(content)
            run = load_json(data["bundle"])
            self.assertTrue(verify_run(run)["certificate_verified"])
            self.assertEqual(load_json(data["view"]["specification"]), run["problem"])
            if name == "second-order-obstructed":
                self.assertIn("Witness row 21: left relation for epsilon × epsilon, entry [1,1], weight 1", data["view"]["facts"])
            if name == "second-order-extends":
                self.assertIn("η L(epsilon)[2,1] = -1", data["view"]["facts"])
            with patch("algebraic_compiler.compiler.weak", side_effect=RuntimeError("No solver during replay")):
                _, replay = self.request("/api/verify", data["bundle"].encode())
            self.assertTrue(json.loads(replay)["replay"]["certificate_verified"])
            _, forged = self.request("/api/tamper", data["bundle"].encode())
            self.assertFalse(json.loads(forged)["replay"]["certificate_verified"])
            self.assertEqual(json.loads(forged)["view"]["claim"], "NOT_EVALUATED")

    def test_exact_large_integer_preservation_and_candidate_scope(self):
        raw = load_json((ROOT / "examples/algebraic/strict-point-valid.json").read_bytes())
        raw["strict_point"]["coordinates"][0] = {"num": 9007199254740993, "den": 1}
        _, content = self.request("/api/compile", canonical(raw))
        data = json.loads(content)
        self.assertIn('9007199254740993', data["bundle"])
        self.assertTrue(data["replay"]["certificate_verified"])
        self.assertIn("Other strict solutions may exist", data["view"]["meaning"])
        self.assertEqual(load_json(data["bundle"])["mathematical_status"], "strict.point_invalid")

    def test_candidate_schema_is_not_a_mathematical_claim(self):
        code, schema = self.request("/api/schema")
        self.assertEqual(code, 200)
        self.assertIn("obstruction", json.loads(schema)["properties"]["requested_passes"]["enum"][-1])
        raw = load_json((ROOT / "examples/algebraic/strict-point-valid.json").read_bytes())
        raw["algebra"]["unit"]["coordinates"][0] = {"num":0,"den":1}
        code, content = self.request("/api/candidate", canonical(raw))
        data = json.loads(content)
        self.assertEqual(code, 200)
        self.assertEqual(data["status"], "CANDIDATE_SPEC")
        self.assertNotIn("certificate_verified", data)
        self.assertIn("have not been verified", data["message"])
        self.assertEqual(self.request("/api/candidate", b'{}')[0], 400)

    def test_guided_question_exact_scaling_and_certified_limits(self):
        for name, scale, expected in (("second-order-extends", "2", "η L(epsilon)[2,1] = -4"),
                                      ("second-order-extends", "-3/2", "η L(epsilon)[2,1] = -9/4"),
                                      ("second-order-obstructed", "2", "λᵀQₚ(ξ) = 4"),
                                      ("second-order-obstructed", "0", "Exact correction η = 0")):
            original = load_json((ROOT / "examples/algebraic" / (name + ".json")).read_bytes())
            with patch("algebraic_compiler.workbench.compile_reconstruction", side_effect=RuntimeError("No proof during preparation")):
                code, content = self.request("/api/question", canonical({"example": name, "scale": scale}))
            data = json.loads(content)
            self.assertEqual(code, 200)
            self.assertNotIn("certificate_verified", data)
            candidate = load_json(data["specification_json"])
            self.assertEqual(candidate["strict_point"], original["strict_point"])
            self.assertEqual(candidate["algebra"], original["algebra"])
            _, content = self.request("/api/compile", data["specification_json"].encode())
            run = json.loads(content)
            self.assertTrue(run["replay"]["certificate_verified"])
            self.assertIn(expected, run["view"]["facts"])
            with patch("algebraic_compiler.compiler.weak", side_effect=RuntimeError("No solver during replay")):
                _, content = self.request("/api/verify", run["bundle"].encode())
            self.assertTrue(json.loads(content)["replay"]["certificate_verified"])
        for scale in ("0.5", "1/0", "1/-2", "1e3", "123456789", 2, "", "2/3/4"):
            self.assertEqual(self.request("/api/question", canonical({"example": "second-order-extends", "scale": scale}))[0], 400)
        self.assertEqual(self.request("/api/question", canonical({"example": "weak-consistent", "scale": "2"}))[0], 400)
        self.assertEqual(self.request("/api/question", b'{}')[0], 400)

    def test_local_origin_payload_and_static_boundaries(self):
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/.git/config")[0], 404)
        self.assertEqual(self.request("/", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("/api/compile", b'{}', {"Origin":"https://evil.example"})[0], 403)
        self.assertEqual(self.request("/api/compile", b'{}', {"Content-Type":"text/plain"})[0], 400)
        self.assertEqual(self.request("/api/compile", b'', {"Content-Type": "application/json", "Content-Length": str(MAX_BYTES + 1)})[0], 400)
        for payload in (b'{"x":0.5}', b'{"x":1,"x":2}'):
            self.assertEqual(self.request("/api/compile", payload)[0], 400)
        _, data = self.request("/api/compile", b'{}')
        self.assertIn("error", json.loads(data))
        self.assertNotIn("view", json.loads(data))
        _, data = self.request("/api/verify", b'{}')
        self.assertFalse(json.loads(data)["replay"]["certificate_verified"])


if __name__ == "__main__":
    unittest.main()
