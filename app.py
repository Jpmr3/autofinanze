import json
import os
import secrets
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server


DB_PATH = os.getenv("AUTOFINANZE_DB", os.path.join(os.path.dirname(__file__), "autofinanze.db"))
BASE_URL = os.getenv("AUTOFINANZE_BASE_URL", "http://127.0.0.1:8000")
OFFER_NAME = os.getenv("OFFER_NAME", "Sistema Ingresos Rápidos MVP")
OFFER_PRICE_CENTS = int(os.getenv("OFFER_PRICE_CENTS", "4900"))
OFFER_CURRENCY = "EUR"
TAX_RATE = 0.21
UPSELL_PRICE_CENTS = int(os.getenv("UPSELL_PRICE_CENTS", "19900"))
UPSELL_NAME = os.getenv("UPSELL_NAME", "Implementación asistida 1:1")
FOLLOWUP_TEMPLATES = [
    ("email", "¿Te ayudo a implementar el sistema en 24h?", 1),
    ("whatsapp", "Plantilla rápida para captar más ventas hoy", 3),
]
STRIPE_PAYMENT_LINK = os.getenv("STRIPE_PAYMENT_LINK", "").strip()
AUTOMATION_RUN_KEY = os.getenv("AUTOMATION_RUN_KEY", "").strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                source TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                full_name TEXT,
                tax_id TEXT,
                amount_cents INTEGER NOT NULL,
                currency TEXT NOT NULL,
                status TEXT NOT NULL,
                payment_provider TEXT NOT NULL,
                payment_ref TEXT,
                offer_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                invoice_number TEXT NOT NULL UNIQUE,
                subtotal_cents INTEGER NOT NULL,
                tax_cents INTEGER NOT NULL,
                total_cents INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(order_id) REFERENCES orders(id)
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                access_token TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                delivered_at TEXT,
                FOREIGN KEY(order_id) REFERENCES orders(id)
            );
            CREATE TABLE IF NOT EXISTS funnel_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                email TEXT,
                metadata TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS followups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                channel TEXT NOT NULL,
                message TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                sent_at TEXT,
                status TEXT NOT NULL
            );
            """
        )


def money(cents: int) -> str:
    return f"{cents / 100:.2f} {OFFER_CURRENCY}"


def parse_post_data(environ):
    try:
        length = int(environ.get("CONTENT_LENGTH", "0"))
    except ValueError:
        length = 0
    raw = environ["wsgi.input"].read(length).decode("utf-8") if length > 0 else ""
    content_type = (environ.get("CONTENT_TYPE") or "").lower()
    if "application/json" in content_type:
        return json.loads(raw or "{}")
    return {k: v[0] for k, v in parse_qs(raw).items()}


def record_event(event_type: str, email: str = "", metadata: str = ""):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO funnel_events(event_type, email, metadata, created_at) VALUES (?, ?, ?, ?)",
            (event_type, email, metadata, now_iso()),
        )


def create_invoice(conn, order_id: int, total_cents: int):
    tax_cents = int(
        (Decimal(total_cents) * Decimal(str(TAX_RATE))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    subtotal_cents = total_cents - tax_cents
    invoice_number = f"INV-{datetime.now().strftime('%Y%m')}-{order_id:06d}"
    conn.execute(
        """
        INSERT INTO invoices(order_id, invoice_number, subtotal_cents, tax_cents, total_cents, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'paid', ?)
        """,
        (order_id, invoice_number, subtotal_cents, tax_cents, total_cents, now_iso()),
    )


def create_delivery(conn, order_id: int):
    token = secrets.token_urlsafe(24)
    conn.execute(
        "INSERT INTO deliveries(order_id, access_token, created_at) VALUES (?, ?, ?)",
        (order_id, token, now_iso()),
    )
    return token


def schedule_followups(conn, email: str):
    for channel, message, days in FOLLOWUP_TEMPLATES:
        at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        conn.execute(
            """
            INSERT INTO followups(email, channel, message, scheduled_at, sent_at, status)
            VALUES (?, ?, ?, ?, NULL, 'pending')
            """,
            (email, channel, message, at),
        )


def create_paid_order(email: str, full_name: str, tax_id: str, amount_cents: int, provider: str, payment_ref: str, offer_name: str):
    with get_db() as conn:
        ts = now_iso()
        cur = conn.execute(
            """
            INSERT INTO orders(email, full_name, tax_id, amount_cents, currency, status, payment_provider, payment_ref, offer_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'paid', ?, ?, ?, ?, ?)
            """,
            (email, full_name, tax_id, amount_cents, OFFER_CURRENCY, provider, payment_ref, offer_name, ts, ts),
        )
        order_id = cur.lastrowid
        create_invoice(conn, order_id, amount_cents)
        delivery_token = create_delivery(conn, order_id)
        schedule_followups(conn, email)
    record_event("purchase", email, f"order_id={order_id};offer={offer_name}")
    return order_id, delivery_token


def html_page(title: str, body: str):
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  <style>
    body{{font-family:Arial,sans-serif;max-width:920px;margin:20px auto;padding:0 16px;line-height:1.45}}
    .card{{border:1px solid #ddd;border-radius:8px;padding:16px;margin:12px 0}}
    .btn{{display:inline-block;background:#0a66c2;color:#fff;padding:10px 14px;border-radius:6px;text-decoration:none;border:none;cursor:pointer}}
    input{{padding:8px;margin:4px 0;width:100%;max-width:460px}}
    .muted{{color:#666}}
  </style>
</head>
<body>
{body}
</body>
</html>"""


def landing():
    stripe_cta = (
        f'<a class="btn" href="{escape(STRIPE_PAYMENT_LINK)}">Pagar ahora con Stripe</a>'
        if STRIPE_PAYMENT_LINK
        else '<a class="btn" href="/checkout">Comprar ahora</a>'
    )
    return html_page(
        "Autofinanze - Landing",
        f"""
<h1>Convierte tu idea en ventas hoy</h1>
<p>Oferta: <strong>{escape(OFFER_NAME)}</strong> por <strong>{money(OFFER_PRICE_CENTS)}</strong></p>
<div class="card">
  <h3>Lo que incluye</h3>
  <ul>
    <li>Embudo corto listo para vender</li>
    <li>Entrega automática tras pago</li>
    <li>Facturación básica y métricas</li>
  </ul>
  {stripe_cta}
</div>
<div class="card">
  <h3>Prueba social</h3>
  <p>+120 clientes atendidos en proyectos de monetización rápida.</p>
</div>
<div class="card">
  <h3>Recibe diagnóstico gratis</h3>
  <form method="post" action="/lead">
    <input name="email" type="email" required placeholder="tu@email.com" />
    <input name="source" type="text" placeholder="Canal (Instagram, X, etc.)" />
    <button class="btn" type="submit">Quiero diagnóstico</button>
  </form>
</div>
<p class="muted"><a href="/dashboard">Ver métricas</a></p>
""",
    )


def checkout(email_hint: str = ""):
    return html_page(
        "Checkout",
        f"""
<h1>Checkout</h1>
<p>Total a cobrar: <strong>{money(OFFER_PRICE_CENTS)}</strong></p>
<form method="post" action="/pay">
  <input name="email" type="email" required placeholder="Email" value="{escape(email_hint)}" />
  <input name="full_name" type="text" required placeholder="Nombre completo" />
  <input name="tax_id" type="text" placeholder="NIF/CIF (opcional)" />
  <button class="btn" type="submit">Pagar ahora</button>
</form>
<p class="muted">Modo interno de pago para MVP. Si configuras STRIPE_PAYMENT_LINK, usa Stripe Checkout.</p>
""",
    )


def thank_you(order_id: int):
    with get_db() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        invoice = conn.execute("SELECT * FROM invoices WHERE order_id = ?", (order_id,)).fetchone()
        delivery = conn.execute("SELECT * FROM deliveries WHERE order_id = ?", (order_id,)).fetchone()
    if not order or not invoice or not delivery:
        return None
    body = f"""
<h1>¡Pago confirmado!</h1>
<p>Pedido <strong>#{order['id']}</strong> por {money(order['amount_cents'])}.</p>
<p><a class="btn" href="/access/{escape(delivery['access_token'])}">Acceder al producto</a></p>
<p><a href="/invoice/{invoice['id']}">Ver factura {escape(invoice['invoice_number'])}</a></p>
<p><a href="/upsell?order={order['id']}">Ver oferta upsell</a></p>
"""
    return html_page("Gracias por tu compra", body)


def invoice_page(invoice_id: int):
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT i.*, o.email, o.full_name, o.tax_id, o.offer_name
            FROM invoices i JOIN orders o ON o.id = i.order_id
            WHERE i.id = ?
            """,
            (invoice_id,),
        ).fetchone()
    if not row:
        return None
    return html_page(
        f"Factura {row['invoice_number']}",
        f"""
<h1>Factura {escape(row['invoice_number'])}</h1>
<p>Cliente: {escape(row['full_name'] or '-')} ({escape(row['email'])})</p>
<p>NIF/CIF: {escape(row['tax_id'] or '-')}</p>
<p>Concepto: {escape(row['offer_name'])}</p>
<ul>
  <li>Base imponible: {money(row['subtotal_cents'])}</li>
  <li>IVA (21%): {money(row['tax_cents'])}</li>
  <li><strong>Total: {money(row['total_cents'])}</strong></li>
</ul>
<p>Estado: <strong>{escape(row['status'])}</strong></p>
""",
    )


def access_page(token: str):
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT d.id, d.delivered_at, o.email, o.offer_name
            FROM deliveries d JOIN orders o ON o.id = d.order_id
            WHERE d.access_token = ?
            """,
            (token,),
        ).fetchone()
        if not row:
            return None
        if not row["delivered_at"]:
            conn.execute("UPDATE deliveries SET delivered_at = ? WHERE id = ?", (now_iso(), row["id"]))
    record_event("delivery_access", row["email"], row["offer_name"])
    return html_page(
        "Entrega del producto",
        f"""
<h1>Entrega automática completada</h1>
<p>Producto: <strong>{escape(row['offer_name'])}</strong></p>
<p>Email de compra: {escape(row['email'])}</p>
<div class="card">
  <h3>Contenido de entrega (MVP)</h3>
  <ol>
    <li>Plantilla de oferta irresistible</li>
    <li>Guion de ventas de 1 página</li>
    <li>Checklist de activación 24h</li>
  </ol>
</div>
""",
    )


def dashboard():
    with get_db() as conn:
        leads = conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
        paid_orders = conn.execute("SELECT COUNT(*) c FROM orders WHERE status = 'paid'").fetchone()["c"]
        revenue = conn.execute("SELECT COALESCE(SUM(amount_cents),0) c FROM orders WHERE status='paid'").fetchone()["c"]
        avg_ticket = int(revenue / paid_orders) if paid_orders else 0
        conversion = (paid_orders / leads * 100) if leads else 0
    return html_page(
        "Dashboard",
        f"""
<h1>Métricas mínimas</h1>
<ul>
  <li>Visitas landing: revisar `funnel_events` (MVP)</li>
  <li>Leads: <strong>{leads}</strong></li>
  <li>Conversiones (pagos): <strong>{paid_orders}</strong></li>
  <li>Tasa de conversión lead→pago: <strong>{conversion:.2f}%</strong></li>
  <li>Ticket promedio: <strong>{money(avg_ticket)}</strong></li>
  <li>Ingresos: <strong>{money(revenue)}</strong></li>
</ul>
<p><a href="/">Volver a landing</a></p>
""",
    )


def upsell(order_id: int):
    return html_page(
        "Upsell",
        f"""
<h1>Oferta adicional: Implementación asistida</h1>
<p>Sesión 1:1 + personalización del embudo por <strong>{money(UPSELL_PRICE_CENTS)}</strong></p>
<form method="post" action="/upsell/buy">
  <input type="hidden" name="order_id" value="{order_id}" />
  <button class="btn" type="submit">Agregar upsell</button>
</form>
""",
    )


def not_found():
    return "404 Not Found", [("Content-Type", "text/plain; charset=utf-8")], b"No encontrado"


def redirect(location: str):
    return "302 Found", [("Location", location)], b""


def response_html(content: str, code: str = "200 OK"):
    return code, [("Content-Type", "text/html; charset=utf-8")], content.encode("utf-8")


def response_json(obj, code: str = "200 OK"):
    return code, [("Content-Type", "application/json; charset=utf-8")], json.dumps(obj).encode("utf-8")


def _run_pending_followups():
    sent_count = 0
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id FROM followups
            WHERE status='pending' AND scheduled_at <= ?
            """,
            (now_iso(),),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE followups SET status='sent', sent_at=? WHERE id=?",
                (now_iso(), row["id"]),
            )
            sent_count += 1
    return sent_count


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/")
    qs = parse_qs(environ.get("QUERY_STRING", ""))
    record_event("visit" if path == "/" and method == "GET" else "request", metadata=f"{method} {path}")

    if method == "GET" and path == "/":
        status, headers, body = response_html(landing())
    elif method == "POST" and path == "/lead":
        data = parse_post_data(environ)
        email = (data.get("email") or "").strip().lower()
        source = (data.get("source") or "direct").strip()
        if not email:
            status, headers, body = response_html("Email requerido", "400 Bad Request")
        else:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO leads(email, source, created_at) VALUES (?, ?, ?)",
                    (email, source, now_iso()),
                )
            record_event("lead", email, source)
            status, headers, body = redirect(f"/checkout?email={email}")
    elif method == "GET" and path == "/checkout":
        email = (qs.get("email", [""])[0] or "").strip().lower()
        status, headers, body = response_html(checkout(email))
    elif method == "POST" and path == "/pay":
        data = parse_post_data(environ)
        email = (data.get("email") or "").strip().lower()
        full_name = (data.get("full_name") or "").strip()
        tax_id = (data.get("tax_id") or "").strip()
        if not email or not full_name:
            status, headers, body = response_html("Email y nombre son requeridos", "400 Bad Request")
        else:
            payment_ref = f"manual_{secrets.token_hex(6)}"
            order_id, _ = create_paid_order(email, full_name, tax_id, OFFER_PRICE_CENTS, "internal", payment_ref, OFFER_NAME)
            status, headers, body = redirect(f"/thank-you?order={order_id}")
    elif method == "POST" and path == "/webhooks/stripe":
        data = parse_post_data(environ)
        event_type = data.get("type", "")
        if event_type != "checkout.session.completed":
            status, headers, body = response_json({"ok": True, "ignored": True})
        else:
            session = data.get("data", {}).get("object", {})
            metadata = session.get("metadata", {})
            email = (session.get("customer_details", {}).get("email") or metadata.get("email") or "").strip().lower()
            full_name = metadata.get("full_name", "Cliente Stripe")
            tax_id = metadata.get("tax_id", "")
            amount = int(session.get("amount_total") or OFFER_PRICE_CENTS)
            payment_ref = session.get("id", f"stripe_{secrets.token_hex(4)}")
            if not email:
                status, headers, body = response_json({"ok": False, "error": "email requerido"}, "400 Bad Request")
            else:
                order_id, _ = create_paid_order(email, full_name, tax_id, amount, "stripe", payment_ref, OFFER_NAME)
                status, headers, body = response_json({"ok": True, "order_id": order_id})
    elif method == "GET" and path == "/thank-you":
        try:
            order_id = int((qs.get("order") or ["0"])[0])
        except ValueError:
            order_id = 0
        page = thank_you(order_id)
        if page is None:
            status, headers, body = not_found()
        else:
            status, headers, body = response_html(page)
    elif method == "GET" and path.startswith("/invoice/"):
        try:
            invoice_id = int(path.split("/")[-1])
            page = invoice_page(invoice_id)
            if page is None:
                status, headers, body = not_found()
            else:
                status, headers, body = response_html(page)
        except ValueError:
            status, headers, body = not_found()
    elif method == "GET" and path.startswith("/access/"):
        token = path.split("/")[-1]
        page = access_page(token)
        if page is None:
            status, headers, body = not_found()
        else:
            status, headers, body = response_html(page)
    elif method == "GET" and path == "/dashboard":
        status, headers, body = response_html(dashboard())
    elif method == "GET" and path == "/upsell":
        try:
            order_id = int((qs.get("order") or ["0"])[0])
        except ValueError:
            order_id = 0
        status, headers, body = response_html(upsell(order_id))
    elif method == "POST" and path == "/upsell/buy":
        data = parse_post_data(environ)
        try:
            source_order_id = int(data.get("order_id", "0"))
        except ValueError:
            source_order_id = 0
        with get_db() as conn:
            source = conn.execute("SELECT * FROM orders WHERE id = ?", (source_order_id,)).fetchone()
        if not source:
            status, headers, body = response_html("Order base no encontrado", "404 Not Found")
        else:
            order_id, _ = create_paid_order(
                source["email"],
                source["full_name"] or "Cliente",
                source["tax_id"] or "",
                UPSELL_PRICE_CENTS,
                "internal",
                f"upsell_{secrets.token_hex(6)}",
                UPSELL_NAME,
            )
            record_event("upsell_purchase", source["email"], f"source_order={source_order_id}")
            status, headers, body = redirect(f"/thank-you?order={order_id}")
    elif method == "POST" and path == "/automation/run":
        key = (qs.get("key") or [""])[0]
        if AUTOMATION_RUN_KEY and key != AUTOMATION_RUN_KEY:
            status, headers, body = response_json({"ok": False, "error": "unauthorized"}, "401 Unauthorized")
        else:
            sent = _run_pending_followups()
            status, headers, body = response_json({"ok": True, "sent": sent})
    else:
        status, headers, body = not_found()

    start_response(status, headers)
    return [body]


init_db()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    with make_server("0.0.0.0", port, app) as server:
        print(f"Autofinanze MVP escuchando en http://127.0.0.1:{port}")
        server.serve_forever()
