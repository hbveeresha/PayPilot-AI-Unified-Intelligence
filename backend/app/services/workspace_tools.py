"""Allowlisted, role-scoped read tools for local PayPilot workspace intelligence.

No tool accepts SQL, arbitrary model-selected columns, or unbounded record counts.
Financial values are always calculated by the backend from SQLite records.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.models import (
    AssistantConversation,
    MarketplaceOrder,
    MarketplaceOrderItem,
    MarketplaceUser,
    PayPalSandboxOrder,
    Product,
    ProductCategory,
    RiskAssessment,
    ShoppingCart,
    ShoppingCartItem,
    Transaction,
)
from app.services import paypal
from app.services.marketplace import cart_summary, create_marketplace_order, expire_stale_orders, product_image_url
from app.services.permissions import WorkspaceActor, order_scope, transaction_scope


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyArgs(_Args):
    pass


class SummaryArgs(_Args):
    period: Literal["all", "today", "yesterday", "week", "month"] = "all"
    status: Literal["successful", "pending", "failed"] | None = None
    date_from: date | None = None
    date_to: date | None = None


class TransactionArgs(_Args):
    transaction_id: str | None = Field(default=None, max_length=40, pattern=r"^(txn_[a-zA-Z0-9_-]+)?$")
    status: Literal["successful", "pending", "failed"] | None = None
    customer_prefix: str | None = Field(default=None, max_length=60)
    payment_method: str | None = Field(default=None, max_length=40)
    period: Literal["all", "today", "yesterday", "week", "month"] = "all"
    date_from: date | None = None
    date_to: date | None = None
    min_amount: float | None = Field(default=None, ge=0, le=1_000_000)
    max_amount: float | None = Field(default=None, ge=0, le=1_000_000)
    limit: int = Field(default=10, ge=1, le=30)


class ProductArgs(_Args):
    query: str | None = Field(default=None, max_length=120)
    name_prefix: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=96, pattern=r"^[a-z0-9-]+$")
    min_price: float | None = Field(default=None, ge=0, le=100_000)
    max_price: float | None = Field(default=None, ge=0, le=100_000)
    available_only: bool = False
    low_stock_only: bool = False
    out_of_stock_only: bool = False
    limit: int = Field(default=10, ge=1, le=30)


class CustomerArgs(_Args):
    customer_id: int | None = Field(default=None, ge=1)
    name_prefix: str | None = Field(default=None, min_length=1, max_length=80)
    name_query: str | None = Field(default=None, min_length=1, max_length=120)
    limit: int = Field(default=10, ge=1, le=30)


class OrderArgs(_Args):
    order_number: str | None = Field(default=None, max_length=40)
    status: str | None = Field(default=None, max_length=30)
    customer_id: int | None = Field(default=None, ge=1)
    limit: int = Field(default=10, ge=1, le=30)


class PaymentStatusArgs(_Args):
    order_number: str = Field(min_length=3, max_length=40)


class PayPalOrderArgs(_Args):
    paypal_order_id: str = Field(min_length=5, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class ToolSpec:
    def __init__(self, description: str, model: type[BaseModel], roles: set[str], source: str, side_effect: bool = False):
        self.description = description
        self.model = model
        self.roles = roles
        self.source = source
        self.side_effect = side_effect

    def ollama_schema(self, name: str) -> dict:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": self.description,
                "parameters": self.model.model_json_schema(),
            },
        }


_ALL = {"admin", "customer", "merchant"}
_ADMIN = {"admin"}
_CUSTOMER = {"customer"}
_MERCHANT = {"merchant"}
_CATALOG = {"admin", "customer", "merchant", "guest"}
TOOL_SPECS: dict[str, ToolSpec] = {
    "get_transaction_summary": ToolSpec("Count local transactions and calculate status totals, averages, and volume by currency. Period may be all, today, yesterday, week, or month.", SummaryArgs, _ALL, "Transactions database"),
    "search_transactions": ToolSpec("Search authorized local transaction records by status, date period, customer name prefix, payment method, amount range, or transaction id.", TransactionArgs, _ALL, "Transactions database"),
    "get_transaction_details": ToolSpec("Explain one authorized transaction and its stored risk assessment. Requires a transaction_id.", TransactionArgs, _ALL, "Transactions database"),
    "get_transaction_statistics": ToolSpec("Calculate local transaction statistics by currency and status.", SummaryArgs, _ALL, "Transactions database"),
    "search_products": ToolSpec("Search public active catalogue products by query, category, price range, availability, or low stock.", ProductArgs, _CATALOG, "Marketplace database"),
    "get_product_details": ToolSpec("Retrieve a product by its exact slug or unique name query.", ProductArgs, _CATALOG, "Marketplace database"),
    "get_inventory_summary": ToolSpec("Calculate product and inventory counts, including unavailable and low-stock items. Merchants see only their inventory.", ProductArgs, _ALL, "Marketplace database"),
    "get_marketplace_summary": ToolSpec("Summarize catalogue, order states, and marketplace order values without mixing them with transaction volume.", SummaryArgs, _ALL, "Marketplace database"),
    "search_customers": ToolSpec("Search authorized customer account and local simulated-transaction customer records by first name, full name, partial name, or name prefix. Administrator only. Never return credentials.", CustomerArgs, _ADMIN, "Customer database"),
    "get_customer_orders": ToolSpec("Retrieve the signed-in customer's own order history, or administrator-authorized order history for a customer_id.", OrderArgs, {"admin", "customer"}, "Marketplace database"),
    "get_order_details": ToolSpec("Retrieve a permitted order by order number, including item quantities and local payment status.", OrderArgs, _ALL, "Marketplace database"),
    "get_merchant_summary": ToolSpec("Summarize an authenticated merchant's own catalogue, inventory, and paid sales; administrators may request workspace-wide totals.", SummaryArgs, {"admin", "merchant"}, "Marketplace database"),
    "get_cart_summary": ToolSpec("Read the signed-in customer's current cart with backend-calculated subtotal, tax, shipping, and total. Administrator receives aggregate cart counts only.", EmptyArgs, {"admin", "customer"}, "Marketplace database"),
    "get_fraud_summary": ToolSpec("Summarize stored risk assessments by risk level. Risk scores are review signals, not proof of fraud.", SummaryArgs, _ALL, "Fraud Intelligence"),
    "search_high_risk_transactions": ToolSpec("List permitted transactions with high or medium recorded risk assessments and their stored reasons.", TransactionArgs, _ALL, "Fraud Intelligence"),
    "get_risk_assessment": ToolSpec("Retrieve the stored risk score, level, reasons, and recommendation for one permitted transaction_id.", TransactionArgs, _ALL, "Fraud Intelligence"),
    "get_workspace_summary": ToolSpec("Combine authorized local transaction, marketplace, and fraud summaries. Keep each monetary measure separate.", EmptyArgs, _ADMIN, "Analytics"),
    "get_sales_analytics": ToolSpec("Analyze paid marketplace order items and product quantities. Merchant access is restricted to that merchant's own products.", SummaryArgs, {"admin", "merchant"}, "Analytics"),
    "get_payment_analytics": ToolSpec("Summarize authorized local payment transaction statuses and daily activity.", SummaryArgs, _ALL, "Analytics"),
    "generate_business_report": ToolSpec("Generate an authorized local business report with separate transaction volume, order value, and risk signals.", SummaryArgs, _ALL, "Analytics"),
    "get_paypal_configuration_status": ToolSpec("Report whether server-side PayPal Sandbox credentials are configured. Never return credentials or secret values.", EmptyArgs, _ALL, "PayPal Sandbox"),
    "get_payment_status": ToolSpec("Read locally recorded payment and order state by order number. This is not a live PayPal verification.", PaymentStatusArgs, _ALL, "PayPal Sandbox"),
    "create_paypal_sandbox_order": ToolSpec("Explicitly create a PayPal Sandbox checkout for the signed-in customer's validated cart. This reserves stock and returns an approval link.", EmptyArgs, _CUSTOMER, "PayPal Sandbox", side_effect=True),
    "capture_paypal_sandbox_payment": ToolSpec("Explicitly capture a signed-in customer's own PayPal Sandbox order by its ID. The backend verifies amount and currency.", PayPalOrderArgs, _CUSTOMER, "PayPal Sandbox", side_effect=True),
    "verify_paypal_payment": ToolSpec("Verify the signed-in customer's own PayPal Sandbox order directly with the provider and compare its recorded amount and currency.", PayPalOrderArgs, _CUSTOMER, "PayPal Sandbox", side_effect=True),
}


def tool_schemas(actor: WorkspaceActor) -> list[dict]:
    if actor.role == "guest":
        return []
    return [spec.ollama_schema(name) for name, spec in TOOL_SPECS.items() if actor.role in spec.roles and not spec.side_effect]


def _period_bounds(period: str) -> tuple[date | None, date | None]:
    today = date.today()
    if period == "today":
        return today, today
    if period == "yesterday":
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday
    if period == "week":
        return today - timedelta(days=6), today
    if period == "month":
        return today.replace(day=1), today
    return None, None


def _with_period(query, period: str, model_column=Transaction.created_at):
    start, end = _period_bounds(period)
    if start:
        query = query.where(model_column >= datetime.combine(start, datetime.min.time(), timezone.utc))
    if end:
        query = query.where(model_column < datetime.combine(end + timedelta(days=1), datetime.min.time(), timezone.utc))
    return query


def _risk_for(db: Session, transaction_id: str) -> dict:
    row = db.scalar(select(RiskAssessment).where(RiskAssessment.transaction_id == transaction_id).order_by(RiskAssessment.created_at.desc(), RiskAssessment.id.desc()))
    if row is None:
        return {"score": 0, "level": "low", "reasons": [], "recommendation": "No risk assessment is stored."}
    try:
        reasons = json.loads(row.reasons_json or "[]")
    except (TypeError, json.JSONDecodeError):
        reasons = []
    return {"score": row.score, "level": row.level, "reasons": reasons, "recommendation": row.recommendation}


def _tx_row(db: Session, tx: Transaction) -> dict:
    risk = _risk_for(db, tx.transaction_id)
    return {
        "transaction_id": tx.transaction_id,
        "status": tx.status,
        "amount": round(float(tx.amount), 2),
        "currency": tx.currency,
        "payment_method": tx.payment_method,
        "created_at": tx.created_at.isoformat(),
        "risk_score": risk["score"],
        "risk_level": risk["level"],
        "risk_reasons": risk["reasons"],
        "risk_recommendation": risk["recommendation"],
        "simulated": not db.scalar(select(PayPalSandboxOrder.id).where(PayPalSandboxOrder.transaction_id == tx.transaction_id).limit(1)),
    }


def _artifact(source: str, title: str, rows: list[dict], summary: str, kind: str = "table") -> dict:
    return {"source": source, "title": title, "summary": summary, "kind": kind, "rows": rows}


def _transaction_query(db: Session, actor: WorkspaceActor):
    query = select(Transaction).options(selectinload(Transaction.risk_assessments))
    return transaction_scope(query, actor)


def _summary_args(args: dict) -> SummaryArgs:
    return SummaryArgs.model_validate(args)


def _transaction_summary(db: Session, actor: WorkspaceActor, args: dict) -> dict:
    period = args.get("period", "all")
    status_filter = args.get("status")
    base = transaction_scope(select(Transaction), actor)
    base = _with_period(base, period)
    if status_filter:
        base = base.where(Transaction.status == status_filter)
    if args.get("date_from"):
        base = base.where(Transaction.created_at >= datetime.combine(args["date_from"], datetime.min.time(), timezone.utc))
    if args.get("date_to"):
        base = base.where(Transaction.created_at < datetime.combine(args["date_to"] + timedelta(days=1), datetime.min.time(), timezone.utc))

    aggregate = base.with_only_columns(
        Transaction.currency,
        Transaction.status,
        func.count(Transaction.id),
        func.sum(Transaction.amount),
        func.avg(Transaction.amount),
    ).order_by(None).group_by(Transaction.currency, Transaction.status)
    by_currency: dict[str, dict] = {}
    status_counts = {key: 0 for key in ("successful", "pending", "failed")}
    status_volume: dict[str, dict[str, float]] = {key: {} for key in status_counts}
    total_count = 0
    for currency, status, count, volume, average in db.execute(aggregate):
        count = int(count or 0)
        total_count += count
        if status in status_counts:
            status_counts[status] += count
            status_volume[status][currency] = round(float(volume or 0), 2)
        current = by_currency.setdefault(currency, {"volume": 0.0, "average": 0.0, "count": 0})
        current["volume"] += float(volume or 0)
        current["count"] += count
    for current in by_currency.values():
        current["volume"] = round(current["volume"], 2)
        current["average"] = round(current["volume"] / current["count"], 2) if current["count"] else 0.0

    method_query = base.with_only_columns(
        Transaction.payment_method,
        func.count(Transaction.id),
        func.sum(Transaction.amount),
    ).order_by(None).group_by(Transaction.payment_method).order_by(func.count(Transaction.id).desc())
    methods = [{"payment_method": method, "count": int(count or 0), "volume": round(float(volume or 0), 2)}
               for method, count, volume in db.execute(method_query)]

    today = date.today()
    first_day = today - timedelta(days=13)
    trend_query = transaction_scope(select(Transaction), actor).where(
        Transaction.created_at >= datetime.combine(first_day, datetime.min.time(), timezone.utc),
        Transaction.created_at < datetime.combine(today + timedelta(days=1), datetime.min.time(), timezone.utc),
    )
    trend_rows = db.execute(trend_query.with_only_columns(
        func.date(Transaction.created_at), Transaction.currency, func.count(Transaction.id), func.sum(Transaction.amount)
    ).order_by(None).group_by(func.date(Transaction.created_at), Transaction.currency))
    daily_map = { (today - timedelta(days=offset)).isoformat(): {"date": (today - timedelta(days=offset)).isoformat(), "count": 0, "volume_by_currency": {}} for offset in range(13, -1, -1) }
    for day, currency, count, volume in trend_rows:
        if day in daily_map:
            daily_map[day]["count"] += int(count or 0)
            daily_map[day]["volume_by_currency"][currency] = round(float(volume or 0), 2)

    result = {
        "period": period,
        "transaction_count": total_count,
        "statuses": status_counts,
        "volume_by_currency": by_currency,
        "successful_volume_by_currency": status_volume["successful"],
        "pending_volume_by_currency": status_volume["pending"],
        "failed_volume_by_currency": status_volume["failed"],
        "success_rate": round(status_counts["successful"] / total_count * 100, 1) if total_count else 0.0,
        "average_by_currency": {currency: values["average"] for currency, values in by_currency.items()},
        "payment_methods": methods,
        "daily_activity": list(daily_map.values()),
        "source": "Transactions database",
    }
    rows = [{"currency": currency, **values} for currency, values in sorted(by_currency.items())]
    result["artifact"] = _artifact(
        "Transactions database", "Transaction summary", rows,
        f"{total_count} local transaction record(s). Status counts: {status_counts}. Success rate: {result['success_rate']:.1f}%.",
    )
    return result


def _sum_currencies(rows: list[Transaction], status: str | None = None) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in rows:
        if status is None or row.status == status:
            totals[row.currency] = totals.get(row.currency, 0.0) + float(row.amount)
    return {currency: round(total, 2) for currency, total in sorted(totals.items())}


def _search_transactions(db: Session, actor: WorkspaceActor, args: dict) -> dict:
    parsed = TransactionArgs.model_validate(args)
    query = _transaction_query(db, actor)
    query = _with_period(query, parsed.period)
    if parsed.date_from:
        query = query.where(Transaction.created_at >= datetime.combine(parsed.date_from, datetime.min.time(), timezone.utc))
    if parsed.date_to:
        query = query.where(Transaction.created_at < datetime.combine(parsed.date_to + timedelta(days=1), datetime.min.time(), timezone.utc))
    if parsed.transaction_id:
        query = query.where(Transaction.transaction_id == parsed.transaction_id)
    if parsed.status:
        query = query.where(Transaction.status == parsed.status)
    if parsed.customer_prefix:
        query = query.where(Transaction.customer_name.ilike(f"{parsed.customer_prefix}%"))
    if parsed.payment_method:
        query = query.where(Transaction.payment_method.ilike(f"%{parsed.payment_method}%"))
    if parsed.min_amount is not None:
        query = query.where(Transaction.amount >= parsed.min_amount)
    if parsed.max_amount is not None:
        query = query.where(Transaction.amount <= parsed.max_amount)
    rows = list(db.scalars(query.order_by(Transaction.created_at.desc()).limit(parsed.limit)))
    safe_rows = [_tx_row(db, row) for row in rows]
    return {"source": "Transactions database", "rows": safe_rows, "filters": {"status": parsed.status, "period": parsed.period, "date_from": parsed.date_from.isoformat() if parsed.date_from else None, "date_to": parsed.date_to.isoformat() if parsed.date_to else None, "min_amount": parsed.min_amount, "max_amount": parsed.max_amount}, "artifact": _artifact("Transactions database", "Matching transactions", safe_rows, f"Found {len(safe_rows)} matching local transaction record(s).")}


def _product_rows(db: Session, actor: WorkspaceActor, args: dict) -> list[dict]:
    parsed = ProductArgs.model_validate(args)
    query = select(Product).options(selectinload(Product.category), selectinload(Product.merchant)).where(Product.is_active.is_(True))
    if actor.role == "merchant" and actor.user_id is not None:
        query = query.where(Product.merchant_id == actor.user_id)
    if parsed.query:
        term = f"%{parsed.query.strip()}%"
        query = query.where(Product.name.ilike(term) | Product.description.ilike(term) | Product.slug.ilike(term))
    if parsed.name_prefix:
        query = query.where(Product.name.ilike(f"{parsed.name_prefix}%"))
    if parsed.category:
        query = query.join(ProductCategory).where(ProductCategory.slug == parsed.category)
    if parsed.min_price is not None:
        query = query.where(Product.price >= Decimal(str(parsed.min_price)))
    if parsed.max_price is not None:
        query = query.where(Product.price <= Decimal(str(parsed.max_price)))
    if parsed.available_only:
        query = query.where(Product.stock_quantity > 0)
    if parsed.low_stock_only:
        query = query.where(Product.stock_quantity <= 5)
    if parsed.out_of_stock_only:
        query = query.where(Product.stock_quantity == 0)
    products = list(db.scalars(query.order_by(Product.rating.desc(), Product.name).limit(parsed.limit)))
    return [{
        "product_id": product.id,
        "name": product.name,
        "description": product.description,
        "category": product.category.name,
        "price": float(product.price),
        "currency": product.currency,
        "available": product.is_active and product.stock_quantity > 0,
        "stock_quantity": product.stock_quantity,
        "merchant_name": product.merchant.full_name if actor.role == "admin" else None,
        "rating": float(product.rating),
        "image_url": product_image_url(product),
        "slug": product.slug,
    } for product in products]


def _order_rows(db: Session, actor: WorkspaceActor, args: dict, detail: bool = False) -> list[dict]:
    parsed = OrderArgs.model_validate(args)
    query = select(MarketplaceOrder).options(
        selectinload(MarketplaceOrder.items).selectinload(MarketplaceOrderItem.product),
        selectinload(MarketplaceOrder.user),
    )
    query = order_scope(query, actor)
    if actor.role == "admin" and parsed.customer_id is not None:
        query = query.where(MarketplaceOrder.user_id == parsed.customer_id)
    if parsed.order_number:
        query = query.where(MarketplaceOrder.order_number == parsed.order_number)
    if parsed.status:
        query = query.where(MarketplaceOrder.status == parsed.status)
    orders = list(db.scalars(query.order_by(MarketplaceOrder.created_at.desc()).limit(1 if detail else parsed.limit)))
    result = []
    for order in orders:
        items = [item for item in order.items if actor.role != "merchant" or (item.product and item.product.merchant_id == actor.user_id)]
        subtotal = round(sum(float(item.line_total) for item in items), 2)
        result.append({
            "order_number": order.order_number,
            "status": order.status,
            "created_at": order.created_at.isoformat(),
            "currency": order.currency,
            "subtotal": subtotal if actor.role == "merchant" else float(order.subtotal),
            "tax": None if actor.role == "merchant" else float(order.tax_amount),
            "shipping": None if actor.role == "merchant" else float(order.shipping_amount),
            "total": None if actor.role == "merchant" else float(order.total_amount),
            "paid_at": order.paid_at.isoformat() if order.paid_at else None,
            "transaction_id": order.transaction_id,
            "customer": order.user.full_name if actor.role == "admin" and order.user else None,
            "items": [{
                "product_name": item.product_name,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price),
                "line_total": float(item.line_total),
                "image_url": product_image_url(item.product, item.image_path),
            } for item in items],
        })
    return result


def _merchant_summary(db: Session, merchant_id: int | None) -> dict:
    inventory = select(
        func.count(Product.id),
        func.coalesce(func.sum(case((Product.stock_quantity > 0, 1), else_=0)), 0),
        func.coalesce(func.sum(case(((Product.stock_quantity > 0) & (Product.stock_quantity <= 5), 1), else_=0)), 0),
        func.coalesce(func.sum(case((Product.stock_quantity == 0, 1), else_=0)), 0),
        func.coalesce(func.sum(Product.stock_quantity), 0),
    ).where(Product.is_active.is_(True))
    if merchant_id is not None:
        inventory = inventory.where(Product.merchant_id == merchant_id)
    product_count, available, low_stock, out_of_stock, units_in_stock = db.execute(inventory).one()

    item_query = select(
        MarketplaceOrderItem.product_id,
        Product.name,
        ProductCategory.name,
        MarketplaceOrder.currency,
        func.sum(MarketplaceOrderItem.quantity),
        func.sum(MarketplaceOrderItem.line_total),
    ).join(Product, MarketplaceOrderItem.product_id == Product.id).join(
        ProductCategory, ProductCategory.id == Product.category_id
    ).join(MarketplaceOrder, MarketplaceOrder.id == MarketplaceOrderItem.order_id).where(
        MarketplaceOrder.status == "paid"
    )
    order_count_query = select(func.count(func.distinct(MarketplaceOrder.id))).join(
        MarketplaceOrderItem, MarketplaceOrderItem.order_id == MarketplaceOrder.id
    ).join(Product, Product.id == MarketplaceOrderItem.product_id).where(MarketplaceOrder.status == "paid")
    if merchant_id is not None:
        item_query = item_query.where(Product.merchant_id == merchant_id)
        order_count_query = order_count_query.where(Product.merchant_id == merchant_id)
    item_rows = db.execute(item_query.group_by(
        MarketplaceOrderItem.product_id, Product.name, ProductCategory.name, MarketplaceOrder.currency
    )).all()
    paid_order_count = int(db.scalar(order_count_query) or 0)
    sales_by_currency: dict[str, float] = {}
    category_totals: dict[tuple[str, str], dict] = {}
    best_products = []
    units_sold = 0
    for product_id, product_name, category, currency, quantity, sales in item_rows:
        quantity = int(quantity or 0)
        sales = float(sales or 0)
        units_sold += quantity
        sales_by_currency[currency] = sales_by_currency.get(currency, 0.0) + sales
        category_row = category_totals.setdefault((category, currency), {"category": category, "currency": currency, "units_sold": 0, "paid_sales": 0.0})
        category_row["units_sold"] += quantity
        category_row["paid_sales"] += sales
        best_products.append({"product_id": product_id, "product_name": product_name, "category": category, "currency": currency, "units_sold": quantity, "paid_sales": round(sales, 2)})
    categories = sorted(
        ({**row, "paid_sales": round(row["paid_sales"], 2)} for row in category_totals.values()),
        key=lambda row: (-row["paid_sales"], row["category"]),
    )
    best_products.sort(key=lambda row: (-row["units_sold"], -row["paid_sales"], row["product_name"]))
    return {
        "product_count": int(product_count or 0),
        "available_count": int(available or 0),
        "low_stock_count": int(low_stock or 0),
        "out_of_stock_count": int(out_of_stock or 0),
        "units_in_stock": int(units_in_stock or 0),
        "paid_order_count": paid_order_count,
        "units_sold": units_sold,
        "paid_sales_by_currency": {currency: round(total, 2) for currency, total in sorted(sales_by_currency.items())},
        "paid_sales_by_category": categories,
        "best_selling_products": best_products[:10],
    }


def _sum_order_items(items: list[MarketplaceOrderItem]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for item in items:
        currency = item.order.currency if item.order else "USD"
        totals[currency] = totals.get(currency, 0.0) + float(item.line_total)
    return {currency: round(total, 2) for currency, total in sorted(totals.items())}


def _marketplace_summary(db: Session, actor: WorkspaceActor, args: dict) -> dict:
    if actor.role == "merchant":
        values = _merchant_summary(db, actor.user_id)
        return {"source": "Marketplace database", **values, "artifact": _artifact("Marketplace database", "Your merchant summary", [values], "Merchant statistics are limited to your own products and paid order items.")}
    if actor.role == "customer":
        status_counts = {status: int(count or 0) for status, count in db.execute(
            select(MarketplaceOrder.status, func.count(MarketplaceOrder.id))
            .where(MarketplaceOrder.user_id == actor.user_id)
            .group_by(MarketplaceOrder.status)
        )}
        value_rows = db.execute(
            select(MarketplaceOrder.currency, func.sum(MarketplaceOrder.total_amount))
            .where(MarketplaceOrder.user_id == actor.user_id)
            .group_by(MarketplaceOrder.currency)
        )
        totals = {currency: round(float(total or 0), 2) for currency, total in value_rows}
        paid_rows = db.execute(
            select(MarketplaceOrder.currency, func.sum(MarketplaceOrder.total_amount))
            .where(MarketplaceOrder.user_id == actor.user_id, MarketplaceOrder.status == "paid")
            .group_by(MarketplaceOrder.currency)
        )
        paid_totals = {currency: round(float(total or 0), 2) for currency, total in paid_rows}
        order_count = sum(status_counts.values())
        sample = _order_rows(db, actor, {"limit": 10})
        result = {"order_count": order_count, "orders_by_status": status_counts, "order_value_by_currency": totals, "paid_order_value_by_currency": paid_totals, "source": "Marketplace database"}
        return {**result, "artifact": _artifact("Marketplace database", "Your marketplace summary", sample, f"Your account has {order_count} marketplace order(s).")}

    counts = {"products": db.scalar(select(func.count(Product.id)).where(Product.is_active.is_(True))) or 0,
              "customers": db.scalar(select(func.count(MarketplaceUser.id)).where(MarketplaceUser.role == "customer", MarketplaceUser.is_active.is_(True))) or 0,
              "merchants": db.scalar(select(func.count(MarketplaceUser.id)).where(MarketplaceUser.role == "merchant", MarketplaceUser.is_active.is_(True))) or 0,
              "orders": db.scalar(select(func.count(MarketplaceOrder.id)) ) or 0}
    statuses = {status: db.scalar(select(func.count(MarketplaceOrder.id)).where(MarketplaceOrder.status == status)) or 0 for status in ("pending_payment", "paid", "payment_failed", "cancelled", "expired")}
    value_rows = db.execute(select(MarketplaceOrder.currency, func.sum(MarketplaceOrder.total_amount)).where(MarketplaceOrder.status == "paid").group_by(MarketplaceOrder.currency))
    values = {currency: round(float(amount or 0), 2) for currency, amount in value_rows}
    sales = _merchant_summary(db, None)
    result = {**counts, "orders_by_status": statuses, "paid_order_value_by_currency": values, "paid_sales_by_category": sales["paid_sales_by_category"], "best_selling_products": sales["best_selling_products"], "source": "Marketplace database"}
    return {**result, "artifact": _artifact("Marketplace database", "Marketplace overview", [{"metric": key, "value": value} for key, value in {**counts, "paid_order_value_by_currency": values}.items()], "Paid marketplace order value is separate from transaction volume.")}


def _counts(rows: list[dict], key: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        result[row[key]] = result.get(row[key], 0) + 1
    return result


def _fraud_summary(db: Session, actor: WorkspaceActor) -> dict:
    tx_ids = select(Transaction.transaction_id)
    tx_ids = transaction_scope(tx_ids, actor)
    rows = list(db.scalars(select(RiskAssessment).where(RiskAssessment.transaction_id.in_(tx_ids)).order_by(RiskAssessment.created_at.desc())))
    latest: dict[str, RiskAssessment] = {}
    for row in rows:
        latest.setdefault(row.transaction_id, row)
    counts = {level: sum(row.level == level for row in latest.values()) for level in ("low", "medium", "high")}
    reasons: dict[str, int] = {}
    for row in latest.values():
        try:
            values = json.loads(row.reasons_json or "[]")
        except (TypeError, json.JSONDecodeError):
            values = []
        for reason in values:
            reasons[str(reason)] = reasons.get(str(reason), 0) + 1
    common = sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0]))[:5]
    return {"assessment_count": len(latest), "risk_levels": counts, "common_indicators": [{"indicator": key, "count": value} for key, value in common], "source": "Fraud Intelligence"}


def _tool_result(db: Session, actor: WorkspaceActor, name: str, args: dict) -> dict:
    if name in {"get_transaction_summary", "get_transaction_statistics", "get_payment_analytics", "generate_business_report"}:
        result = _transaction_summary(db, actor, args)
        if name == "get_payment_analytics":
            result["artifact"]["title"] = "Payment analytics"
        if name == "generate_business_report":
            result["marketplace"] = _marketplace_summary(db, actor, {})
            result["fraud"] = _fraud_summary(db, actor)
            result["artifact"] = _artifact("Analytics", "Business report", [
                {"section": "Transactions", "data": {k: result[k] for k in ("transaction_count", "statuses", "volume_by_currency", "successful_volume_by_currency", "pending_volume_by_currency", "failed_volume_by_currency", "success_rate", "payment_methods")}},
                {"section": "Marketplace", "data": {k: result["marketplace"].get(k) for k in ("orders", "order_count", "paid_order_value_by_currency", "order_value_by_currency", "paid_sales_by_category", "best_selling_products") if k in result["marketplace"]}},
                {"section": "Fraud", "data": result["fraud"]},
            ], "Transaction, marketplace order, and risk values are kept separate.", "sections")
        return result
    if name == "search_transactions":
        parsed = TransactionArgs.model_validate(args)
        if parsed.customer_prefix and actor.role != "admin":
            raise PermissionError("Customer-name searches require workspace administrator access.")
        return _search_transactions(db, actor, args)
    if name in {"get_transaction_details", "get_risk_assessment"}:
        parsed = TransactionArgs.model_validate(args)
        if not parsed.transaction_id:
            raise ValueError("Provide a transaction ID to look up.")
        row = db.scalar(_transaction_query(db, actor).where(Transaction.transaction_id == parsed.transaction_id))
        if row is None:
            return {"source": "Transactions database", "rows": [], "artifact": _artifact("Transactions database", "Transaction details", [], "No permitted transaction record matches that ID.")}
        details = _tx_row(db, row)
        return {"source": "Fraud Intelligence" if name == "get_risk_assessment" else "Transactions database", "record": details, "artifact": _artifact("Fraud Intelligence" if name == "get_risk_assessment" else "Transactions database", "Transaction risk assessment" if name == "get_risk_assessment" else "Transaction details", [details], "Risk scores are review cues, not proof of fraud.")}
    if name in {"search_products", "get_product_details", "get_inventory_summary"}:
        if name == "get_inventory_summary":
            parsed = ProductArgs.model_validate(args)
            query = select(Product).where(Product.is_active.is_(True))
            if actor.role == "merchant" and actor.user_id is not None:
                query = query.where(Product.merchant_id == actor.user_id)
            if parsed.query:
                term = f"%{parsed.query.strip()}%"
                query = query.where(Product.name.ilike(term) | Product.description.ilike(term) | Product.slug.ilike(term))
            if parsed.name_prefix:
                query = query.where(Product.name.ilike(f"{parsed.name_prefix}%"))
            if parsed.category:
                query = query.join(ProductCategory).where(ProductCategory.slug == parsed.category)
            if parsed.min_price is not None:
                query = query.where(Product.price >= Decimal(str(parsed.min_price)))
            if parsed.max_price is not None:
                query = query.where(Product.price <= Decimal(str(parsed.max_price)))
            if parsed.available_only:
                query = query.where(Product.stock_quantity > 0)
            if parsed.low_stock_only:
                query = query.where(Product.stock_quantity <= 5)
            if parsed.out_of_stock_only:
                query = query.where(Product.stock_quantity == 0)
            filtered = query.with_only_columns(Product.id, Product.stock_quantity, Product.category_id).subquery()
            stock = filtered.c.stock_quantity
            counts = db.execute(select(
                func.count(filtered.c.id),
                func.coalesce(func.sum(case((stock > 0, 1), else_=0)), 0),
                func.coalesce(func.sum(case(((stock > 0) & (stock <= 5), 1), else_=0)), 0),
                func.coalesce(func.sum(case((stock == 0, 1), else_=0)), 0),
                func.coalesce(func.sum(stock), 0),
            ).select_from(filtered)).one()
            category_rows = db.execute(select(ProductCategory.name, func.count(filtered.c.id)).join(ProductCategory, ProductCategory.id == filtered.c.category_id).group_by(ProductCategory.name).order_by(func.count(filtered.c.id).desc(), ProductCategory.name)).all()
            category_counts = [{"category": category, "product_count": int(count)} for category, count in category_rows]
            rows = [{"product_count": int(counts[0]), "available": int(counts[1]), "low_stock": int(counts[2]), "out_of_stock": int(counts[3]), "units_in_stock": int(counts[4]), "categories": category_counts}]
            category_text = f" Largest category by product count: {category_counts[0]['category']} ({category_counts[0]['product_count']})." if category_counts else ""
            summary = f"Across all matching products: {rows[0]['product_count']} product(s), {rows[0]['available']} available, {rows[0]['low_stock']} low-stock, {rows[0]['out_of_stock']} out of stock, and {rows[0]['units_in_stock']} units in stock.{category_text}"
        else:
            rows = _product_rows(db, actor, args)
            if name == "get_product_details" and len(rows) > 1:
                rows = rows[:1]
            summary = f"Retrieved {len(rows)} matching product record(s)."
        return {"source": "Marketplace database", "rows": rows, "artifact": _artifact("Marketplace database", "Inventory summary" if name == "get_inventory_summary" else "Products", rows, summary, "products" if name != "get_inventory_summary" else "table")}
    if name == "get_marketplace_summary":
        return _marketplace_summary(db, actor, args)
    if name == "search_customers":
        if actor.role != "admin":
            raise PermissionError("Customer directory searches require workspace administrator access.")
        parsed = CustomerArgs.model_validate(args)
        account_query = select(MarketplaceUser).where(
            MarketplaceUser.role == "customer", MarketplaceUser.is_active.is_(True)
        )
        transaction_query = select(Transaction)
        if parsed.customer_id is not None:
            account_query = account_query.where(MarketplaceUser.id == parsed.customer_id)
            # Transaction rows do not have a marketplace account ID; only account-backed records match this filter.
            transaction_query = transaction_query.where(False)
        if parsed.name_prefix:
            account_query = account_query.where(MarketplaceUser.full_name.ilike(f"{parsed.name_prefix}%"))
            transaction_query = transaction_query.where(Transaction.customer_name.ilike(f"{parsed.name_prefix}%"))
        if parsed.name_query:
            needle = f"%{parsed.name_query.strip()}%"
            account_query = account_query.where(MarketplaceUser.full_name.ilike(needle))
            transaction_query = transaction_query.where(Transaction.customer_name.ilike(needle))
        users = list(db.scalars(account_query.order_by(MarketplaceUser.created_at.desc()).limit(parsed.limit)))
        rows_by_key: dict[str, dict] = {}
        for user in users:
            orders = list(db.scalars(
                select(MarketplaceOrder).where(MarketplaceOrder.user_id == user.id)
                .order_by(MarketplaceOrder.created_at.desc()).limit(10)
            ))
            rows_by_key[f"account:{user.id}"] = {
                "customer_id": user.id,
                "name": user.full_name,
                "email": user.email,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "order_count": db.scalar(select(func.count(MarketplaceOrder.id)).where(MarketplaceOrder.user_id == user.id)) or 0,
                "orders": [{"order_number": order.order_number, "status": order.status,
                            "total": float(order.total_amount), "currency": order.currency,
                            "created_at": order.created_at.isoformat() if order.created_at else None}
                           for order in orders],
                "transaction_count": 0,
                "transaction_volume_by_currency": {},
            }
        tx_rows = list(db.scalars(transaction_query.order_by(Transaction.created_at.desc()).limit(parsed.limit * 5)))
        for tx in tx_rows:
            normalized_email = (tx.customer_email or "").casefold()
            account_key = next((key for key, value in rows_by_key.items()
                                if value.get("customer_id") is not None
                                and (value.get("email") or "").casefold() == normalized_email), None)
            key = account_key or f"transaction-customer:{(tx.customer_name or '').casefold()}:{normalized_email}"
            row = rows_by_key.setdefault(key, {
                "customer_id": None, "name": tx.customer_name, "email": tx.customer_email,
                "created_at": tx.created_at.isoformat() if tx.created_at else None,
                "order_count": 0, "orders": [], "transaction_count": 0,
                "transaction_volume_by_currency": {},
            })
            row["transaction_count"] += 1
            currency = tx.currency or "USD"
            row["transaction_volume_by_currency"][currency] = round(
                row["transaction_volume_by_currency"].get(currency, 0.0) + float(tx.amount or 0), 2
            )
        rows = sorted(rows_by_key.values(), key=lambda row: row.get("created_at") or "", reverse=True)[:parsed.limit]
        summary = f"Found {len(rows)} matching customer record(s) across marketplace accounts and local transaction history."
        return {"source": "Customer database", "rows": rows,
                "artifact": _artifact("Customer database", "Customer details", rows, summary)}
    if name in {"get_customer_orders", "get_order_details"}:
        parsed = OrderArgs.model_validate(args)
        if name == "get_customer_orders" and actor.role == "customer":
            parsed.customer_id = actor.user_id
        if name == "get_order_details" and not parsed.order_number:
            raise ValueError("Provide an order number to look up.")
        rows = _order_rows(db, actor, parsed.model_dump(exclude_none=True), detail=name == "get_order_details")
        title = "Order details" if name == "get_order_details" else "Orders"
        return {"source": "Marketplace database", "rows": rows, "artifact": _artifact("Marketplace database", title, rows, f"Found {len(rows)} permitted order record(s).", "orders")}
    if name == "get_merchant_summary":
        merchant_id = actor.user_id if actor.role == "merchant" else None
        result = _merchant_summary(db, merchant_id)
        return {"source": "Marketplace database", **result, "artifact": _artifact("Marketplace database", "Merchant analytics", [result], "Sales include paid order items only.")}
    if name == "get_cart_summary":
        if actor.role == "customer" and actor.user_id:
            result = cart_summary(db, actor.user_id)
            return {"source": "Marketplace database", "cart": result, "artifact": _artifact("Marketplace database", "Your cart", result.get("items", []), "Prices, tax, shipping, and totals are calculated by the backend.")}
        carts = db.execute(select(func.count(ShoppingCart.id), func.count(ShoppingCartItem.id)).outerjoin(ShoppingCartItem, ShoppingCartItem.cart_id == ShoppingCart.id)).one()
        aggregate = {"cart_count": int(carts[0] or 0), "item_count": int(carts[1] or 0)}
        return {"source": "Marketplace database", **aggregate, "artifact": _artifact("Marketplace database", "Cart activity", [aggregate], "Administrator view is aggregate only.")}
    if name in {"get_fraud_summary", "search_high_risk_transactions"}:
        summary = _fraud_summary(db, actor)
        if name == "get_fraud_summary":
            rows = summary["common_indicators"]
            return {**summary, "artifact": _artifact("Fraud Intelligence", "Fraud and risk summary", rows, "High-risk scores are review signals and do not confirm fraud.")}
        query = transaction_scope(select(Transaction).options(selectinload(Transaction.risk_assessments)), actor)
        ids = select(RiskAssessment.transaction_id).where(RiskAssessment.level.in_(("high", "medium")))
        rows = list(db.scalars(query.where(Transaction.transaction_id.in_(ids)).order_by(Transaction.created_at.desc()).limit(TransactionArgs.model_validate(args).limit)))
        data = [_tx_row(db, row) for row in rows]
        return {"source": "Fraud Intelligence", "rows": data, "artifact": _artifact("Fraud Intelligence", "Flagged risk signals", data, "Risk flags are review cues, not proof of fraud.")}
    if name == "get_workspace_summary":
        tx = _transaction_summary(db, actor, {})
        market = _marketplace_summary(db, actor, {})
        fraud = _fraud_summary(db, actor)
        sections = [{"section": "Transactions", "data": {k: tx[k] for k in ("transaction_count", "statuses", "volume_by_currency", "successful_volume_by_currency", "pending_volume_by_currency", "failed_volume_by_currency", "success_rate", "payment_methods")}}, {"section": "Marketplace", "data": {k: market.get(k) for k in ("products", "orders", "orders_by_status", "paid_order_value_by_currency", "paid_sales_by_category", "best_selling_products") if k in market}}, {"section": "Fraud", "data": fraud}]
        return {"source": "Analytics", "transactions": tx, "marketplace": market, "fraud": fraud, "artifact": _artifact("Analytics", "Workspace summary", sections, "Each section is calculated from local records; transaction and order values remain distinct.", "sections")}
    if name == "get_sales_analytics":
        result = _merchant_summary(db, actor.user_id if actor.role == "merchant" else None)
        return {"source": "Analytics", **result, "artifact": _artifact("Analytics", "Paid sales analytics", [result], "Sales are calculated only from stored paid marketplace order items.")}
    if name == "get_paypal_configuration_status":
        configured = paypal.enabled()
        settings = get_settings()
        result = {"configured": configured, "mode": "sandbox", "checkout_available": configured, "message": "PayPal Sandbox is configured." if configured else "PayPal Sandbox is not configured. Local workspace intelligence, transaction analysis and marketplace features remain available. Configure Sandbox credentials to enable PayPal checkout."}
        return {"source": "PayPal Sandbox", **result, "artifact": _artifact("PayPal Sandbox", "PayPal configuration", [{"configured": configured, "mode": "sandbox", "checkout_available": configured}], result["message"])}
    if name == "create_paypal_sandbox_order":
        if actor.role != "customer" or actor.user_id is None:
            raise PermissionError("A signed-in customer is required to start checkout.")
        if not paypal.enabled():
            raise ValueError("PayPal Sandbox is not configured. Local workspace intelligence and marketplace browsing remain available; configure Sandbox credentials to enable checkout.")
        expire_stale_orders(db)
        order = create_marketplace_order(db, actor.user_id)
        try:
            checkout = paypal.create_order(db, order, get_settings().app_base_url.rstrip("/"))
        except Exception:
            db.rollback()
            raise
        record = {"order_number": checkout["order"]["order_number"], "total": checkout["order"]["total"], "currency": checkout["order"]["currency"], "approval_url": checkout["approval_url"], "status": checkout["status"], "sandbox": True}
        return {"source": "PayPal Sandbox", "record": record, "artifact": _artifact("PayPal Sandbox", "Sandbox checkout created", [record], "The order total came from your server-validated cart. Continue on PayPal Sandbox to approve it.", "payment_link")}
    if name == "capture_paypal_sandbox_payment":
        if actor.role != "customer" or actor.user_id is None:
            raise PermissionError("A signed-in customer is required to capture checkout.")
        parsed = PayPalOrderArgs.model_validate(args)
        result = paypal.capture_order(db, parsed.paypal_order_id, actor.user_id)
        record = {key: result.get(key) for key in ("captured", "transaction_id", "order_number", "already_captured", "sandbox")}
        return {"source": "PayPal Sandbox", "record": record, "artifact": _artifact("PayPal Sandbox", "Sandbox payment capture", [record], "Capture result was verified by the backend against the local order amount and currency.")}
    if name == "verify_paypal_payment":
        if actor.role != "customer" or actor.user_id is None:
            raise PermissionError("A signed-in customer is required to verify checkout.")
        parsed = PayPalOrderArgs.model_validate(args)
        record = paypal.verify_order(db, parsed.paypal_order_id, actor.user_id)
        return {"source": "PayPal Sandbox", "record": record, "artifact": _artifact("PayPal Sandbox", "Provider-verified payment status", [record], "Status was fetched from PayPal Sandbox and compared with the stored order.")}
    if name == "get_payment_status":
        parsed = PaymentStatusArgs.model_validate(args)
        order_query = order_scope(select(MarketplaceOrder).options(selectinload(MarketplaceOrder.user)), actor).where(MarketplaceOrder.order_number == parsed.order_number)
        order = db.scalar(order_query)
        if order is None:
            return {"source": "PayPal Sandbox", "rows": [], "artifact": _artifact("PayPal Sandbox", "Recorded payment status", [], "No permitted local order matches that order number.")}
        payment = db.scalar(select(PayPalSandboxOrder).where(PayPalSandboxOrder.marketplace_order_id == order.id))
        row = {"order_number": order.order_number, "order_status": order.status, "transaction_id": order.transaction_id, "paypal_order_id": payment.paypal_order_id if payment else order.paypal_order_id, "recorded_payment_status": payment.status if payment else "not_captured", "source": "local PayPal Sandbox order record", "live_provider_verified": False}
        return {"source": "PayPal Sandbox", "record": row, "artifact": _artifact("PayPal Sandbox", "Recorded payment status", [row], "This is the locally recorded status, not a live PayPal verification.")}
    raise ValueError("That workspace tool is not available.")


def execute_tool(db: Session, actor: WorkspaceActor, name: str, raw_args: dict | None) -> dict:
    spec = TOOL_SPECS.get(name)
    if spec is None:
        raise ValueError("That workspace tool is not available.")
    if actor.role not in spec.roles:
        raise PermissionError("Your account role cannot access that workspace information.")
    if not isinstance(raw_args, dict):
        raise ValueError("Tool arguments must be a JSON object.")
    try:
        args = spec.model.model_validate(raw_args).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise ValueError("The workspace request needs different search details.") from exc
    if isinstance(args, dict) and "period" in args and not args.get("period"):
        args["period"] = "all"
    return _tool_result(db, actor, name, args)


def deterministic_tool(message: str, actor: WorkspaceActor, context: dict | None = None) -> tuple[str, dict] | None:
    """Recognize common workspace questions when Ollama is unavailable or declines a tool."""
    text = message.casefold().strip()
    previous = context or {}
    # Deterministic customer lookup must run before Ollama so the model's
    # privacy-redacted context cannot cause false "no customer data" replies.
    customer_intent = any(term in text for term in ("customer", "customers", "client", "clients"))
    customer_lookup = (customer_intent and any(term in text for term in (
        "detail", "information", "info", "search", "find", "show", "list", "name", "registered", "account"
    ))) or bool(re.search(r"\b(?:give me|show me|get me)\s+(?:the\s+)?details?\s+about\s+[a-z]", text))
    if customer_lookup:
        prefix_match = re.search(
            r"(?:names?\s+(?:(?:that|who)\s+)?(?:start(?:s|ing)?|begin(?:s|ning)?)\s+with\s+|name\s+initial(?:\s+is)?\s+|first\s+letter(?:\s+is)?\s+|letters?\s+)(?:the\s+)?([a-z])\b",
            text,
        )
        if prefix_match:
            return "search_customers", {"name_prefix": prefix_match.group(1).upper(), "limit": 30}
        named_match = re.search(
            r"(?:about|named|called|for|find|search(?:\s+for)?)\s+([a-z][a-z' -]{1,70}?)(?:\s+(?:and|whose|who|with|details|information|customer|customers).*)?$",
            text,
        )
        if named_match:
            candidate = named_match.group(1).strip(" .?!")
            if candidate and candidate not in {"me", "them", "customers", "customer"}:
                return "search_customers", {"name_query": candidate, "limit": 20}
        return "search_customers", {"limit": 20}
    if _is_followup(text) and previous.get("tool") in TOOL_SPECS:
        name = previous["tool"]
        args = dict(previous.get("args") or {})
        if any(word in text for word in ("successful", "success", "failed", "failure", "pending")):
            args["status"] = "successful" if any(word in text for word in ("successful", "success")) else "failed" if any(word in text for word in ("failed", "failure")) else "pending"
            name = "search_transactions"
        if "yesterday" in text or "yday" in text:
            args["period"] = "yesterday"
            name = "search_transactions" if name not in {"get_transaction_summary", "get_transaction_statistics"} else name
        elif "today" in text:
            args["period"] = "today"
        if any(token in text for token in ("below", "under", "less than")):
            amount = _amount(text)
            if amount is not None:
                args["max_price"] = amount
                name = "search_products"
        return name, args

    paypal_id = re.search(r"\b[A-Za-z0-9_-]{8,80}\b", message)
    if any(word in text for word in ("capture", "capture payment")) and paypal_id and "paypal" in text:
        return "capture_paypal_sandbox_payment", {"paypal_order_id": paypal_id.group(0)}
    if any(word in text for word in ("verify", "check status", "check my payment")) and paypal_id and any(word in text for word in ("paypal", "payment", "order")):
        return "verify_paypal_payment", {"paypal_order_id": paypal_id.group(0)}
    if actor.role == "customer" and any(phrase in text for phrase in ("start checkout", "create checkout", "checkout now", "proceed to checkout", "pay for my cart", "place my order")):
        return "create_paypal_sandbox_order", {}

    if "payment method" in text or "payment methods" in text:
        return "get_payment_analytics", {"period": "all"}
    txid = re.search(r"\btxn_[a-zA-Z0-9_-]+\b", message)
    if txid:
        return "get_risk_assessment" if any(word in text for word in ("risk", "fraud", "why", "unusual")) else "get_transaction_details", {"transaction_id": txid.group(0)}
    if any(word in text for word in ("fraud", "suspicious", "high risk", "risk assessment", "flagged")):
        if any(word in text for word in ("show", "list", "find", "which", "transactions")) and not any(word in text for word in ("summary", "how many", "count")):
            return "search_high_risk_transactions", {"limit": 10}
        return "get_fraud_summary", {}
    if any(word in text for word in ("paypal", "pay pal", "sandbox config", "payment provider")) and any(word in text for word in ("config", "configured", "enable", "status", "available")):
        return "get_paypal_configuration_status", {}
    if any(word in text for word in ("checkout", "paypal order", "pay pal order")) and any(word in text for word in ("create", "start", "begin", "initiate", "place", "proceed")) and actor.role == "customer":
        return "create_paypal_sandbox_order", {}
    if "order" in text or "orders" in text:
        if any(word in text for word in ("order number", "order id", "status of", "details of order")):
            order_number = re.search(r"\b(?:ord|ppord|order)[-_][a-z0-9-]+\b", message, re.IGNORECASE)
            if order_number:
                return "get_order_details", {"order_number": order_number.group(0)}
        if actor.role == "merchant":
            return "get_merchant_summary", {}
        return "get_customer_orders" if actor.role == "customer" and any(word in text for word in ("my", "show", "latest", "history")) else "get_marketplace_summary", {}
    if any(word in text for word in ("cart", "shipping", "tax", "subtotal")):
        return "get_cart_summary", {}
    if any(word in text for word in ("inventory", "stock", "out of stock", "low stock")):
        listing = any(word in text for word in ("show", "list", "find", "which"))
        if listing and "out of stock" in text:
            return "search_products", {"out_of_stock_only": True, "limit": 20}
        if listing and any(word in text for word in ("low stock", "low inventory")):
            return "search_products", {"low_stock_only": True, "limit": 20}
        return "get_inventory_summary", {"low_stock_only": "low" in text, "available_only": "available" in text, "out_of_stock_only": "out of stock" in text, "limit": 30}
    if any(word in text for word in ("best selling", "top selling", "selling best", "units sold", "sales analytics", "category performs best", "best performing category", "top category")):
        return ("get_sales_analytics", {}) if actor.role in {"admin", "merchant"} else None
    product_terms = ("product", "products", "laptop", "laptops", "phone", "mobile", "category", "categories", "price", "catalog", "catalogue", "available")
    if any(word in text for word in ("product details", "details for product", "information for product")):
        match = re.search(r"(?:details|information) for (?:product )?([a-z0-9][a-z0-9 -]{1,80})", text)
        return "get_product_details", {"query": match.group(1).strip() if match else ""}
    if any(word in text for word in product_terms):
        category = next((slug for phrase, slug in (("laptop", "laptops"), ("mobile", "mobiles"), ("phone", "mobiles"), ("shoe", "shoes"), ("gaming", "gaming"), ("beauty", "beauty-wellness"), ("appliance", "appliances"), ("accessor", "accessories"), ("fashion", "fashion"), ("home", "home-kitchen"), ("electronic", "electronics")) if phrase in text), None)
        amount = _amount(text)
        args: dict = {"category": category, "available_only": "available" in text or "in stock" in text, "limit": 20}
        prefix = re.search(r"(?:names? start with|name starts with)\s+([a-z][a-z'-]*)", text)
        if prefix:
            args["name_prefix"] = prefix.group(1)
        if "name" in text or "called" in text or "find " in text:
            query_match = re.search(r"(?:named|called|for)\s+([a-z0-9][a-z0-9 -]{1,60})", text)
            if query_match:
                args["query"] = query_match.group(1).strip()
        if amount is not None and any(word in text for word in ("below", "under", "less than")):
            args["max_price"] = amount
        if amount is not None and any(word in text for word in ("above", "over", "more than")):
            args["min_price"] = amount
        if any(word in text for word in ("most products", "largest category", "best category")):
            return "get_inventory_summary", {"limit": 30}
        return "search_products", {key: value for key, value in args.items() if value is not None}
    if any(word in text for word in ("merchant", "seller", "sales", "selling")):
        return ("get_merchant_summary", {}) if actor.role in {"admin", "merchant"} else None
    if "compare" in text and any(word in text for word in ("transaction", "payment", "order", "marketplace", "sales")):
        return ("get_workspace_summary", {}) if actor.role == "admin" else ("generate_business_report", {})
    if any(word in text for word in ("workspace summary", "business summary", "complete report", "business report", "workspace report", "whole workspace")):
        return ("get_workspace_summary", {}) if actor.role == "admin" else ("generate_business_report", {})
    if any(word in text for word in ("latest", "most recent", "above", "over", "greater than", "customer name", "payment method", "date range", "between", "from ", "name starts with", "names start with")) and any(word in text for word in ("transaction", "transactions", "payment", "payments")):
        args: dict = {"limit": 10}
        amount = _amount(text)
        if amount is not None and any(word in text for word in ("above", "over", "greater than")):
            args["min_amount"] = amount
        method = next((value for value in ("paypal", "pay pal", "card", "bank transfer", "wallet") if value in text), None)
        if method:
            args["payment_method"] = method
        if "today" in text:
            args["period"] = "today"
        elif "yesterday" in text:
            args["period"] = "yesterday"
        dates = re.search(r"(?:between|from)\s+(\d{4}-\d{2}-\d{2})\s+(?:and|to)\s+(\d{4}-\d{2}-\d{2})", text)
        if dates:
            args["date_from"], args["date_to"] = dates.group(1), dates.group(2)
        prefix = re.search(r"(?:name starts with|names start with|customer name starts with|for customer)\s+([a-z][a-z'-]*)", text)
        if prefix:
            args["customer_prefix"] = prefix.group(1)
        return "search_transactions", args
    payment_words = ("transaction", "transactions", "payment", "payments", "volume", "total", "average", "successful", "failed", "pending")
    if any(word in text for word in payment_words):
        status = next((value for value in ("successful", "failed", "pending") if value in text), None)
        period = "today" if "today" in text else "yesterday" if "yesterday" in text else "all"
        if status or any(word in text for word in ("show", "list", "latest", "recent", "above", "over")):
            args = {"status": status, "period": period, "limit": 10}
            amount = _amount(text)
            if amount is not None and any(word in text for word in ("above", "over", "greater than")):
                args["min_amount"] = amount
            return "search_transactions", {key: value for key, value in args.items() if value is not None}
        return "get_transaction_summary", {"period": period}
    if any(word in text for word in ("analytics", "report", "trend", "compare")):
        return ("get_workspace_summary", {}) if actor.role == "admin" else ("get_payment_analytics", {})
    return None


def _is_followup(text: str) -> bool:
    normalized = re.sub(r"[^\w\s]", "", text.casefold()).strip()
    return normalized in {"and", "how many", "show them", "show only successful ones", "only successful ones", "only failed ones", "only pending ones", "what about yesterday", "what about today", "yesterday", "today", "more", "tell me more", "what about those"}


def _amount(text: str) -> float | None:
    match = re.search(r"(?:\$|usd\s*)?([0-9]+(?:\.[0-9]{1,2})?)", text, re.IGNORECASE)
    return float(match.group(1)) if match else None


def format_tool_result(message: str, result: dict, language: str = "en") -> str:
    """Produce a conservative fallback from the exact structured result."""
    source = result.get("source", "PayPilot workspace database")
    if source == "Customer database":
        rows = result.get("rows", [])
        if not rows:
            return "No matching customer records were found in the local customer accounts or transaction history."
        if re.search(r"\b(?:names?\s+only|only\s+(?:the\s+)?names?)\b", message, re.IGNORECASE):
            names = list(dict.fromkeys(
                str(row.get("name", "")).strip()
                for row in rows[:20]
                if str(row.get("name", "")).strip()
            ))
            return "\n".join(names) if names else "No matching customer names were found."
        lines = []
        for row in rows[:20]:
            details = [f"Name: {row.get('name') or 'Not recorded'}"]
            if row.get("customer_id") is not None:
                details.append(f"Customer ID: {row['customer_id']}")
            if row.get("email"):
                details.append(f"Email: {row['email']}")
            if row.get("created_at"):
                details.append(f"Date: {row['created_at']}")
            details.append(f"Orders: {row.get('order_count', 0)}")
            details.append(f"Transactions: {row.get('transaction_count', 0)}")
            volumes = row.get("transaction_volume_by_currency") or {}
            if volumes:
                details.append("Transaction volume: " + ", ".join(f"{currency} {amount:,.2f}" for currency, amount in sorted(volumes.items())))
            orders = row.get("orders") or []
            if orders:
                details.append("Recent orders: " + "; ".join(f"{order['order_number']} ({order['status']}, {order['currency']} {order['total']:,.2f})" for order in orders[:5]))
            lines.append(" · ".join(details))
        return f"Source: Customer database\nFound {len(rows)} matching customer record(s):\n" + "\n".join(lines)
    if "transaction_count" in result:
        count = result["transaction_count"]
        statuses = result.get("statuses", {})
        totals = result.get("volume_by_currency", {})
        volume = ", ".join(f"{currency} {values['volume']:,.2f}" for currency, values in totals.items()) or "no recorded volume"
        if "payment method" in message.casefold() or "methods" in message.casefold():
            methods = result.get("payment_methods", [])
            best = ", ".join(f"{row['payment_method']} ({row['count']})" for row in methods[:5]) or "no payment-method records"
            return f"Payment methods by stored transaction count: {best}. The database contains {count} simulated transactions."
        if "trend" in message.casefold() or "over time" in message.casefold():
            days = result.get("daily_activity", [])
            active = sum(1 for day in days if day["count"])
            return f"Over the last 14 days, {active} day(s) had recorded transaction activity. The {source} currently contains {count} simulated transactions; volume by currency: {volume}. These local records are not automatically completed real-world payments."
        if result.get("period") in {"today", "yesterday", "week", "month"}:
            period = result["period"]
            return f"There are {count} simulated transactions for {period} in the {source}. Volume by currency: {volume}. Status counts: {statuses}. These local records are not automatically completed real-world payments."
        return f"There are {count} simulated transactions in the {source}. Volume by currency: {volume}. Status counts: {statuses}. These local records are not automatically completed real-world payments."
    if result.get("rows") is not None and source == "Transactions database":
        rows = result.get("rows", [])
        status = result.get("filters", {}).get("status") or next((word for word in ("failed", "pending", "successful") if word in message.casefold()), None)
        period = result.get("filters", {}).get("period")
        label = f" {status}" if status else ""
        day_label = f" {period}" if period and period != "all" else ""
        if result.get("filters", {}).get("date_from") and result.get("filters", {}).get("date_to"):
            day_label = f" from {result['filters']['date_from']} through {result['filters']['date_to']}"
        if not rows:
            return f"There are no matching{label} simulated transactions{day_label} in the local database."
        details = "\n".join(f"{row['transaction_id']} · {row['status']} · {row['currency']} {row['amount']:,.2f}" for row in rows[:10])
        noun = "payments" if status else "transactions"
        answer = f"There are {len(rows)} matching{label} simulated {noun}{day_label} in the local database:\n{details}"
        return f"Following up: {answer}" if _is_followup(message) else answer
    if source == "Fraud Intelligence" and result.get("rows") is not None:
        rows = result.get("rows", [])
        if not rows:
            return "There are no stored high- or medium-risk transaction flags in the local records. Risk flags are review cues, not proof of fraud."
        ids = ", ".join(row["transaction_id"] for row in rows[:10])
        return f"Found {len(rows)} stored high- or medium-risk transaction flag(s): {ids}. These are review cues, not proof of fraud."
    artifact = result.get("artifact", {})
    rows = artifact.get("rows", [])
    if "risk_levels" in result:
        levels = result["risk_levels"]
        flagged = levels.get("high", 0) + levels.get("medium", 0)
        common = ", ".join(f"{row['indicator']} ({row['count']})" for row in result.get("common_indicators", [])) or "no repeated indicators"
        return f"The Fraud Intelligence database has {flagged} high- or medium-risk flag(s) among {result.get('assessment_count', 0)} assessed transaction(s). High: {levels.get('high', 0)}; medium: {levels.get('medium', 0)}. Common indicators: {common}. A risk flag is not proof of fraud."
    if "paid_sales_by_currency" in result:
        sales = result.get("paid_sales_by_currency", {})
        categories = result.get("paid_sales_by_category", [])
        top = categories[0] if categories else None
        top_text = f" Top category by paid sales: {top['category']} ({top['currency']} {top['paid_sales']:,.2f})." if top else " No paid category sales are recorded yet."
        return f"There are {result.get('units_sold', 0)} units sold across {result.get('paid_order_count', 0)} paid order(s); paid sales by currency: {sales}.{top_text}"
    if result.get("record"):
        row = result["record"]
        if "approval_url" in row:
            return f"I created a PayPal Sandbox checkout for order {row['order_number']} totaling {row['currency']} {row['total']:,.2f}. Continue at {row['approval_url']}. This is a Sandbox order, not a live payment."
        if "provider_status" in row:
            return f"PayPal Sandbox reports {row['provider_status']} for order {row['paypal_order_id']}. Amount and currency match the local order: {row['amount_matches']}."
        if "captured" in row:
            return f"PayPal Sandbox capture {'completed' if row['captured'] else 'did not complete'} for order {row.get('order_number') or 'requested'}. Local transaction: {row.get('transaction_id') or 'not recorded'}."
        if "transaction_id" in row:
            reasons = "; ".join(row.get("risk_reasons") or []) or "no notable risk reasons were stored"
            recommendation = row.get("risk_recommendation") or "No further recommendation was stored."
            return (f"Transaction {row['transaction_id']} is recorded as {row['status']} for {row['currency']} {row['amount']:,.2f}. "
                    f"Risk is {row['risk_level']} ({row['risk_score']}/100). Recorded reasons: {reasons}. {recommendation} Risk scores are review cues, not proof of fraud.")
        if "order_number" in row:
            return f"Order {row['order_number']} is locally recorded as {row['order_status']}; payment status: {row['recorded_payment_status']}. This is not a live PayPal verification."
    if result.get("message") and "configured" in result:
        return result["message"]
    if artifact.get("kind") == "products" and rows:
        items = "; ".join(f"{row['name']} — {row['currency']} {row['price']:,.2f}" for row in rows[:6])
        extra = f" and {len(rows) - 6} more" if len(rows) > 6 else ""
        return f"Found {len(rows)} matching product(s) in the {source}: {items}{extra}."
    summary = artifact.get("summary") or result.get("summary") or f"The {source} contains {len(rows)} matching record(s)."
    return str(summary)
