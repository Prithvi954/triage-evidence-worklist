"""FHIR connector against a local fake FHIR R4 server (synthetic data)."""
import base64
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app import fhir
from tests.helpers import make_app, user

NOTE1 = "ED triage note. Jane Q. Testpatient reports demo signal A since this morning. MRN 4455667 noted."
NOTE2 = "Reassessment. Testpatient now denies demo signal A. Daughter at bedside."


class FakeFhir:
    def __init__(self):
        self.encounters = {"e1": {"resourceType": "Encounter", "id": "e1", "status": "in-progress",
                                  "subject": {"reference": "Patient/p1"}, "period": {"start": "2026-11-14T09:00:00Z"},
                                  "reasonCode": [{"text": "Demo concern X"}], "location": [{"location": {"display": "ED 7"}}]}}
        self.patient = {"resourceType": "Patient", "id": "p1", "name": [{"family": "Testpatient", "given": ["Jane", "Q."]}],
                        "gender": "female", "birthDate": "1980-04-02", "identifier": [{"value": "4455667"}],
                        "telecom": [{"system": "phone", "value": "555-0100"}]}
        self.docs = [self._doc("d1", NOTE1, "2026-11-14T09:05:00Z")]
        self.auth_seen = []

    @staticmethod
    def _doc(i, text, when):
        return {"resourceType": "DocumentReference", "id": i, "status": "current", "date": when,
                "content": [{"attachment": {"contentType": "text/plain", "data": base64.b64encode(text.encode()).decode()}}]}

    def serve(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                fake.auth_seen.append(self.headers.get("Authorization"))
                u = urlparse(self.path)
                q = parse_qs(u.query)
                if u.path == "/fhir/Encounter":
                    body = {"resourceType": "Bundle", "entry": [{"resource": e} for e in fake.encounters.values()
                                                                if e["status"] == q.get("status", ["in-progress"])[0]]}
                elif u.path == "/fhir/Patient/p1":
                    body = fake.patient
                elif u.path == "/fhir/DocumentReference":
                    body = {"resourceType": "Bundle", "entry": [{"resource": d} for d in fake.docs]}
                else:
                    self.send_response(404); self.end_headers(); return
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/fhir+json")
                self.end_headers()
                self.wfile.write(data)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv


class TestFhir(unittest.TestCase):
    def setUp(self):
        self.fake = FakeFhir()
        self.srv = self.fake.serve()
        self.app = make_app()
        self.client = fhir.FhirClient(f"http://127.0.0.1:{self.srv.server_address[1]}/fhir", token="sandbox-token")

    def tearDown(self):
        self.srv.shutdown()

    def test_sync_creates_encounter_and_notes_idempotently(self):
        stats = fhir.sync(self.app.svc, self.client, "org_n")
        self.assertEqual((stats["encounters"], stats["notes"]), (1, 1))
        self.assertIn("Bearer sandbox-token", self.fake.auth_seen)
        riley = user(self.app, "u_riley")
        row = next(q for q in self.app.svc.state(riley)["queue"] if q["encounter_id"] == "fhir_e1")
        self.assertEqual(row["label"], "Testpatient, Jane Q.")
        self.assertEqual(row["display"]["sex"], "F")
        self.assertEqual(row["display"]["location"], "ED 7")
        self.assertEqual(row["unreviewed_notes"], 1)
        again = fhir.sync(self.app.svc, self.client, "org_n")
        self.assertEqual((again["encounters"], again["notes"]), (0, 0))      # nothing duplicated
        self.fake.docs.append(self.fake._doc("d2", NOTE2, "2026-11-14T09:40:00Z"))
        self.assertEqual(fhir.sync(self.app.svc, self.client, "org_n")["notes"], 1)

    def test_real_notes_never_go_to_ai_outside_clinical_mode(self):
        fhir.sync(self.app.svc, self.client, "org_n")
        self.app.svc.run_jobs_once()
        self.assertEqual(self.app.adapter.sent_requests, [])
        st = self.app.svc.state(user(self.app, "u_riley"))
        item = next(i for i in st["inbox"] if i["encounter_id"] == "fhir_e1")
        self.assertEqual(item["ai_state"], "blocked_not_approved_fixture")
        self.assertIn("manual review required", item["ai_state_text"])

    def test_clinical_mode_sends_minimized_text_without_patient_identifiers(self):
        self.app.adapter.allow_real_notes = True     # what clinical mode + confirmed BAA would set
        fhir.sync(self.app.svc, self.client, "org_n")
        self.app.svc.run_jobs_once()
        sent = json.dumps(self.app.adapter.sent_requests)
        self.assertIn("demo signal A", sent)
        for ident in ("Testpatient", "Jane", "4455667", "1980-04-02", "555-0100"):
            self.assertNotIn(ident, sent)
        self.assertIn("[identifier removed]", sent)

    def test_finished_encounter_is_closed_not_deleted(self):
        fhir.sync(self.app.svc, self.client, "org_n")
        self.fake.encounters["e1"]["status"] = "finished"
        self.assertEqual(fhir.sync(self.app.svc, self.client, "org_n")["closed"], 1)
        self.assertEqual(self.app.store.one("SELECT status FROM encounters WHERE id='fhir_e1'")["status"], "closed")

    def test_insecure_url_refused_and_errors_recorded(self):
        with self.assertRaises(fhir.FhirError):
            fhir.FhirClient("http://ehr.example.com/fhir")
        bad = fhir.FhirClient(f"http://127.0.0.1:{self.srv.server_address[1]}/nope")
        with self.assertRaises(fhir.FhirError):
            fhir.sync(self.app.svc, bad, "org_n")
        row = self.app.store.one("SELECT last_error FROM fhir_sync")
        self.assertEqual(row["last_error"], "FhirError")


if __name__ == "__main__":
    unittest.main()
