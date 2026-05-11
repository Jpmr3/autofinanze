import io
import os
import tempfile
import unittest


class AutofinanzeAppTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["AUTOFINANZE_DB"] = os.path.join(self.tempdir.name, "test.db")
        os.environ["AUTOFINANZE_BASE_URL"] = "http://127.0.0.1:8000"
        import importlib
        import app as app_module
        self.app_module = importlib.reload(app_module)
        self.app_module.init_db()

    def tearDown(self):
        self.tempdir.cleanup()
        os.environ.pop("AUTOFINANZE_DB", None)

    def _call(self, method, path, body="", content_type="application/x-www-form-urlencoded"):
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


if __name__ == "__main__":
    unittest.main()
