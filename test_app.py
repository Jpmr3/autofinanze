import io
import hmac
import hashlib
import json
import os
import tempfile
import time
import unittest


class AutofinanzeAppTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["AUTOFINANZE_DB"] = os.path.join(self.tempdir.name, "test.db")
        os.environ["AUTOFINANZE_BASE_URL"] = "http://127.0.0.1:8000"
        os.environ["AUTOMATION_RUN_KEY"] = "test-key"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test"
        import importlib
        import app as app_module
        self.app_module = importlib.reload(app_module)
        self.app_module.init_db()

    def tearDown(self):
        self.tempdir.cleanup()
        os.environ.pop("AUTOFINANZE_DB", None)
        os.environ.pop("AUTOFINANZE_BASE_URL", None)
        os.environ.pop("AUTOMATION_RUN_KEY", None)
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)

    def _call(self, method, path, body="", content_type="application/x-www-form-urlencoded", extra_environ=None):
        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = dict(headers)

        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path.split("?")[0],
            "QUERY_STRING": path.split("?", 1)[1] if "?" in path else "",
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": str(len(body.encode("utf-8"))),
            "wsgi.input": io.BytesIO(body.encode("utf-8")),
        }
        if extra_environ:
            environ.update(extra_environ)
        payload = b"".join(self.app_module.app(environ, start_response)).decode("utf-8")
        return captured["status"], captured["headers"], payload

    def test_landing_responds(self):
        status, _, body = self._call("GET", "/")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Convierte tu idea en ventas hoy", body)

    def test_pay_creates_complete_order(self):
        status, headers, _ = self._call(
            "POST",
            "/pay",
            "email=test%40mail.com&full_name=Test+User&tax_id=ES123",
        )
        self.assertTrue(status.startswith("302"))
        self.assertIn("/thank-you?order=", headers.get("Location", ""))

        with self.app_module.get_db() as conn:
            order = conn.execute("SELECT * FROM orders WHERE email='test@mail.com'").fetchone()
            self.assertIsNotNone(order)
            invoice = conn.execute("SELECT * FROM invoices WHERE order_id=?", (order["id"],)).fetchone()
            delivery = conn.execute("SELECT * FROM deliveries WHERE order_id=?", (order["id"],)).fetchone()
            self.assertIsNotNone(invoice)
            self.assertIsNotNone(delivery)

    def test_stripe_webhook_creates_order_with_valid_signature(self):
        payload = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_123",
                    "amount_total": 4900,
                    "customer_details": {"email": "stripe@test.com"},
                    "metadata": {"full_name": "Stripe User", "tax_id": "X123"},
                }
            },
        }
        raw = json.dumps(payload)
        timestamp = str(int(time.time()))
        signed = f"{timestamp}.{raw}".encode("utf-8")
        sig = hmac.new(b"whsec_test", signed, hashlib.sha256).hexdigest()
        header = f"t={timestamp},v1={sig}"
        status, _, body = self._call(
            "POST",
            "/webhooks/stripe",
            body=raw,
            content_type="application/json",
            extra_environ={"HTTP_STRIPE_SIGNATURE": header},
        )
        self.assertTrue(status.startswith("200"))
        self.assertIn('"ok": true', body)
        with self.app_module.get_db() as conn:
            order = conn.execute("SELECT * FROM orders WHERE email='stripe@test.com'").fetchone()
            self.assertIsNotNone(order)

    def test_automation_run_marks_followups_as_sent(self):
        self._call(
            "POST",
            "/pay",
            "email=auto%40mail.com&full_name=Auto+User&tax_id=ES123",
        )
        with self.app_module.get_db() as conn:
            conn.execute("UPDATE followups SET scheduled_at='2000-01-01T00:00:00+00:00' WHERE email='auto@mail.com'")
        status, _, body = self._call(
            "POST",
            "/automation/run",
            "",
            extra_environ={"HTTP_X_AUTOMATION_KEY": "test-key"},
        )
        self.assertTrue(status.startswith("200"))
        self.assertIn('"sent": 2', body)
        with self.app_module.get_db() as conn:
            sent = conn.execute("SELECT COUNT(*) c FROM followups WHERE email='auto@mail.com' AND status='sent'").fetchone()["c"]
            self.assertEqual(sent, 2)


if __name__ == "__main__":
    unittest.main()
