import csv
import io
import json
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database.session import get_db
from app.api.dependencies import require_workspace_admin, require_workspace_data, workspace_actor
from app.models import ApplicationSetting, AssistantConversation, AssistantMessage, RiskAssessment, Transaction
from app.schemas.api import ChatRequest, SettingsUpdate, SimulationCreate, SimulationOut, TransactionSearch
from app.core.config import get_settings
from app.services.assistant import chat, stream_chat
from app.services.risk import assessment_dict
from app.services.simulation import create_simulation
from app.services import paypal
from app.services.permissions import WorkspaceActor, transaction_scope


router = APIRouter(prefix="/api")


def _latest_risk(tx: Transaction) -> RiskAssessment | None:
    return max(tx.risk_assessments, key=lambda row: row.created_at or datetime.min, default=None)


def _transaction_out(tx: Transaction, include_identity: bool = True) -> dict:
    risk = _latest_risk(tx)
    return {
        "transaction_id": tx.transaction_id,
        "order_id": tx.order_id,
        "customer_name": tx.customer_name if include_identity else "",
        "customer_email": tx.customer_email if include_identity else "",
        "amount": tx.amount,
        "currency": tx.currency,
        "status": tx.status,
        "payment_method": tx.payment_method,
        "description": tx.description,
        "created_at": tx.created_at,
        "risk_score": risk.score if risk else 0,
        "risk_level": risk.level if risk else "low",
    }


def _csv_safe(value: object) -> str:
    text = str(value or "")
    if text[:1] in ("=", "+", "-", "@", "\t", "\r", "\n"):
        return "'" + text
    return text


def _message_metadata(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


@router.get("/health")
def health():
    return {"status": "ok", "service": "PayPilot AI", "mode": "simulation-only"}


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), actor: WorkspaceActor = Depends(require_workspace_data)):
    transactions = list(db.scalars(transaction_scope(select(Transaction).options(selectinload(Transaction.risk_assessments)), actor).order_by(Transaction.created_at.desc())))
    status_counts = {s: sum(tx.status == s for tx in transactions) for s in ("successful", "pending", "failed")}
    total = len(transactions)
    risk_rows = [row for tx in transactions for row in tx.risk_assessments]
    high_risk_ids = {row.transaction_id for row in risk_rows if row.level == "high"}
    successful_volume = round(sum(tx.amount for tx in transactions if tx.status == "successful"), 2)
    today = date.today()
    today_rows = [tx for tx in transactions if tx.created_at.date() == today]
    success_rate = round(status_counts["successful"] / total * 100, 1) if total else 0
    insights = []
    if total:
        insights.append(f"Success rate is {success_rate:.1f}% across {total} simulated payments.")
        if status_counts["failed"]:
            insights.append(f"{status_counts['failed']} failed payment(s) may warrant a closer look at the selected scenario inputs.")
        if high_risk_ids:
            insights.append(f"{len(high_risk_ids)} transaction(s) have high risk signals; scores are review cues, not proof of fraud.")
    else:
        insights.append("Create a simulated payment to see activity and data-backed insights here.")
    recent = [_transaction_out(tx, actor.role != "merchant") for tx in transactions[:8]]
    return {
        "totals": {
            "transaction_count": total,
            "volume": round(sum(tx.amount for tx in transactions), 2),
            "successful_volume": successful_volume,
            "successful": status_counts["successful"],
            "pending": status_counts["pending"],
            "failed": status_counts["failed"],
            "high_risk": len(high_risk_ids),
            "success_rate": success_rate,
            "today_count": len(today_rows),
            "today_volume": round(sum(tx.amount for tx in today_rows), 2),
        },
        "recent_transactions": recent,
        "insights": insights,
        "simulation_only": True,
    }


@router.get("/transactions")
def transactions(
    request: Request,
    status: str | None = Query(default=None, pattern="^(successful|pending|failed)$"),
    risk: str | None = Query(default=None, pattern="^(low|medium|high)$"),
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    actor: WorkspaceActor = Depends(require_workspace_data),
):
    if "search" in request.query_params:
        raise HTTPException(status_code=400, detail="Transaction search must be sent in a POST JSON body at /api/transactions/search")
    return _list_transactions(db, None, status, risk, date_from, date_to, page, page_size, actor)


@router.post("/transactions/search")
def search_transactions(payload: TransactionSearch, db: Session = Depends(get_db), actor: WorkspaceActor = Depends(require_workspace_data)):
    return _list_transactions(
        db,
        payload.search,
        payload.status,
        payload.risk,
        payload.date_from,
        payload.date_to,
        payload.page,
        payload.page_size,
        actor,
    )


def _list_transactions(db: Session, search: str | None, status: str | None, risk: str | None, date_from, date_to, page: int, page_size: int, actor: WorkspaceActor | None = None):
    query = transaction_scope(select(Transaction).options(selectinload(Transaction.risk_assessments)), actor or WorkspaceActor("admin"))
    if search:
        term = f"%{search.strip()}%"
        if actor is not None and actor.role == "merchant":
            query = query.where(Transaction.transaction_id.ilike(term))
        else:
            query = query.where(Transaction.transaction_id.ilike(term) | Transaction.customer_name.ilike(term) | Transaction.customer_email.ilike(term))
    if status:
        query = query.where(Transaction.status == status)
    if date_from:
        query = query.where(Transaction.created_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to:
        query = query.where(Transaction.created_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc))
    rows = list(db.scalars(query.order_by(Transaction.created_at.desc())))
    mapped = [_transaction_out(tx, actor is None or actor.role != "merchant") for tx in rows]
    if risk:
        mapped = [row for row in mapped if row["risk_level"] == risk]
    total = len(mapped)
    start = (page - 1) * page_size
    return {"items": mapped[start : start + page_size], "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size}


@router.get("/transactions/{transaction_id}")
def transaction_detail(transaction_id: str, db: Session = Depends(get_db), actor: WorkspaceActor = Depends(require_workspace_data)):
    tx = db.scalar(transaction_scope(select(Transaction).options(selectinload(Transaction.risk_assessments)), actor).where(Transaction.transaction_id == transaction_id))
    if tx is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    risk = _latest_risk(tx)
    return {**_transaction_out(tx, actor.role != "merchant"), "simulated": True, "risk": assessment_dict(risk) if risk else None}


@router.post("/simulations", response_model=SimulationOut, status_code=201)
def simulate(payload: SimulationCreate, db: Session = Depends(get_db), _actor: WorkspaceActor = Depends(require_workspace_admin)):
    tx, risk = create_simulation(db, payload)
    return {"simulated": True, "transaction": _transaction_out(tx), "risk": assessment_dict(risk)}


@router.post("/assistant/chat")
def assistant_chat(
    payload: ChatRequest,
    session_key: str = Header(alias="X-Conversation-Session", pattern=r"^[a-f0-9]{32}$"),
    db: Session = Depends(get_db),
    actor: WorkspaceActor = Depends(workspace_actor),
):
    return chat(db, payload.message, payload.conversation_id, session_key, actor)


@router.post("/assistant/chat/stream")
def assistant_chat_stream(
    payload: ChatRequest,
    session_key: str = Header(alias="X-Conversation-Session", pattern=r"^[a-f0-9]{32}$"),
    db: Session = Depends(get_db),
    actor: WorkspaceActor = Depends(workspace_actor),
):
    return StreamingResponse(
        stream_chat(db, payload.message, payload.conversation_id, session_key, actor),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/assistant/conversations/{conversation_id}")
def assistant_conversation(
    conversation_id: str,
    session_key: str = Header(alias="X-Conversation-Session", pattern=r"^[a-f0-9]{32}$"),
    db: Session = Depends(get_db),
    actor: WorkspaceActor = Depends(workspace_actor),
):
    conversation = db.scalar(
        select(AssistantConversation).where(
            AssistantConversation.conversation_id == conversation_id,
            AssistantConversation.session_key == session_key,
            AssistantConversation.actor_key == actor.key,
        )
    )
    if conversation is None and actor.role == "admin":
        conversation = db.scalar(select(AssistantConversation).where(
            AssistantConversation.conversation_id == conversation_id,
            AssistantConversation.session_key == session_key,
            AssistantConversation.actor_key.is_(None),
        ))
        if conversation is not None:
            conversation.actor_key = actor.key
            db.commit()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = list(
        db.scalars(
            select(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation_id)
            .order_by(AssistantMessage.created_at, AssistantMessage.id)
        )
    )
    return {
        "conversation_id": conversation_id,
        "messages": [
            {"role": row.role, "content": row.content, "model": row.model, "created_at": row.created_at.isoformat(), "metadata": _message_metadata(row.metadata_json)}
            for row in messages
        ],
    }


@router.delete("/assistant/conversations/{conversation_id}", status_code=204)
def delete_assistant_conversation(
    conversation_id: str,
    session_key: str = Header(alias="X-Conversation-Session", pattern=r"^[a-f0-9]{32}$"),
    db: Session = Depends(get_db),
    actor: WorkspaceActor = Depends(workspace_actor),
):
    conversation = db.scalar(
        select(AssistantConversation).where(
            AssistantConversation.conversation_id == conversation_id,
            AssistantConversation.session_key == session_key,
            AssistantConversation.actor_key == actor.key,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(conversation)
    db.commit()
    return Response(status_code=204)


@router.get("/analytics")
def analytics(db: Session = Depends(get_db), actor: WorkspaceActor = Depends(require_workspace_data)):
    rows = list(db.scalars(transaction_scope(select(Transaction).options(selectinload(Transaction.risk_assessments)), actor)))
    today = date.today()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    daily = [{"date": d.isoformat(), "count": 0, "volume": 0.0} for d in days]
    day_map = {item["date"]: item for item in daily}
    for tx in rows:
        key = tx.created_at.date().isoformat()
        if key in day_map:
            day_map[key]["count"] += 1
            day_map[key]["volume"] += tx.amount
    for item in daily:
        item["volume"] = round(item["volume"], 2)
    statuses = {s: sum(tx.status == s for tx in rows) for s in ("successful", "pending", "failed")}
    risk_counts = {level: 0 for level in ("low", "medium", "high")}
    for tx in rows:
        risk = _latest_risk(tx)
        if risk:
            risk_counts[risk.level] += 1
    return {
        "transaction_volume": round(sum(tx.amount for tx in rows), 2),
        "daily_activity": daily,
        "status_breakdown": statuses,
        "risk_distribution": risk_counts,
        "total_transactions": len(rows),
        "simulation_only": True,
    }


@router.get("/risk")
def risk_feed(level: str | None = Query(default=None, pattern="^(low|medium|high)$"), db: Session = Depends(get_db), actor: WorkspaceActor = Depends(require_workspace_data)):
    visible_ids = transaction_scope(select(Transaction.transaction_id), actor)
    query = select(RiskAssessment).order_by(RiskAssessment.created_at.desc())
    query = query.where(RiskAssessment.transaction_id.in_(visible_ids))
    if level:
        query = query.where(RiskAssessment.level == level)
    rows = list(db.scalars(query.limit(200)))
    tx_ids = [row.transaction_id for row in rows]
    tx_map = {tx.transaction_id: tx for tx in db.scalars(select(Transaction).where(Transaction.transaction_id.in_(tx_ids)))} if tx_ids else {}
    return {"items": [{**assessment_dict(row), "transaction": _transaction_out(tx_map[row.transaction_id], actor.role != "merchant") if row.transaction_id in tx_map else None} for row in rows], "total": len(rows)}


@router.get("/reports")
def reports(period: str = Query(default="daily", pattern="^(daily|monthly)$"), db: Session = Depends(get_db), actor: WorkspaceActor = Depends(require_workspace_data)):
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc) if period == "monthly" else datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    query = transaction_scope(select(Transaction).options(selectinload(Transaction.risk_assessments)), actor)
    rows = list(db.scalars(query.where(Transaction.created_at >= start).order_by(Transaction.created_at.desc())))
    statuses = {s: sum(tx.status == s for tx in rows) for s in ("successful", "pending", "failed")}
    return {
        "period": period,
        "from": start.isoformat(),
        "to": now.isoformat(),
        "transaction_count": len(rows),
        "volume": round(sum(tx.amount for tx in rows), 2),
        "successful_volume": round(sum(tx.amount for tx in rows if tx.status == "successful"), 2),
        "statuses": statuses,
        "high_risk_count": sum((_latest_risk(tx).level == "high") for tx in rows if _latest_risk(tx)),
        "items": [_transaction_out(tx, actor.role != "merchant") for tx in rows[:100]],
        "simulation_only": True,
    }


@router.get("/export/transactions.csv")
def export_transactions(db: Session = Depends(get_db), actor: WorkspaceActor = Depends(require_workspace_data)):
    query = transaction_scope(select(Transaction).options(selectinload(Transaction.risk_assessments)), actor)
    rows = list(db.scalars(query.order_by(Transaction.created_at.desc())))
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["transaction_id", "order_id", "customer_name", "customer_email", "amount", "currency", "status", "risk_score", "risk_level", "created_at", "simulated"])
    for tx in rows:
        item = _transaction_out(tx, actor.role != "merchant")
        email = item["customer_email"] if actor.role in {"admin", "customer"} else ""
        writer.writerow([item["transaction_id"], item["order_id"], _csv_safe(item["customer_name"]), _csv_safe(email), item["amount"], item["currency"], item["status"], item["risk_score"], item["risk_level"], item["created_at"].isoformat(), "yes"])
    return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=paypilot-transactions.csv"})


@router.get("/settings")
def get_app_settings(db: Session = Depends(get_db)):
    currency = db.get(ApplicationSetting, "default_currency")
    settings = get_settings()
    return {
        "default_currency": currency.value if currency else "USD",
        "assistant_model": settings.ollama_model,
        "simulation_only": True,
        "paypal_sandbox_enabled": paypal.enabled(),
    }


@router.put("/settings")
def update_app_settings(payload: SettingsUpdate, db: Session = Depends(get_db), _actor: WorkspaceActor = Depends(require_workspace_admin)):
    setting = db.get(ApplicationSetting, "default_currency")
    if setting is None:
        setting = ApplicationSetting(key="default_currency", value=payload.default_currency)
        db.add(setting)
    else:
        setting.value = payload.default_currency
    db.commit()
    return {"default_currency": payload.default_currency, "saved": True}
