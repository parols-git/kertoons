"""
Selling image-generation credits via Stripe Checkout - a Stripe-*hosted*
payment page, so this app never receives, sees, or stores a card number
(the "Add credits" button redirects the browser to Stripe's own domain for
the actual payment; Stripe redirects back afterward).

Calls Stripe's REST API directly with `requests` (same pattern as
openai_client.py/gemini_client.py/image_client.py) rather than adding the
`stripe` Python SDK as a new dependency.

Two independent paths both end up crediting the account, each safe to run
even if the other also fires (see db.grant_credits_for_payment - keyed by
Stripe's own checkout session id, so a session is only ever credited once):
  1. server.py's /api/stripe/webhook - Stripe calls this asynchronously
     once a payment completes; the durable source of truth.
  2. server.py's /api/credits/confirm - called when the user's browser
     itself returns to the success_url; gives immediate UI feedback without
     waiting on the webhook, by asking Stripe directly whether that
     specific session was actually paid.
"""
import hmac
import time
import json
import hashlib

import requests

from . import config

STRIPE_API_BASE = "https://api.stripe.com/v1"

# The one credit pack this app sells. A real storefront might offer several
# tiers; kept to one for simplicity, matching the rest of this app's scope.
CREDIT_PACK_CREDITS = 50
CREDIT_PACK_PRICE_USD_CENTS = 500  # $5.00
CREDIT_PACK_LABEL = f"{CREDIT_PACK_CREDITS} Kertoons image credits"


class PaymentError(Exception):
    pass


def _auth():
    return (config.STRIPE_SECRET_KEY, "")


def create_checkout_session(success_url: str, cancel_url: str, client_reference_id: str) -> str:
    """Creates a Stripe Checkout Session for one credit pack and returns the
    hosted checkout page URL to redirect the browser to. `client_reference_id`
    is the app's own user id (as a string) - Stripe echoes it back on both
    the webhook event and the session-retrieve response, which is how a
    completed payment gets tied back to the right account without Stripe
    needing to know anything about this app's own login/session system.

    Stripe's REST API takes form-encoded bodies with bracket notation for
    nested/array fields (not JSON) - this is normal for their API, not a
    workaround."""
    data = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": client_reference_id,
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": str(CREDIT_PACK_PRICE_USD_CENTS),
        "line_items[0][price_data][product_data][name]": CREDIT_PACK_LABEL,
    }
    resp = requests.post(f"{STRIPE_API_BASE}/checkout/sessions", data=data, auth=_auth(), timeout=30)
    if resp.status_code != 200:
        raise PaymentError(f"Stripe API error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["url"]


def retrieve_checkout_session(session_id: str) -> dict:
    """Looks up a Checkout Session directly (used by /api/credits/confirm
    when the user's own browser returns from Stripe, so the UI can confirm
    payment immediately rather than waiting on the webhook)."""
    resp = requests.get(f"{STRIPE_API_BASE}/checkout/sessions/{session_id}", auth=_auth(), timeout=30)
    if resp.status_code != 200:
        raise PaymentError(f"Stripe API error {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def verify_webhook_signature(payload: bytes, sig_header: str, tolerance_seconds: int = 300) -> dict:
    """Verifies a Stripe webhook's `Stripe-Signature` header (Stripe's
    documented scheme: HMAC-SHA256 of "{timestamp}.{raw body}" using the
    webhook signing secret) and returns the parsed event dict if valid.
    Raises PaymentError otherwise - this is what stops anyone who isn't
    Stripe from POSTing a fake "payment succeeded" event to grant
    themselves free credits, so it is not optional/best-effort."""
    if not config.STRIPE_WEBHOOK_SECRET:
        # Fail closed. Without this check, an unset secret would make
        # hmac.new() sign against b"" - a key anyone can guess is empty
        # simply by knowing it's unconfigured - so an "unconfigured" webhook
        # would actually accept ANY forged signature computed the same way,
        # rather than rejecting all of them as intended.
        raise PaymentError("STRIPE_WEBHOOK_SECRET is not configured")
    if not sig_header:
        raise PaymentError("missing Stripe-Signature header")

    parts = {}
    for item in sig_header.split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            parts[k] = v
    timestamp = parts.get("t")
    signature = parts.get("v1")
    if not timestamp or not signature:
        raise PaymentError("malformed Stripe-Signature header")

    try:
        timestamp_int = int(timestamp)
    except ValueError:
        raise PaymentError("non-numeric timestamp in Stripe-Signature header")
    if abs(time.time() - timestamp_int) > tolerance_seconds:
        raise PaymentError("webhook timestamp outside tolerance - possible replay")

    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(
        config.STRIPE_WEBHOOK_SECRET.encode("utf-8"), signed_payload, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise PaymentError("signature mismatch")

    return json.loads(payload)
