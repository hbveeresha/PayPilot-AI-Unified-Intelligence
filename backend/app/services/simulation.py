from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import SimulatedOrder, Transaction
from app.schemas.api import SimulationCreate
from app.services.risk import assess_transaction


def create_simulation(db: Session, payload: SimulationCreate) -> tuple[Transaction, object]:
    order_id = f"ord_{uuid4().hex[:16]}"
    transaction_id = f"txn_{uuid4().hex[:20]}"
    status = {"approved": "successful", "failed": "failed", "pending": "pending"}[payload.outcome]
    order = SimulatedOrder(
        order_id=order_id,
        amount=payload.amount,
        currency=payload.currency.upper(),
        description=payload.description,
        status="completed" if status == "successful" else status,
    )
    db.add(order)
    db.flush()
    transaction = Transaction(
        transaction_id=transaction_id,
        order_id=order_id,
        customer_name=payload.customer_name.strip(),
        customer_email=payload.customer_email.strip().lower(),
        amount=round(payload.amount, 2),
        currency=payload.currency.upper(),
        status=status,
        description=payload.description.strip(),
        created_at=datetime.now(timezone.utc),
    )
    db.add(transaction)
    db.flush()
    risk = assess_transaction(db, transaction)
    db.commit()
    db.refresh(transaction)
    db.refresh(risk)
    return transaction, risk
