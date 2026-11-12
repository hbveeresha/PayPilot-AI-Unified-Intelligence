from datetime import datetime, timedelta, timezone
from random import Random
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import SimulatedOrder, Transaction
from app.services.risk import assess_transaction


def seed_demo_data(db: Session, count: int = 48) -> int:
    if (db.scalar(select(func.count(Transaction.id))) or 0) > 0:
        return 0
    rng = Random(4207)
    names = ["Alex Morgan", "Jordan Lee", "Taylor Kim", "Riley Shah", "Casey Reed", "Avery Patel", "Sam Rivera", "Jamie Chen"]
    statuses = ["successful", "successful", "successful", "pending", "failed"]
    now = datetime.now(timezone.utc)
    for index in range(count):
        amount = round(rng.uniform(18, 680), 2)
        if index in (9, 33):
            amount = round(rng.uniform(2800, 4900), 2)
        name = names[index % len(names)]
        email = f"customer{index % len(names)}@example.test"
        status = "failed" if index in (11, 24, 38) else rng.choice(statuses)
        description = "Local synthetic demo transaction"
        created_at = now - timedelta(days=rng.randrange(0, 28), hours=rng.randrange(0, 24))
        if index in (40, 41, 42, 43):
            name = "Synthetic review sample"
            email = "review-sample@example.test"
            amount = 3210.0 if index == 43 else 72.0 + index - 40
            status = "failed" if index in (42, 43) else "successful"
            created_at = now - timedelta(hours=44 - index)
            description = "Synthetic repeated-attempt risk example"
        order_id = f"ord_demo_{uuid4().hex[:12]}"
        order = SimulatedOrder(order_id=order_id, amount=amount, currency="USD", description="Local demo order", status="completed" if status == "successful" else status)
        db.add(order)
        db.flush()
        tx = Transaction(
            transaction_id=f"txn_demo_{uuid4().hex[:14]}",
            order_id=order_id,
            customer_name=name,
            customer_email=email,
            amount=amount,
            currency="USD",
            status=status,
            payment_method="Simulated card",
            description=description,
            ip_address="192.0.2.10",
            created_at=created_at,
        )
        db.add(tx)
        db.flush()
        assess_transaction(db, tx)
    db.commit()
    return count
