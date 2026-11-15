"""HTTP-level tests: two live sessions, SSE, CSRF, input limits, object-level authorization."""
import http.client
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from app.server import make_handler
from tests.helpers import CANARY_ORG2, CANARY_PASSAGE, make_app


class Client:
    def __init__(self, port):
        self.port, self.cookie = port, ""

    def req(self, method, path, body=None, headers=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"X-Requested-With": "triage-ui", "Content-Type": "application/json"}
        if self.cookie:
            h["Cookie"] = self.cookie
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body) if body is not None else None)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        sc = r.getheader("Set-Cookie")
        if sc and sc.startswith("sid="):
            self.cookie = sc.split(";")[0]
        txt = r.read().decode()
        c.close()
        return r.status, (json.loads(txt) if txt and txt[0] in "[{" else txt), r


class TestHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = make_app()
        cls.app.start_worker()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.app))
        cls.srv.daemon_threads = True
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.app._stop.set()
        cls.srv.shutdown()

    def login(self, uid):
        c = Client(self.port)
        st, _, _ = c.req("POST", "/api/login", {"user_id": uid})
        self.assertEqual(st, 200)
        return c

    def test_security_headers_and_csrf(self):
        c = Client(self.port)
        st, _, r = c.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("script-src 'self'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        st, _, _ = c.req("POST", "/api/login", {"user_id": "u_riley"}, headers={"X-Requested-With": ""})
        self.assertEqual(st, 403)

    def test_unauthenticated_and_oversized_and_bad_ids(self):
        anon = Client(self.port)
        self.assertEqual(anon.req("GET", "/api/state")[0], 401)
        r = self.login("u_riley")
        self.assertEqual(r.req("POST", "/api/encounters/enc_A/escalate", raw="x" * 20000)[0], 413)
        self.assertEqual(r.req("GET", "/api/encounters/..%2fenc_A")[0], 404)
        self.assertEqual(r.req("GET", "/api/encounters/enc_S")[0], 404)        # other org looks like missing
        self.assertEqual(r.req("GET", "/api/review-items/ri_nope")[0], 404)
        self.assertEqual(r.req("GET", "/static/../app/config.py")[0], 404)

    def test_no_raw_text_intake_endpoint(self):
        r = self.login("u_riley")
        for path in ("/api/notes", "/api/upload", "/api/sim/ingest", "/api/extract"):
            st, _, _ = r.req("POST", path, {"body": "pasted real note", "encounter_id": "enc_A"})
            self.assertEqual(st, 404, path)
        st, body, _ = r.req("POST", "/api/sim/deliver", {"note_id": "note_FAKE", "version": 1})
        self.assertEqual(st, 404)

    def test_two_sessions_live_reorder_and_org_isolation(self):
        riley, sam, casey = self.login("u_riley"), self.login("u_sam"), self.login("u_casey")
        # Riley's live event stream
        events = []
        def listen():
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", "/api/events", headers={"Cookie": riley.cookie})
            resp = c.getresponse()
            buf = b""
            t0 = time.time()
            while time.time() - t0 < 6:
                line = resp.fp.readline()
                if not line:
                    break
                if line.startswith(b"data:"):
                    events.append(json.loads(line[5:]))
        th = threading.Thread(target=listen, daemon=True)
        th.start()
        time.sleep(0.4)
        st, d, _ = riley.req("POST", "/api/sim/deliver", {"note_id": "note_B2", "version": 1})
        item = d["review_item_id"]
        for _ in range(30):
            _, s, _ = riley.req("GET", "/api/state")
            if s["inbox"] and s["inbox"][0]["ai_state"] == "findings":
                break
            time.sleep(0.2)
        before = [q["encounter_id"] for q in s["queue"]]
        _, det, _ = sam.req("GET", f"/api/review-items/{item}")
        st, _, _ = sam.req("POST", f"/api/encounters/enc_B/priority",
                           {"priority": 1, "reason_code": "new_evidence_confirmed", "expected_version": det["encounter_version"],
                            "review_item_id": item})
        self.assertEqual(st, 200)
        _, s2, _ = riley.req("GET", "/api/state")
        after = [q["encounter_id"] for q in s2["queue"]]
        self.assertLess(after.index("enc_B"), after.index("enc_A"))
        self.assertLess(before.index("enc_A"), before.index("enc_B"))
        # "Enter on A" opens A: the client opens by the row's encounter id, verified here server-side
        _, enc, _ = riley.req("GET", "/api/encounters/enc_A")
        self.assertEqual(enc["encounter_id"], "enc_A")
        self.assertTrue(any(o["id"] == "ob_A_re1" for o in enc["queue_row"]["obligations"]))
        th.join(7)
        types = [e["type"] for e in events]
        self.assertIn("note_received", types)
        self.assertIn("queue_changed", types)
        for e in events:
            blob = json.dumps(e)
            self.assertNotIn("Patient B", blob)
            self.assertNotIn(CANARY_PASSAGE, blob)
        self.assertEqual(len({e["seq"] for e in events}), len(events))   # no duplicate deliveries
        # Other clinic sees nothing from Clinic North
        _, cs, _ = casey.req("GET", "/api/state")
        blob = json.dumps(cs)
        self.assertNotIn("enc_B", blob)
        self.assertNotIn("Patient A", blob)
        self.assertEqual(casey.req("GET", f"/api/review-items/{item}")[0], 404)
        self.assertEqual(casey.req("GET", f"/api/review-items/{item}/ai-request")[0], 404)
        _, rs, _ = riley.req("GET", "/api/state")
        self.assertNotIn(CANARY_ORG2, json.dumps(rs))


if __name__ == "__main__":
    unittest.main()
