import json
import http.client
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.models import (
    AssistantConversation, AssistantMessage, MarketplaceOrder, MarketplaceUser,
    Product, RiskAssessment, Transaction,
)
from app.services.assistant_nlu import (
    detect_payment_intent,
    financial_fallback,
    general_fallback,
    resolve_language,
)
from app.services.permissions import WorkspaceActor, transaction_scope
from app.services.workspace_tools import (
    TOOL_SPECS,
    deterministic_tool,
    execute_tool,
    format_tool_result,
    tool_schemas,
)


def _latest_risk(tx: Transaction) -> RiskAssessment | None:
    return max(tx.risk_assessments, key=lambda row: row.created_at or datetime.min, default=None)


def _public_transaction(tx: Transaction, risk: RiskAssessment | None = None) -> dict:
    risk = risk or _latest_risk(tx)
    return {
        "id": tx.transaction_id,
        "amount": round(tx.amount, 2),
        "currency": tx.currency,
        "status": tx.status,
        "risk_score": risk.score if risk else 0,
        "risk_level": risk.level if risk else "low",
        "risk_reasons": json.loads(risk.reasons_json or "[]") if risk else [],
    }


def _transaction_ids(actor: WorkspaceActor | None = None):
    return transaction_scope(select(Transaction.transaction_id), actor or WorkspaceActor("admin"))


def _currency_totals(db: Session, start=None, actor: WorkspaceActor | None = None) -> dict[str, float]:
    query = select(Transaction.currency, func.sum(Transaction.amount)).group_by(Transaction.currency)
    query = query.where(Transaction.transaction_id.in_(_transaction_ids(actor)))
    if start is not None:
        query = query.where(func.date(Transaction.created_at) >= start.isoformat())
    return {currency: round(float(total or 0), 2) for currency, total in db.execute(query)}


def build_context(db: Session, requested_transaction_id: str | None = None, actor: WorkspaceActor | None = None) -> dict:
    visible_ids = _transaction_ids(actor)
    transaction_count = db.scalar(select(func.count(Transaction.id)).where(Transaction.transaction_id.in_(visible_ids))) or 0
    status_counts = {
        status: count
        for status, count in db.execute(select(Transaction.status, func.count(Transaction.id)).where(Transaction.transaction_id.in_(visible_ids)).group_by(Transaction.status))
    }
    today = date.today()
    today_start = today - timedelta(days=13)
    today_count = db.scalar(select(func.count(Transaction.id)).where(Transaction.transaction_id.in_(visible_ids), func.date(Transaction.created_at) == today.isoformat())) or 0
    today_statuses = {
        status: count
        for status, count in db.execute(
            select(Transaction.status, func.count(Transaction.id))
            .where(Transaction.transaction_id.in_(visible_ids), func.date(Transaction.created_at) == today.isoformat())
            .group_by(Transaction.status)
        )
    }
    high_risk_count = db.scalar(
        select(func.count(func.distinct(RiskAssessment.transaction_id))).where(RiskAssessment.level == "high", RiskAssessment.transaction_id.in_(visible_ids))
    ) or 0
    recent_rows = list(
        db.scalars(
            select(Transaction)
            .options(selectinload(Transaction.risk_assessments))
            .where(Transaction.transaction_id.in_(visible_ids))
            .order_by(Transaction.created_at.desc())
            .limit(20)
        )
    )
    requested_row = None
    if requested_transaction_id and not any(row.transaction_id == requested_transaction_id for row in recent_rows):
        requested_row = db.scalar(
            select(Transaction)
            .options(selectinload(Transaction.risk_assessments))
            .where(Transaction.transaction_id == requested_transaction_id, Transaction.transaction_id.in_(visible_ids))
        )
    failed_rows = list(
        db.scalars(select(Transaction).options(selectinload(Transaction.risk_assessments)).where(Transaction.transaction_id.in_(visible_ids), Transaction.status == "failed").order_by(Transaction.created_at.desc()).limit(10))
    )
    successful_rows = list(
        db.scalars(select(Transaction).options(selectinload(Transaction.risk_assessments)).where(Transaction.transaction_id.in_(visible_ids), Transaction.status == "successful").order_by(Transaction.created_at.desc()).limit(10))
    )
    today_status_rows = {
        status: list(
            db.scalars(
                select(Transaction)
                .options(selectinload(Transaction.risk_assessments))
                .where(Transaction.transaction_id.in_(visible_ids), Transaction.status == status, func.date(Transaction.created_at) == today.isoformat())
                .order_by(Transaction.created_at.desc())
                .limit(10)
            )
        )
        for status in ("successful", "pending", "failed")
    }
    pending_rows = list(
        db.scalars(select(Transaction).options(selectinload(Transaction.risk_assessments)).where(Transaction.transaction_id.in_(visible_ids), Transaction.status == "pending").order_by(Transaction.created_at.desc()).limit(10))
    )
    suspicious_assessments = list(
        db.scalars(
            select(RiskAssessment)
            .options(selectinload(RiskAssessment.transaction))
            .where(RiskAssessment.level.in_(("high", "medium")), RiskAssessment.transaction_id.in_(visible_ids))
            .order_by(RiskAssessment.score.desc(), RiskAssessment.created_at.desc())
            .limit(10)
        )
    )
    suspicious_rows = [
        _public_transaction(assessment.transaction, assessment)
        for assessment in suspicious_assessments
        if assessment.transaction is not None
    ]
    daily_activity = [
        {"date": day.isoformat(), "count": 0}
        for day in (today - timedelta(days=offset) for offset in range(13, -1, -1))
    ]
    daily_by_date = {item["date"]: item for item in daily_activity}
    for day, count in db.execute(
        select(func.date(Transaction.created_at), func.count(Transaction.id))
        .where(Transaction.transaction_id.in_(visible_ids), func.date(Transaction.created_at) >= today_start.isoformat())
        .group_by(func.date(Transaction.created_at))
    ):
        if day in daily_by_date:
            daily_by_date[day]["count"] = count

    recent = [_public_transaction(row) for row in recent_rows]
    if requested_row:
        recent.append(_public_transaction(requested_row))
    return {
        "transaction_count": transaction_count,
        "volume_by_currency": _currency_totals(db, actor=actor),
        "statuses": {key: status_counts.get(key, 0) for key in ("successful", "pending", "failed")},
        "today_count": today_count,
        "today_volume_by_currency": _currency_totals(db, today, actor),
        "today_statuses": {key: today_statuses.get(key, 0) for key in ("successful", "pending", "failed")},
        "high_risk_count": high_risk_count,
        "recent_transactions": recent,
        "failed_transactions": [_public_transaction(row) for row in failed_rows],
        "successful_transactions": [_public_transaction(row) for row in successful_rows],
        "pending_transactions": [_public_transaction(row) for row in pending_rows],
        "today_transactions": {status: [_public_transaction(row) for row in rows] for status, rows in today_status_rows.items()},
        "suspicious_transactions": suspicious_rows,
        "daily_activity": daily_activity,
    }



_CONTEXT_TERMS = ("context", "capabilities", "what can you do")
# Previously any of these words occurring ANYWHERE in a message (e.g. "available",
# "use", "see") combined with a domain word (e.g. "marketplace", "transaction")
# was enough to classify the message as a context/status question. That matched
# ordinary data requests too -- "Show me available marketplace products" contains
# both "marketplace" and "available" -- which hijacked them into the generic
# Workspace Integration Status reply instead of running the real catalogue search
# tool. A context/status question is now recognized only when it is actually
# phrased as a question about the assistant's own capability or the caller's
# permission, not merely because it happens to share a word with one.
_SELF_CAPABILITY_STARTERS = ("can you", "do you", "are you", "could you", "would you", "will you")
_SELF_CAPABILITY_PHRASES = (
    "do i have access", "am i able to", "is this available to me", "what access do i have",
    "what can you access", "what do you have access to", "are you able to",
)
_ROLE_PERMISSION_TERMS = (
    "my role", "my current role", "my permission", "my permissions", "my access",
    "with my role", "as a customer", "as a merchant", "with my account",
)


def _is_context_status_question(message: str) -> bool:
    """Recognize questions about PayPilot's identity, context, or data access.

    This intentionally does NOT fire for ordinary data/action requests that
    merely mention a workspace domain (marketplace, transactions, fraud,
    customers, PayPal) -- those are left for _resolve_workspace_tool to route
    to the real backend tool. It fires only for genuine self-referential
    capability/permission questions.
    """
    text = re.sub(r"\s+", " ", message.casefold()).strip()
    if "context" in text:
        return any(term in text for term in ("your", "details", "give", "show", "what", "tell", "information", "attached", "have"))
    domain_mentioned = any(
        term in text for term in ("customer", "customers", "transaction", "transactions", "fraud", "risk", "marketplace", "paypal", "pay pal")
    )
    if not domain_mentioned:
        return False
    if text.startswith(_SELF_CAPABILITY_STARTERS):
        return True
    if any(phrase in text for phrase in _SELF_CAPABILITY_PHRASES):
        return True
    if any(term in text for term in _ROLE_PERMISSION_TERMS):
        return True
    return False


def _table_is_queryable(db: Session, model) -> bool:
    """Check the configured database schema without reading user records."""
    try:
        return inspect(db.connection()).has_table(model.__tablename__)
    except Exception:
        return False


def _registered_read_tools(actor: WorkspaceActor, names: tuple[str, ...]) -> list[str]:
    return [
        name for name in names
        if name in TOOL_SPECS
        and actor.role in TOOL_SPECS[name].roles
        and not TOOL_SPECS[name].side_effect
    ]


def _module_status(db: Session, actor: WorkspaceActor, names: tuple[str, ...], models: tuple) -> dict:
    registered = [name for name in names if name in TOOL_SPECS and not TOOL_SPECS[name].side_effect]
    permitted = _registered_read_tools(actor, names)
    missing_tables = [model.__tablename__ for model in models if not _table_is_queryable(db, model)]
    database_ready = not missing_tables
    return {
        "available": bool(permitted) and database_ready,
        "permitted_tools": permitted,
        "registered_tools": registered,
        "database_ready": database_ready,
        "missing_tables": missing_tables,
    }


def _context_awareness_reply(db: Session, message: str, actor: WorkspaceActor) -> str | None:
    """Answer context/access questions from live backend configuration and allowlists."""
    if not _is_context_status_question(message):
        return None

    settings = get_settings()
    statuses = {
        "transactions": _module_status(db, actor, (
            "get_transaction_summary", "search_transactions", "get_transaction_details",
            "get_transaction_statistics", "get_payment_analytics",
        ), (Transaction,)),
        "marketplace": _module_status(db, actor, (
            "search_products", "get_product_details", "get_inventory_summary",
            "get_marketplace_summary", "get_order_details", "get_customer_orders",
            "get_cart_summary", "get_merchant_summary",
        ), (Product, MarketplaceOrder)),
        "customers": _module_status(db, actor, (
            "search_customers", "get_customer_orders", "get_cart_summary", "get_order_details",
        ), (MarketplaceUser, MarketplaceOrder)),
        "fraud": _module_status(db, actor, (
            "get_fraud_summary", "search_high_risk_transactions", "get_risk_assessment",
        ), (RiskAssessment, Transaction)),
        "analytics": _module_status(db, actor, (
            "get_workspace_summary", "get_sales_analytics", "get_payment_analytics",
            "generate_business_report",
        ), (Transaction, Product, MarketplaceOrder, RiskAssessment)),
    }

    # Query the same allowlisted status tool used by ordinary assistant requests.
    try:
        paypal_status = execute_tool(db, actor, "get_paypal_configuration_status", {}).get("configured") is True
        paypal_status_checked = True
    except Exception:
        paypal_status = False
        paypal_status_checked = False

    text = re.sub(r"\s+", " ", message.casefold())
    asks_customer = any(term in text for term in ("customer", "customers"))
    asks_transactions = any(term in text for term in ("transaction", "transactions"))
    asks_fraud = any(term in text for term in ("fraud", "risk"))
    asks_marketplace = "marketplace" in text or "inventory" in text or "product" in text
    asks_paypal = "paypal" in text or "pay pal" in text
    broad = "context" in text

    lines = ["I’m PayPilot AI, integrated with this PayPilot application.",
             f"Configured local language model: Ollama `{settings.ollama_model}`.",
             "I can handle general questions and learning/technology topics, and use authorized backend tools for PayPilot workspace analysis."]

    if broad or asks_transactions:
        status = statuses["transactions"]
        if status["available"]:
            lines.append("Transactions: available through authorized, role-scoped read tools against the local transaction database. These records may be simulated; they are not automatically PayPal transactions.")
        elif not status["permitted_tools"]:
            lines.append("Transactions: not available to this account role through the assistant’s read tools.")
        else:
            missing = ", ".join(status["missing_tables"])
            lines.append(f"Transactions: backend tools are registered, but required database table(s) are unavailable: {missing}.")

    if broad or asks_marketplace:
        status = statuses["marketplace"]
        marketplace_labels = {
            "search_products": "catalogue search", "get_product_details": "product details",
            "get_inventory_summary": "inventory summaries", "get_marketplace_summary": "marketplace summaries",
            "get_order_details": "permitted order details", "get_customer_orders": "customer order history",
            "get_cart_summary": "cart summaries", "get_merchant_summary": "merchant summaries",
        }
        marketplace_capabilities = [marketplace_labels[name] for name in status["permitted_tools"] if name in marketplace_labels]
        if status["available"]:
            lines.append("Marketplace: available through authorized backend tools for " + ", ".join(marketplace_capabilities) + ". Records remain restricted to this account’s role.")
        elif not status["permitted_tools"]:
            lines.append("Marketplace: no marketplace read tool is permitted for this account role.")
        else:
            missing = ", ".join(status["missing_tables"])
            lines.append(f"Marketplace: backend tools are registered, but required marketplace table(s) are unavailable: {missing}.")

    if broad or asks_customer:
        # For a direct single-topic permission question ("Can you access customer
        # records with my current role?") lead with an explicit Yes/No before the
        # explanation, per the required direct-answer behavior. A broad status
        # report (multiple topics at once) keeps the plain bullet, since a
        # Yes/No prefix on every line would not read as a direct answer there.
        direct_yes = "" if broad else "Yes — "
        direct_no = "" if broad else "No — "
        directory = _registered_read_tools(actor, ("search_customers",))
        if directory and statuses["customers"]["database_ready"]:
            lines.append(direct_yes + "Customer data: customer-directory search is available to the workspace administrator and can search customer accounts plus local transaction customer records. Customer contact details remain protected in assistant replies.")
        elif actor.role == "customer":
            lines.append(direct_no + "Customer data: general customer-directory search is not permitted. This signed-in customer can access only their own permitted order and cart data through scoped tools; other customers’ records are not accessible.")
        elif actor.role == "merchant":
            lines.append(direct_no + "Customer data: general customer-directory search is not permitted. Order information is limited to records allowed by the merchant’s product/order scope; there is no unrestricted customer directory access.")
        elif actor.role == "guest":
            lines.append(direct_no + "Customer data: unavailable to a guest account; sign-in and the required role are needed.")
        else:
            missing = ", ".join(statuses["customers"]["missing_tables"])
            lines.append(f"Customer data: the administrator search tool is registered, but required customer table(s) are unavailable: {missing}.")

    if broad or asks_fraud:
        status = statuses["fraud"]
        if status["available"]:
            lines.append("Fraud Intelligence: available through role-scoped tools that read stored risk assessments. Risk flags are review signals, not proof of fraud.")
        elif not status["permitted_tools"]:
            lines.append("Fraud Intelligence: not available to this account role through the assistant’s read tools.")
        else:
            missing = ", ".join(status["missing_tables"])
            lines.append(f"Fraud Intelligence: tools are registered, but required risk/transaction table(s) are unavailable: {missing}.")

    if broad or asks_paypal:
        if not paypal_status_checked:
            lines.append("PayPal Sandbox: its configuration-status tool could not be checked, so I cannot confirm whether checkout is configured.")
        elif paypal_status:
            lines.append("PayPal Sandbox: configured. This is a test environment, not live payment processing. A signed-in customer must explicitly start checkout and approve it with PayPal; I do not silently charge or capture payments.")
        else:
            lines.append("PayPal Sandbox: not configured (server-side Sandbox credentials are missing or incomplete). Checkout/capture is unavailable, but local transaction analysis and marketplace data access remain separate and can still work.")

    if broad:
        lines.append("Analytics: " + ("available through permitted backend tools." if statuses["analytics"]["available"] else "not currently available to this account because its tools or required database tables are unavailable."))
        lines.append("Context and privacy: I use this conversation to understand follow-ups and can summarize my operating capabilities, but I cannot disclose private system instructions, credentials, tokens or internal reasoning.")
    lines.append("What I checked for this answer: the registered backend read tools, this account’s role permissions, queryability of the relevant database tables, and the PayPal Sandbox configuration status. I did not retrieve individual customer or transaction records for this status check.")
    return "\n\n".join(lines)

def _format_totals(values: dict[str, float]) -> str:
    if not values:
        return "no recorded volume"
    return ", ".join(f"{currency} {amount:,.2f}" for currency, amount in sorted(values.items()))


def _redact_private_text(db: Session, text: str) -> str:
    from app.models import MarketplaceUser

    identifiers = db.execute(select(Transaction.customer_name, Transaction.customer_email, Transaction.ip_address).distinct())
    for row in identifiers:
        for value in row:
            if value and len(value.strip()) > 1:
                text = re.sub(re.escape(value.strip()), "[redacted customer detail]", text, flags=re.IGNORECASE)
    account_values = db.execute(select(MarketplaceUser.full_name, MarketplaceUser.email).distinct())
    for row in account_values:
        for value in row:
            if value and len(value.strip()) > 1:
                text = re.sub(re.escape(value.strip()), "[redacted account detail]", text, flags=re.IGNORECASE)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", "[redacted email]", text)
    text = re.sub(r"\b(?:\d[ -]?){13,19}\b", "[redacted number]", text)
    return text


def _transaction_list(items: list[dict]) -> str:
    if not items:
        return "No matching simulated transactions were found."
    lines = [f"{row['id']} · {row['status']} · {row['currency']} {row['amount']:,.2f}" for row in items]
    return "\n".join(lines)


def _customer_name_prefix(message: str) -> str | None:
    """Recognize explicit requests to list local demo customers by name initial."""
    text = message.casefold()
    patterns = (
        r"(?:customer|person|people|transaction|name|names).*?(?:start(?:s|ing)?|begin(?:s|ning)?).*?(?:with|wi|letter)\s+([a-z])\b",
        r"(?:start(?:s|ing)?|begin(?:s|ning)?).*?(?:with|wi|letter)\s+([a-z])\b.*?(?:customer|person|people|transaction|name|names)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).upper()
    return None


def _customer_prefix_reply(db: Session, prefix: str, actor: WorkspaceActor | None = None) -> str:
    actor = actor or WorkspaceActor("admin")
    if actor.role != "admin":
        return "Customer-name lookups are available only to the workspace administrator."
    query = transaction_scope(
        select(Transaction).options(selectinload(Transaction.risk_assessments)),
        actor,
    )
    rows = list(
        db.scalars(
            query.where(Transaction.customer_name.ilike(f"{prefix}%"))
            .order_by(Transaction.created_at.desc())
            .limit(20)
        )
    )
    if not rows:
        return f"No simulated transaction customers with names starting with {prefix} were found."
    lines = [
        f"{tx.customer_name} · {tx.transaction_id} · {tx.currency} {tx.amount:,.2f} · {tx.status}"
        for tx in rows
    ]
    return (
        f"Found {len(rows)} matching simulated transaction record(s) for names starting with {prefix}:\n"
        + "\n".join(lines)
        + "\nThese are local simulator records, not real payment data. Email addresses are omitted."
    )


def _followup_target(message: str, history: list[dict]) -> str | None:
    text = message.lower().strip(" .!?\n\t")
    followup = text in {"show them", "list them", "tell me more", "what about them", "and them", "why"}
    followup = followup or (
        text.startswith("how many")
        and not any(word in text for word in ("transaction", "payment", "failed", "pending", "risk"))
    )
    if not followup:
        return None
    for entry in reversed(history):
        if entry.get("role") == "user" and entry.get("content"):
            return str(entry["content"])
    return None


def deterministic_reply(message: str, context: dict, history: list[dict] | None = None) -> str:
    history = history or []
    previous = _followup_target(message, history)
    if previous:
        if message.lower().strip(" .!?\n\t") == "why" and re.search(r"\btxn_[a-zA-Z0-9_-]+\b", previous):
            previous = f"Why was this transaction marked risky? {previous}"
        return f"Following up on your previous question: {deterministic_reply(previous, context, [])}"

    text = message.lower().strip()
    normalized = re.sub(r"[^a-z0-9\s_:-]", "", text)
    if normalized in {"hi", "hello", "hey", "hey there", "hello there", "good morning", "good afternoon", "good evening"}:
        return "Hi! I’m PayPilot AI. I can help you understand simulated payment activity, transaction status, risk signals, and business trends."
    if normalized in {"how are you", "how are you doing"}:
        return "I’m ready to help you explore the local payment simulations. What would you like to know?"
    if any(phrase in text for phrase in ("who created you", "who made you", "who developed you", "who is your creator", "who built you", "founder of paypilot", "creator of paypilot")):
        return "PayPilot AI was created and developed by H B Veeresha. It is an integrated marketplace and payment-intelligence workspace; PayPal checkout runs separately in Sandbox."
    if any(phrase in text for phrase in ("what is your name", "who are you", "your name")):
        return "I’m PayPilot AI, created by H B Veeresha. I’m a local assistant for general conversation and simulated payment intelligence."
    if any(phrase in text for phrase in ("what can you do", "help me", "your capabilities")):
        return "I can summarize transaction totals, list failed or pending simulations, explain a transaction ID, surface unusual risk signals, and describe payment trends. I can’t make payments or expose customer contact details."

    transaction_match = re.search(r"\btxn_[a-zA-Z0-9_-]+\b", message)
    if transaction_match:
        found = next((row for row in context["recent_transactions"] if row["id"].lower() == transaction_match.group(0).lower()), None)
        if not found:
            return "I couldn’t find that transaction ID in the available records. Check the ID in Transactions and try again."
        if any(word in text for word in ("why", "risk", "risky", "fraud", "unusual")):
            reasons = "; ".join(found.get("risk_reasons", [])) or "No notable risk reasons were recorded."
            return f"Transaction {found['id']} has a {found['risk_level']} simulated risk score of {found['risk_score']}/100. Recorded reasons: {reasons} This is a review signal, not proof of fraud."
        return f"Transaction {found['id']} is a simulated {found['status']} payment for {found['currency']} {found['amount']:,.2f}. Customer names and contact details are omitted."

    if re.search(r"\b(failed|failure|declined)\b", text):
        count = context["today_statuses"]["failed"] if "today" in text else context["statuses"]["failed"]
        records = context["today_transactions"]["failed"] if "today" in text else context["failed_transactions"]
        return f"There are {count} failed simulated payments{' today' if 'today' in text else ''}.\n{_transaction_list(records)}"
    if re.search(r"\b(pending|awaiting)\b", text):
        count = context["today_statuses"]["pending"] if "today" in text else context["statuses"]["pending"]
        records = context["today_transactions"]["pending"] if "today" in text else context["pending_transactions"]
        return f"There are {count} pending simulated payments{' today' if 'today' in text else ''}.\n{_transaction_list(records)}"
    if re.search(r"\b(successful|succeeded|approved)\b", text):
        count = context["today_statuses"]["successful"] if "today" in text else context["statuses"]["successful"]
        records = context["today_transactions"]["successful"] if "today" in text else context["successful_transactions"]
        return f"There are {count} successful simulated payments{' today' if 'today' in text else ''}.\n{_transaction_list(records)}"
    if any(word in text for word in ("suspicious", "unusual", "fraud", "high risk", "risk")):
        return (
            f"I found {context['high_risk_count']} high-risk transaction(s). The top unusual records are:\n"
            f"{_transaction_list(context['suspicious_transactions'])}\nRisk scores are review signals, not proof of fraud."
        )
    if re.search(r"\b(how many|count|number of)\b.*\btransactions?\b|\btransactions?\s+do i have\b", text):
        return f"There are {context['transaction_count']} simulated transactions in this workspace."
    if any(word in text for word in ("volume", "total amount", "total value")):
        return f"Total simulated transaction volume by currency: {_format_totals(context['volume_by_currency'])}. No currency conversion is applied."
    if any(word in text for word in ("trend", "trends", "over time", "increasing", "decreasing")):
        activity = context["daily_activity"]
        older = sum(item["count"] for item in activity[:7])
        newer = sum(item["count"] for item in activity[7:])
        direction = "up" if newer > older else "down" if newer < older else "steady"
        return f"Over the last 14 days, transaction counts were {direction}: {older} in the first 7 days and {newer} in the most recent 7 days. This comparison uses actual database records."
    if "today" in text:
        statuses = context["today_statuses"]
        return (
            f"Today’s local records contain {context['today_count']} simulated transactions "
            f"with volume {_format_totals(context['today_volume_by_currency'])}: {statuses['successful']} succeeded, "
            f"{statuses['pending']} are pending, and {statuses['failed']} failed."
        ) if context["today_count"] else "There are no simulated transactions recorded today yet."
    if any(word in text for word in ("summar", "activity", "performance", "report", "business")):
        statuses = context["statuses"]
        return (
            f"Business summary: {context['transaction_count']} simulated transactions, "
            f"volume {_format_totals(context['volume_by_currency'])}, {statuses['successful']} successful, "
            f"{statuses['pending']} pending, {statuses['failed']} failed, and {context['high_risk_count']} high-risk signals."
        )
    if re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", message):
        return "I can’t retrieve or repeat customer contact details in chat. I can still summarize the transaction by ID or status."
    return "I’m PayPilot AI. Ask me about transaction counts, volume, failed or pending payments, unusual activity, a transaction ID, or payment trends. I never execute payments."


def _strip_model_thinking(text: str) -> str:
    """Remove Qwen-style private reasoning tags before returning model output."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _assistant_messages(message: str, context: dict, history: list[dict] | None, language: str, style: str) -> list[dict]:
    messages = [
        {
            "role": "system",
            "content": (
                "You are PayPilot AI, a friendly, intelligent, professional general-purpose conversational assistant. "
                "Project creator/developer: H B Veeresha. If asked who created, built, or developed PayPilot AI, answer "
                "that it was created by H B Veeresha. Do not invent a company, team, co-founder, or CEO, and do not "
                "describe the creator as CEO unless independently established. "
                "You can discuss general knowledge, education, technology, explanations, casual conversation, and payments. "
                "Infer intended meaning from typos, informal wording, abbreviations, or missing punctuation; do not correct spelling unless asked. "
                f"A lightweight detector suggests language code {language} with writing style {style}; treat this as a hint, not a constraint. "
                "Infer the actual language from the user's message when the detector is uncertain, and honor any explicit language request. "
                "Reply in the user's language and script. For Kannada written in Latin letters, reply naturally in Kannada transliteration; "
                "for native script, reply in that script. Follow a user's natural language change and keep the language for terse follow-ups. "
                "You are the intelligent workspace assistant for PayPilot AI. "
                "PayPilot AI was created by H B Veeresha; do not claim the creator made Qwen or Ollama. "
                "The workspace can contain local transaction, marketplace, order, inventory, and risk records. "
                "Some transaction records are simulations. PayPal Sandbox is a separate provider integration and is never implied by a local record. "
                "Use only facts in the supplied database context. Never invent transaction IDs, counts, amounts, "
                "customers, orders, products, or activity. Do not reveal email addresses, IP addresses, passwords, "
                "authentication tokens, or payment credentials. "
                "If a requested fact is missing, say so. Never initiate or capture a payment unless the signed-in customer explicitly requests that Sandbox action; the customer must approve checkout on PayPal. Never claim local records are real provider payments. "
                "Keep answers natural, clear, and concise. Use conversation history to resolve follow-up questions. "
                "The supplied database context is authoritative only for the records it names; do not force it into general questions."
            ),
        },
        {"role": "system", "content": f"Current local database context (JSON): {json.dumps(context)}"},
    ]
    messages.extend(
        {"role": item["role"], "content": item["content"]}
        for item in (history or [])[-10:]
        if item.get("role") in {"user", "assistant"} and item.get("content")
    )
    messages.append({"role": "user", "content": message})
    return messages


def ollama_reply(
    message: str,
    context: dict,
    history: list[dict] | None = None,
    language: str = "en",
    style: str = "latin",
) -> tuple[str, str]:
    settings = get_settings()
    messages = _assistant_messages(message, context, history, language, style)
    payload = {"model": settings.ollama_model, "stream": False, "messages": messages}
    request = urllib.request.Request(
        f"{settings.ollama_base_url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.ollama_timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
        answer = _strip_model_thinking(result.get("message", {}).get("content", ""))
        if answer:
            return answer, settings.ollama_model
    except (OSError, urllib.error.URLError, http.client.HTTPException, TimeoutError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
        pass
    detected_language, style = resolve_language(message, history)
    answer = general_fallback(message, detected_language, style, context, history)
    return answer, "deterministic-fallback"


def _ollama_json(payload: dict) -> dict:
    settings = get_settings()
    request = urllib.request.Request(
        f"{settings.ollama_base_url.rstrip('/')}/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=settings.ollama_timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _safe_model_history(db: Session, history: list[dict]) -> list[dict]:
    return [{"role": item["role"], "content": _redact_private_text(db, item["content"])} for item in history[-10:]]


MAX_TOOL_CALLS_PER_TURN = 4


def _ollama_plan_calls(db: Session, message: str, history: list[dict], actor: WorkspaceActor, language: str, style: str, schemas: list[dict]) -> list[tuple[str, dict]] | None:
    """Ask Ollama which authorized tool(s), if any, are needed for this request.

    Returns a list of (tool_name, arguments) pairs -- possibly more than one,
    since a single model response can request several tools at once when a
    question needs information combined from more than one module (e.g. "give
    me the business overview"). Returns an empty list when the model decides
    no tool is needed (plain conversation). Returns None when Ollama could not
    be reached or returned something unusable, so the caller can fall back.
    """
    if not schemas:
        return None
    settings = get_settings()
    safe_message = _redact_private_text(db, message)
    messages = _assistant_messages(safe_message, {"available_source": "authorized local workspace tools only"}, _safe_model_history(db, history), language, style)
    messages[0]["content"] += (
        " You have direct access to PayPilot's authorized backend tools listed in this request; this is the same "
        "allowlist the rest of the application uses, so you already know every module (dashboard, marketplace, "
        "inventory, customers, cart, orders, transactions, fraud, analytics, reports, PayPal Sandbox) through it -- "
        "you do not need a hardcoded list of example questions. When the latest request needs PayPilot records, "
        "call every relevant tool needed to answer it completely (call more than one tool in this turn if the "
        "question spans more than one module, such as a combined business overview). Choose read-only tools unless "
        "the user explicitly asked for a payment action. Do not guess tool results and do not request secrets. "
        "For general conversation that needs no workspace data, respond without calling any tool."
    )
    try:
        result = _ollama_json({"model": settings.ollama_model, "stream": False, "messages": messages, "tools": schemas})
        calls = result.get("message", {}).get("tool_calls") or []
        planned: list[tuple[str, dict]] = []
        for call in calls[:MAX_TOOL_CALLS_PER_TURN]:
            function = call.get("function", {})
            name = function.get("name")
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    continue
            if name not in TOOL_SPECS or actor.role not in TOOL_SPECS[name].roles or not isinstance(arguments, dict):
                continue
            if TOOL_SPECS[name].side_effect:
                # Payment-initiating tools stay deterministic-only (see _resolve_workspace_tool):
                # never let a model-selected plan trigger a Sandbox checkout or capture.
                continue
            if name == "search_customers":
                # Customer-directory lookups also stay deterministic-only: the model's
                # privacy-redacted context excludes customer identities, so letting it
                # pick its own search arguments here could search for or mismatch a
                # real name it never actually saw. The regex-based deterministic
                # matcher in deterministic_tool() is the only path to this tool.
                continue
            planned.append((name, arguments))
        return planned
    except (OSError, urllib.error.URLError, http.client.HTTPException, TimeoutError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return None


def _safe_tool_result_for_model(db: Session, result: dict) -> dict:
    # Tool artifacts stay available to the authorized UI. The LLM receives a
    # redacted copy without customer identity, provider order IDs, or secrets.
    serialized = _redact_private_text(db, json.dumps(result, ensure_ascii=False))
    try:
        safe = json.loads(serialized)
    except json.JSONDecodeError:
        safe = {"source": result.get("source", "workspace"), "summary": "Verified database results are attached in the UI."}
    return safe


def _ollama_explain_tools(db: Session, message: str, history: list[dict], named_results: list[tuple[str, dict]], language: str, style: str) -> tuple[str, str] | None:
    """Explain one or several verified tool results in one natural-language answer.

    named_results is [(tool_name, result_dict), ...]. With exactly one result
    this keeps the original single-result context shape (verified_tool_result);
    with several it exposes each tool's result under its own key so the model
    can compare or combine them (e.g. a combined business overview spanning
    transactions, marketplace and fraud) without ever inventing new figures.
    """
    settings = get_settings()
    safe_message = _redact_private_text(db, message)
    safe_history = _safe_model_history(db, history)
    if len(named_results) == 1:
        context = {"verified_tool_result": _safe_tool_result_for_model(db, named_results[0][1])}
    else:
        context = {"verified_tool_results": {name: _safe_tool_result_for_model(db, result) for name, result in named_results}}
    messages = _assistant_messages(safe_message, context, safe_history, language, style)
    messages[0]["content"] += (
        " Database facts are returned by validated local tools and appear as verified_tool_result or "
        "verified_tool_results. Explain only those facts, do not add new amounts, IDs, statuses, or identities, "
        "and keep transaction volume separate from marketplace order value. When more than one tool result is "
        "present, combine or compare them as the question asked. State when a record is simulated or only "
        "locally recorded."
    )
    try:
        response = _ollama_json({"model": settings.ollama_model, "stream": False, "messages": messages})
        answer = _strip_model_thinking(response.get("message", {}).get("content", ""))
        return (answer, settings.ollama_model) if answer else None
    except (OSError, urllib.error.URLError, http.client.HTTPException, TimeoutError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return None


def _start_turn(db: Session, message: str, conversation_id: str | None, session_key: str | None, actor: WorkspaceActor | None = None):
    actor = actor or WorkspaceActor("admin")
    conversation = (
        db.scalar(
            select(AssistantConversation).where(
                AssistantConversation.conversation_id == conversation_id,
                AssistantConversation.session_key == session_key,
                AssistantConversation.actor_key == actor.key,
            )
        )
        if conversation_id
        else None
    )
    if conversation is None and conversation_id and actor.role == "admin":
        # Existing V3 conversations had no actor binding. Claim only a matching
        # session-key conversation when it is accessed by the local admin.
        conversation = db.scalar(select(AssistantConversation).where(
            AssistantConversation.conversation_id == conversation_id,
            AssistantConversation.session_key == session_key,
            AssistantConversation.actor_key.is_(None),
        ))
        if conversation is not None:
            conversation.actor_key = actor.key
    if conversation is None:
        conversation = AssistantConversation(
            conversation_id=f"chat_{uuid4().hex}",
            session_key=session_key,
            actor_key=actor.key,
            title="PayPilot AI chat",
        )
        db.add(conversation)
        db.flush()
    prior_messages = list(
        db.scalars(
            select(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation.conversation_id)
            .order_by(AssistantMessage.created_at.desc(), AssistantMessage.id.desc())
            .limit(10)
        )
    )
    history = [{"role": item.role, "content": item.content} for item in reversed(prior_messages)]
    try:
        context = json.loads(conversation.context_json or "{}")
        if not isinstance(context, dict):
            context = {}
    except (TypeError, json.JSONDecodeError):
        context = {}
    return conversation, history, context


def _record_turn(db: Session, conversation: AssistantConversation | str, message: str, answer: str, model: str, metadata: dict | None = None, context: dict | None = None) -> None:
    conversation_id = conversation.conversation_id if isinstance(conversation, AssistantConversation) else conversation
    db.add_all(
        [
            AssistantMessage(conversation_id=conversation_id, role="user", content=message, model="user"),
            AssistantMessage(conversation_id=conversation_id, role="assistant", content=answer, model=model, metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None),
        ]
    )
    if isinstance(conversation, AssistantConversation) and context is not None:
        conversation.context_json = json.dumps(context, ensure_ascii=False)
    db.commit()


def _response_source(model: str) -> str:
    if model == "sqlite-grounded":
        return "database"
    if model == "deterministic-fallback":
        return "fallback"
    return "ollama"


def _legacy_artifact(intent: str, context: dict) -> dict:
    if intent in {"failed", "pending", "successful"}:
        rows = context.get(f"{intent}_transactions", [])
        if any(word in context.get("requested_text", "").casefold() for word in ("today", "ಇಂದು", "आज", "இன்று", "ఈరోజు", "ഇന്ന്")):
            rows = context.get("today_transactions", {}).get(intent, [])
        rows = [{"transaction_id": row["id"], "status": row["status"], "amount": row["amount"], "currency": row["currency"], "risk_level": row["risk_level"]} for row in rows]
        return {"source": "Transactions database", "title": f"{intent.title()} transactions", "kind": "table", "rows": rows}
    if intent == "risk":
        rows = [{"transaction_id": row["id"], "status": row["status"], "amount": row["amount"], "currency": row["currency"], "risk_level": row["risk_level"], "risk_score": row["risk_score"]} for row in context.get("suspicious_transactions", [])]
        return {"source": "Fraud Intelligence", "title": "Risk signals", "kind": "table", "rows": rows}
    if intent in {"summary", "count", "volume", "today", "trend"}:
        rows = [{"currency": currency, "volume": total} for currency, total in context.get("volume_by_currency", {}).items()]
        return {"source": "Transactions database", "title": "Transaction summary", "kind": "table", "rows": rows}
    return {"source": "Transactions database", "title": "Transaction information", "kind": "table", "rows": []}


def _execute_planned_tool(db: Session, actor: WorkspaceActor, name: str, arguments: dict, context: dict) -> dict | None:
    """Run one tool call and translate backend errors into a reply dict, or
    return None on success (caller reads the return value separately)."""
    try:
        return {"ok": True, "result": execute_tool(db, actor, name, arguments)}
    except PermissionError:
        return {"ok": False, "reply": {
            "reply": "I can discuss general topics, but this workspace data is not available to your current account. Sign in with the appropriate role or use the local administrator workspace.",
            "model": "deterministic-fallback", "source": "permission", "source_label": "Access control",
            "artifacts": [], "conversation_context": {},
        }}
    except ValueError as exc:
        return {"ok": False, "reply": {
            "reply": str(exc), "model": "deterministic-fallback", "source": "fallback",
            "source_label": "Local fallback · limited understanding", "artifacts": [], "conversation_context": context,
        }}
    except Exception as exc:
        from fastapi import HTTPException
        if not isinstance(exc, HTTPException):
            raise
        detail = exc.detail if isinstance(exc.detail, str) else "The requested operation could not be completed."
        return {"ok": False, "reply": {
            "reply": detail, "model": "sqlite-grounded", "source": "payment",
            "source_label": "PayPal Sandbox · backend validation", "artifacts": [], "conversation_context": context,
        }}


def _resolve_workspace_tool(db: Session, message: str, history: list[dict], context: dict, actor: WorkspaceActor, language: str, style: str) -> dict | None:
    """The single orchestration entrypoint connecting user intent, the
    authorized tool catalog, and the database for every data/action request.

    Priority:
    1. Deterministic customer-directory lookups and payment-initiating (side
       effect) actions never go through the model -- they are either privacy
       sensitive (the model's redacted context excludes customer identities)
       or financially sensitive (checkout/capture must only fire on an exact,
       unambiguous keyword match, never a model's best guess).
    2. Otherwise, when Ollama is reachable, it is asked FIRST and directly --
       not as a refinement of a keyword guess -- which tool(s) from the
       complete role-scoped catalog (tool_schemas) the request needs. A
       single model turn can request more than one tool, which is how a
       cross-module question ("give me the business overview") gets answered
       in one turn instead of needing a hardcoded combined handler.
    3. If Ollama is unreachable, returns no plan, or every planned call fails
       permission/validation, the original deterministic keyword matcher
       (deterministic_tool) is used as the offline fallback, exactly as
       before -- this keeps the assistant answering from the database even
       with no local model running.
    """
    deterministic_candidate = deterministic_tool(message, actor, context)
    if deterministic_candidate is not None:
        candidate_name = deterministic_candidate[0]
        if TOOL_SPECS[candidate_name].side_effect or candidate_name == "search_customers":
            return _run_tool_calls(db, actor, [deterministic_candidate], message, history, context, language, style, allow_explain=False)

    schemas = tool_schemas(actor)
    if schemas:
        planned_calls = _ollama_plan_calls(db, message, history, actor, language, style, schemas)
        if planned_calls:
            return _run_tool_calls(db, actor, planned_calls, message, history, context, language, style, allow_explain=True)
        if planned_calls == []:
            # The model explicitly decided no workspace tool is needed (plain
            # conversation). Respect that rather than forcing a keyword match.
            return None

    if deterministic_candidate is not None:
        return _run_tool_calls(db, actor, [deterministic_candidate], message, history, context, language, style, allow_explain=False)
    return None


def _run_tool_calls(
    db: Session, actor: WorkspaceActor, calls: list[tuple[str, dict]], message: str, history: list[dict],
    context: dict, language: str, style: str, allow_explain: bool,
) -> dict:
    results: list[tuple[str, dict]] = []
    artifacts: list[dict] = []
    for name, arguments in calls:
        outcome = _execute_planned_tool(db, actor, name, arguments, context)
        if not outcome["ok"]:
            # Stop at the first failure (permission/validation/payment error) and
            # surface it directly rather than silently dropping it or guessing.
            if results:
                break
            return outcome["reply"]
        result = outcome["result"]
        results.append((name, result))
        if result.get("artifact"):
            artifacts.append(result["artifact"])
    if not results:
        return None

    answer = None
    model = "sqlite-grounded"
    if allow_explain:
        explained = _ollama_explain_tools(db, message, history, results, language, style)
        if explained:
            answer, model = explained
    if not answer:
        # Offline / explain-unavailable fallback: concatenate each tool's own
        # deterministic one-line summary rather than leaving a blank reply.
        answer = "\n".join(format_tool_result(message, result, language) for _, result in results)

    primary_source = results[0][1].get("source", "PayPilot workspace database")
    return {
        "reply": answer,
        "model": model,
        "source": "database" if model == "sqlite-grounded" else "ollama",
        "source_label": primary_source + (" · local fallback" if model == "sqlite-grounded" else ""),
        "artifacts": artifacts,
        "conversation_context": {"tool": calls[-1][0], "args": calls[-1][1]},
    }


def chat(db: Session, message: str, conversation_id: str | None = None, session_key: str | None = None, actor: WorkspaceActor | None = None) -> dict:
    actor = actor or WorkspaceActor("admin")
    conversation, history, conversation_context = _start_turn(db, message, conversation_id, session_key, actor)
    name_prefix = _customer_name_prefix(message)
    transaction_name_prefix = name_prefix if any(word in message.casefold() for word in ("transaction", "payment")) else None
    intent, language = detect_payment_intent(message, history)
    language, style = resolve_language(message, history)
    metadata: dict | None = None
    next_context = conversation_context
    context_reply = _context_awareness_reply(db, message, actor)
    tool_turn = _resolve_workspace_tool(db, message, history, conversation_context, actor, language, style) if not transaction_name_prefix and context_reply is None else None
    if context_reply is not None:
        answer, model = context_reply, "sqlite-grounded"
        source, source_label = "database", "Workspace integration status"
    elif tool_turn:
        answer, model = tool_turn["reply"], tool_turn["model"]
        source, source_label = tool_turn["source"], tool_turn["source_label"]
        metadata = {"artifacts": tool_turn["artifacts"], "source_label": source_label}
        next_context = tool_turn["conversation_context"]
    elif transaction_name_prefix:
        answer = _customer_prefix_reply(db, transaction_name_prefix, actor)
        model = "sqlite-grounded"
        rows = list(db.scalars(transaction_scope(select(Transaction), actor).where(Transaction.customer_name.ilike(f"{transaction_name_prefix}%")).order_by(Transaction.created_at.desc()).limit(20)))
        artifact_rows = [{"customer_name": tx.customer_name, "transaction_id": tx.transaction_id, "amount": round(tx.amount, 2), "currency": tx.currency, "status": tx.status} for tx in rows] if actor.role == "admin" else []
        metadata = {"artifacts": [{"source": "Transactions database", "title": "Transactions by customer-name initial", "kind": "table", "rows": artifact_rows}], "source_label": "Transactions database · local fallback"}
        source, source_label = "database", "Transactions database · local fallback"
    elif intent:
        match = re.search(r"\btxn_[a-zA-Z0-9_-]+\b", message)
        context = build_context(db, match.group(0) if match else None, actor)
        context["requested_text"] = message
        if re.sub(r"[^\w\s]", "", message.casefold()).strip() in {"and", "why", "how many", "more", "tell me more", "continue", "and why"}:
            context["previous_text"] = next((item["content"] for item in reversed(history) if item.get("role") == "user"), "")
        answer = financial_fallback(intent, context, language, style)
        normalized = re.sub(r"[^\w\s]", "", message.casefold()).strip()
        if normalized in {"and", "why", "how many", "more", "tell me more", "continue"}:
            answer = f"Following up: {answer}"
        model = "sqlite-grounded"
        metadata = {"artifacts": [_legacy_artifact(intent, context)]}
        next_context = {"tool": "get_transaction_summary", "args": {"period": "today" if "today" in message.casefold() else "all"}}
    else:
        if tool_turn:
            answer, model = tool_turn["reply"], tool_turn["model"]
            source, source_label = tool_turn["source"], tool_turn["source_label"]
            metadata = {"artifacts": tool_turn["artifacts"], "source_label": source_label}
            next_context = tool_turn["conversation_context"]
        else:
            context = {"financial_context_attached": False}
            safe_message = _redact_private_text(db, message)
            safe_history = _safe_model_history(db, history)
            answer, model = ollama_reply(safe_message, context, safe_history, language, style)
            source = _response_source(model)
            source_label = "General conversation" if model != "deterministic-fallback" else "Local fallback · limited understanding"
    if context_reply is None and not tool_turn and (transaction_name_prefix or intent):
        source = "database"
        if transaction_name_prefix:
            source_label = "Transactions database · local fallback"
        else:
            source_label = "Fraud Intelligence" if intent == "risk" else "Transactions database"
    _record_turn(db, conversation, message, answer, model, metadata, next_context)
    return {
        "conversation_id": conversation.conversation_id,
        "reply": answer,
        "model": model,
        "used_fallback": model in {"deterministic-fallback", "sqlite-grounded"},
        "source": source,
        "source_label": source_label,
        "artifacts": metadata.get("artifacts", []) if metadata else [],
    }


def stream_chat(db: Session, message: str, conversation_id: str | None = None, session_key: str | None = None, actor: WorkspaceActor | None = None):
    """Yield newline-delimited JSON chunks and store the completed turn."""
    actor = actor or WorkspaceActor("admin")
    conversation, history, conversation_context = _start_turn(db, message, conversation_id, session_key, actor)
    name_prefix = _customer_name_prefix(message)
    transaction_name_prefix = name_prefix if any(word in message.casefold() for word in ("transaction", "payment")) else None
    intent, language = detect_payment_intent(message, history)
    language, style = resolve_language(message, history)
    model = "sqlite-grounded"
    answer_parts: list[str] = []
    model_chunks: list[str] = []
    metadata: dict | None = None
    next_context = conversation_context
    source_label = "General conversation"
    context: dict = {"financial_context_attached": False}

    def event(payload: dict) -> str:
        return json.dumps(payload, ensure_ascii=False) + "\n"

    context_reply = _context_awareness_reply(db, message, actor)
    tool_turn = _resolve_workspace_tool(db, message, history, conversation_context, actor, language, style) if not transaction_name_prefix and context_reply is None else None
    if context_reply is not None:
        answer = context_reply
        model = "sqlite-grounded"
        source_label = "Workspace integration status"
        for start in range(0, len(answer), 96):
            chunk = answer[start:start + 96]
            answer_parts.append(chunk)
            yield event({"type": "chunk", "text": chunk})
    elif tool_turn:
        answer = tool_turn["reply"]
        model = tool_turn["model"]
        source_label = tool_turn["source_label"]
        metadata = {"artifacts": tool_turn["artifacts"], "source_label": source_label}
        next_context = tool_turn["conversation_context"]
        for start in range(0, len(answer), 96):
            chunk = answer[start:start + 96]
            answer_parts.append(chunk)
            yield event({"type": "chunk", "text": chunk})
    elif transaction_name_prefix:
        answer = _customer_prefix_reply(db, transaction_name_prefix, actor)
        model = "sqlite-grounded"
        source_label = "Transactions database · local fallback"
        rows = list(db.scalars(transaction_scope(select(Transaction), actor).where(Transaction.customer_name.ilike(f"{transaction_name_prefix}%")).order_by(Transaction.created_at.desc()).limit(20)))
        artifact_rows = [{"customer_name": tx.customer_name, "transaction_id": tx.transaction_id, "amount": round(tx.amount, 2), "currency": tx.currency, "status": tx.status} for tx in rows] if actor.role == "admin" else []
        metadata = {"artifacts": [{"source": "Transactions database", "title": "Transactions by customer-name initial", "kind": "table", "rows": artifact_rows}], "source_label": source_label}
        answer_parts.append(answer)
        yield event({"type": "chunk", "text": answer})
    elif intent:
        match = re.search(r"\btxn_[a-zA-Z0-9_-]+\b", message)
        context = build_context(db, match.group(0) if match else None, actor)
        context["requested_text"] = message
        if re.sub(r"[^\w\s]", "", message.casefold()).strip() in {"and", "why", "how many", "more", "tell me more", "continue", "and why"}:
            context["previous_text"] = next((item["content"] for item in reversed(history) if item.get("role") == "user"), "")
        answer = financial_fallback(intent, context, language, style)
        if re.sub(r"[^\w\s]", "", message.casefold()).strip() in {"and", "why", "how many", "more", "tell me more", "continue"}:
            answer = f"Following up: {answer}"
        metadata = {"artifacts": [_legacy_artifact(intent, context)]}
        next_context = {"tool": "get_transaction_summary", "args": {"period": "today" if "today" in message.casefold() else "all"}}
        source_label = "Fraud Intelligence" if intent == "risk" else "Transactions database"
        answer_parts.append(answer)
        yield event({"type": "chunk", "text": answer})
    else:
        if tool_turn:
            answer = tool_turn["reply"]
            model = tool_turn["model"]
            source_label = tool_turn["source_label"]
            metadata = {"artifacts": tool_turn["artifacts"], "source_label": source_label}
            next_context = tool_turn["conversation_context"]
            for start in range(0, len(answer), 96):
                chunk = answer[start:start + 96]
                answer_parts.append(chunk)
                yield event({"type": "chunk", "text": chunk})
        else:
            model = "deterministic-fallback"
            settings = get_settings()
            safe_message = _redact_private_text(db, message)
            safe_history = _safe_model_history(db, history)
            payload = {"model": settings.ollama_model, "stream": True, "messages": _assistant_messages(safe_message, context, safe_history, language, style)}
            request = urllib.request.Request(
                f"{settings.ollama_base_url.rstrip('/')}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=settings.ollama_timeout_seconds) as response:
                    for raw_line in response:
                        if not raw_line.strip():
                            continue
                        frame = json.loads(raw_line.decode("utf-8"))
                        chunk = frame.get("message", {}).get("content", "")
                        if chunk:
                            model = settings.ollama_model
                            model_chunks.append(chunk)
                if model_chunks:
                    raw_answer = "".join(model_chunks)
                    visible_answer = _strip_model_thinking(raw_answer)
                    if visible_answer:
                        if "<think>" in raw_answer.casefold():
                            answer_parts.append(visible_answer)
                            yield event({"type": "chunk", "text": visible_answer})
                        else:
                            answer_parts.extend(model_chunks)
                            for visible_chunk in model_chunks:
                                yield event({"type": "chunk", "text": visible_chunk})

            except (OSError, urllib.error.URLError, http.client.HTTPException, TimeoutError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
                if answer_parts:
                    answer_parts.append("\n\n(The local model stream was interrupted.)")
                    yield event({"type": "warning", "text": "The local model stream was interrupted."})
                else:
                    answer = general_fallback(message, language, style, context, history)
                    answer_parts.append(answer)
                    yield event({"type": "chunk", "text": answer})
                    source_label = "Local fallback · limited understanding"

    answer = "".join(answer_parts)
    if not answer:
        answer = general_fallback(message, language, style, context, history)
        answer_parts.append(answer)
        yield event({"type": "chunk", "text": answer})
    if not source_label or source_label == "General conversation" and model == "deterministic-fallback":
        source_label = "Local fallback · limited understanding"
    _record_turn(db, conversation, message, answer, model, metadata, next_context)
    yield event({
        "type": "done",
        "conversation_id": conversation.conversation_id,
        "model": model,
        "used_fallback": model in {"deterministic-fallback", "sqlite-grounded"},
        "source": _response_source(model),
        "source_label": source_label,
        "artifacts": metadata.get("artifacts", []) if metadata else [],
    })
