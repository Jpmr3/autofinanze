# autofinanze

MVP de monetización en español para vender una oferta digital con:

- landing de conversión y captura de leads
- checkout inmediato (modo interno o Stripe Payment Link)
- confirmación de pago
- entrega automática con enlace de acceso
- facturación básica (recibo/factura HTML)
- panel de métricas mínimas
- embudo corto con upsell
- automatización simple de seguimiento

## Ejecutar

```bash
cd /home/runner/work/autofinanze/autofinanze
python app.py
```

Servidor por defecto en `http://127.0.0.1:8000`.

## Variables de entorno

- `PORT` (opcional, por defecto `8000`)
- `AUTOFINANZE_DB` (opcional, por defecto `autofinanze.db`)
- `AUTOFINANZE_BASE_URL` (opcional, por defecto `http://127.0.0.1:8000`)
- `OFFER_NAME` (opcional, por defecto `Sistema Ingresos Rápidos MVP`)
- `OFFER_PRICE_CENTS` (opcional, por defecto `4900`)
- `STRIPE_PAYMENT_LINK` (opcional, si existe usa checkout externo)
- `AUTOMATION_RUN_KEY` (opcional, protege `POST /automation/run`)

## Endpoints principales

- `GET /` landing + formulario lead + CTA de compra
- `POST /lead` guarda lead
- `GET /checkout` checkout
- `POST /pay` pago inmediato interno
- `POST /webhooks/stripe` confirmación de pago por webhook Stripe (JSON)
- `GET /thank-you?order=<id>` confirmación de compra
- `GET /invoice/<id>` factura HTML
- `GET /access/<token>` entrega del producto
- `GET /dashboard` métricas
- `GET /upsell?order=<id>` upsell
- `POST /upsell/buy` compra de upsell
- `POST /automation/run?key=<AUTOMATION_RUN_KEY>` ejecuta seguimientos pendientes
