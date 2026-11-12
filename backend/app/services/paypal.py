"""Optional PayPal Sandbox checkout. Credentials are read only from server-side environment settings."""
import base64
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.models import MarketplaceOrder, PayPalSandboxOrder, ShoppingCart, ShoppingCartItem, SimulatedOrder, Transaction
from app.services.marketplace import order_out, release_reserved_stock
from app.services.risk import assess_transaction


def _request(path: str, method: str = "GET", payload: dict | None = None, token: str | None = None):
    settings = get_settings()
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{settings.paypal_base_url.rstrip('/')}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise HTTPException(status_code=502, detail=f"PayPal Sandbox request failed: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=502, detail="Could not reach PayPal Sandbox. Check your connection and try again.") from exc


def enabled() -> bool:
    settings = get_settings()
    return settings.paypal_mode == "sandbox" and bool(settings.paypal_client_id and settings.paypal_client_secret)


def _access_token() -> str:
    settings = get_settings()
    if not enabled():
        raise HTTPException(status_code=503, detail="PayPal Sandbox is not configured. Add PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET to your local .env file.")
    credentials = base64.b64encode(f"{settings.paypal_client_id}:{settings.paypal_client_secret}".encode()).decode()
    request = urllib.request.Request(
        f"{settings.paypal_base_url.rstrip('/')}/v1/oauth2/token",
        data=b"grant_type=client_credentials",
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["access_token"]
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=502, detail="PayPal Sandbox authentication failed. Check the Sandbox app credentials.") from exc
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="Unable to obtain a PayPal Sandbox access token.") from exc


def create_order(db: Session, marketplace_order: MarketplaceOrder, base_url: str) -> dict:
    # Price, currency, description, and buyer identity always come from the
    # persisted marketplace order, never from a browser request body.
    amount = Decimal(str(marketplace_order.total_amount))
    currency = marketplace_order.currency
    description = f"PayPilot marketplace order {marketplace_order.order_number}"
    user_id = marketplace_order.user_id
    token = _access_token()
    data = _request("/v2/checkout/orders", "POST", {
        "intent": "CAPTURE",
        "purchase_units": [{
            "amount": {"currency_code": currency.upper(), "value": f"{amount:.2f}"},
            "description": description[:127],
        }],
        "application_context": {
            "brand_name": "PayPilot AI Sandbox",
            "user_action": "PAY_NOW",
            "return_url": f"{base_url.rstrip('/')}/?paypal_order_return=1",
            "cancel_url": f"{base_url.rstrip('/')}/?paypal_order_cancel=1",
        },
    }, token)
    approval = next((link["href"] for link in data.get("links", []) if link.get("rel") == "approve"), None)
    if not data.get("id") or not approval:
        raise HTTPException(status_code=502, detail="PayPal did not return an approval link for this Sandbox order.")
    db.add(PayPalSandboxOrder(
        paypal_order_id=data["id"],
        amount=amount,
        currency=currency.upper(),
        description=description,
        status="created",
        user_id=user_id,
        marketplace_order_id=marketplace_order.id,
    ))
    marketplace_order.paypal_order_id = data["id"]
    db.commit()
    return {
        "id": data["id"],
        "approval_url": approval,
        "status": data.get("status", "CREATED"),
        "sandbox": True,
        "order": order_out(marketplace_order),
    }


def capture_order(db: Session, paypal_order_id: str, user_id: int) -> dict:
    local = db.scalar(select(PayPalSandboxOrder).where(PayPalSandboxOrder.paypal_order_id == paypal_order_id))
    if local is None:
        raise HTTPException(status_code=404, detail="This PayPal Sandbox order was not created by this PayPilot workspace.")
    if local.user_id != user_id or local.marketplace_order_id is None:
        raise HTTPException(status_code=404, detail="This PayPal Sandbox order was not found for your account.")
    if local.transaction_id:
        tx = db.scalar(select(Transaction).where(Transaction.transaction_id == local.transaction_id))
        order = db.get(MarketplaceOrder, local.marketplace_order_id)
        return {
            "captured": True,
            "transaction_id": tx.transaction_id if tx else local.transaction_id,
            "order_number": order.order_number if order else None,
            "already_captured": True,
            "sandbox": True,
        }
    order = db.scalar(
        select(MarketplaceOrder)
        .where(MarketplaceOrder.id == local.marketplace_order_id, MarketplaceOrder.user_id == user_id)
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Marketplace order not found")
    if order.status != "pending_payment":
        raise HTTPException(status_code=409, detail=f"This order cannot be captured (status: {order.status}).")
    order_expires_at = order.expires_at
    if order_expires_at.tzinfo is None:
        order_expires_at = order_expires_at.replace(tzinfo=timezone.utc)
    if order_expires_at <= datetime.now(timezone.utc):
        order = db.scalar(
            select(MarketplaceOrder)
            .options(selectinload(MarketplaceOrder.items))
            .where(MarketplaceOrder.id == local.marketplace_order_id)
        )
        release_reserved_stock(db, order)
        order.status = "expired"
        local.status = "expired"
        db.commit()
        raise HTTPException(status_code=409, detail="This checkout expired before capture. Your reserved stock has been released; please check out again.")
    token = _access_token()
    data = _request(f"/v2/checkout/orders/{paypal_order_id}/capture", "POST", {}, token)
    capture = (((data.get("purchase_units") or [{}])[0].get("payments") or {}).get("captures") or [{}])[0]
    capture_status = str(capture.get("status", "unknown")).upper()
    if capture_status != "COMPLETED":
        if capture_status in {"DECLINED", "DENIED"}:
            order = db.scalar(select(MarketplaceOrder).options(selectinload(MarketplaceOrder.items)).where(MarketplaceOrder.id == local.marketplace_order_id))
            if order is not None and order.status == "pending_payment":
                release_reserved_stock(db, order)
                order.status = "payment_failed"
                local.status = "declined" if capture_status == "DECLINED" else "denied"
                db.commit()
        elif capture_status in {"VOIDED", "CANCELLED"}:
            order = db.scalar(select(MarketplaceOrder).options(selectinload(MarketplaceOrder.items)).where(MarketplaceOrder.id == local.marketplace_order_id))
            if order is not None and order.status == "pending_payment":
                release_reserved_stock(db, order)
                order.status = "cancelled"
                local.status = "cancelled"
                db.commit()
        elif capture_status in {"PENDING", "PAYER_ACTION_REQUIRED"}:
            local.status = "pending"
            db.commit()
        raise HTTPException(status_code=409, detail=f"PayPal Sandbox capture is not completed (status: {capture_status}).")
    captured_amount = (capture.get("amount") or {})
    try:
        amount_matches = round(float(captured_amount.get("value", -1)), 2) == round(local.amount, 2)
    except (TypeError, ValueError):
        amount_matches = False
    if not amount_matches or captured_amount.get("currency_code", "").upper() != local.currency.upper():
        raise HTTPException(status_code=409, detail="Captured Sandbox amount or currency does not match the local order. No transaction was recorded.")
    payer = data.get("payer") or {}
    name = payer.get("name") or {}
    customer_name = " ".join(part for part in [name.get("given_name"), name.get("surname")] if part) or "Sandbox Customer"
    email = payer.get("email_address") or "sandbox-customer@example.test"
    simulated_order_id = f"ord_paypal_{paypal_order_id[:24]}"
    simulated_order = SimulatedOrder(order_id=simulated_order_id, amount=local.amount, currency=local.currency, description=local.description, status="completed")
    db.add(simulated_order)
    db.flush()
    tx = Transaction(
        transaction_id=f"txn_paypal_{capture.get('id', paypal_order_id)[-20:]}",
        order_id=simulated_order_id,
        customer_name=customer_name[:120],
        customer_email=email[:160],
        amount=local.amount,
        currency=local.currency,
        status="successful",
        payment_method="PayPal Sandbox",
        description=local.description,
    )
    db.add(tx)
    db.flush()
    risk = assess_transaction(db, tx)
    local.status = "captured"
    local.transaction_id = tx.transaction_id
    order.status = "paid"
    order.transaction_id = tx.transaction_id
    order.paid_at = datetime.now(timezone.utc)
    cart_id = db.scalar(select(ShoppingCart.id).where(ShoppingCart.user_id == user_id))
    if cart_id is not None:
        db.execute(delete(ShoppingCartItem).where(ShoppingCartItem.cart_id == cart_id))
    db.commit()
    return {
        "captured": True,
        "transaction_id": tx.transaction_id,
        "order_number": order.order_number,
        "capture_id": capture.get("id"),
        "risk_level": risk.level,
        "sandbox": True,
    }


def cancel_order(db: Session, paypal_order_id: str, user_id: int) -> dict:
    local = db.scalar(select(PayPalSandboxOrder).where(PayPalSandboxOrder.paypal_order_id == paypal_order_id))
    if local is None or local.user_id != user_id or local.marketplace_order_id is None:
        raise HTTPException(status_code=404, detail="This PayPal Sandbox order was not found for your account.")
    if local.transaction_id:
        raise HTTPException(status_code=409, detail="A captured order cannot be cancelled here.")
    order = db.scalar(
        select(MarketplaceOrder)
        .options(selectinload(MarketplaceOrder.items))
        .where(MarketplaceOrder.id == local.marketplace_order_id, MarketplaceOrder.user_id == user_id)
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Marketplace order not found")
    if order.status == "pending_payment":
        release_reserved_stock(db, order)
        order.status = "cancelled"
        local.status = "cancelled"
        db.commit()
    return {"cancelled": True, "order_number": order.order_number}


def verify_order(db: Session, paypal_order_id: str, user_id: int) -> dict:
    """Read provider state for an order already linked to this customer's cart."""
    local = db.scalar(select(PayPalSandboxOrder).where(PayPalSandboxOrder.paypal_order_id == paypal_order_id))
    if local is None or local.user_id != user_id or local.marketplace_order_id is None:
        raise HTTPException(status_code=404, detail="This PayPal Sandbox order was not found for your account.")
    token = _access_token()
    data = _request(f"/v2/checkout/orders/{paypal_order_id}", "GET", token=token)
    units = data.get("purchase_units") or []
    amount = (units[0].get("amount") or {}) if units else {}
    try:
        amount_matches = round(float(amount.get("value", -1)), 2) == round(float(local.amount), 2)
    except (TypeError, ValueError):
        amount_matches = False
    currency_matches = amount.get("currency_code", "").upper() == local.currency.upper()
    return {
        "paypal_order_id": paypal_order_id,
        "provider_status": str(data.get("status", "UNKNOWN")),
        "local_status": local.status,
        "amount_matches": amount_matches,
        "currency_matches": currency_matches,
        "verified": bool(data.get("id") == paypal_order_id and amount_matches and currency_matches),
        "sandbox": True,
    }
