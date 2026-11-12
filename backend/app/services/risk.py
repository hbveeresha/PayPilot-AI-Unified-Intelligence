import json
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import RiskAssessment, Transaction


def assess_transaction(db: Session, transaction: Transaction) -> RiskAssessment:
    reasons: list[str] = []
    score = 6
    if transaction.amount >= 2500:
        score += 40
        reasons.append("Amount is above the usual demo payment range.")
    elif transaction.amount >= 1000:
        score += 18
        reasons.append("Amount is larger than most simulated payments.")
    if transaction.status == "failed":
        score += 18
        reasons.append("This simulated payment did not complete.")
    window_start = transaction.created_at - timedelta(hours=24)
    frequency_filters = (
        Transaction.customer_email == transaction.customer_email,
        Transaction.created_at >= window_start,
        Transaction.created_at <= transaction.created_at,
    )
    count = db.scalar(select(func.count(Transaction.id)).where(*frequency_filters)) or 0
    if count >= 4:
        score += min(25, (count - 2) * 6)
        reasons.append(f"This customer has {count} simulated attempts in the preceding 24 hours.")
    failed_count = db.scalar(
        select(func.count(Transaction.id)).where(*frequency_filters, Transaction.status == "failed")
    ) or 0
    if failed_count >= 2:
        score += min(24, (failed_count - 1) * 12)
        reasons.append(f"This customer has {failed_count} failed simulated attempts in the preceding 24 hours.")
    hour = transaction.created_at.hour if isinstance(transaction.created_at, datetime) else 12
    if hour < 5 or hour >= 23:
        score += 12
        reasons.append("The transaction was created during a low-activity hour.")
    try:
        from sklearn.ensemble import IsolationForest

        history = list(db.scalars(select(Transaction.amount).where(Transaction.transaction_id != transaction.transaction_id).order_by(Transaction.created_at.desc()).limit(250)))
        if len(history) >= 20:
            detector = IsolationForest(contamination=0.08, random_state=17, n_estimators=80)
            detector.fit([[value] for value in history])
            if detector.predict([[transaction.amount]])[0] == -1:
                score += 14
                reasons.append("Anomaly detection flagged this amount as unusual versus recent simulated activity.")
    except (ImportError, ValueError):
        # Rule-based checks still return an explainable assessment if sklearn is unavailable.
        pass
    if not reasons:
        reasons.append("No notable rule-based indicators were found.")
    score = min(score, 100)
    level = "high" if score >= 65 else "medium" if score >= 35 else "low"
    recommendation = {
        "high": "Review this simulated activity before treating it as trusted.",
        "medium": "Monitor for repeated patterns and verify the context.",
        "low": "No immediate review suggested by the current rules.",
    }[level]
    assessment = RiskAssessment(
        transaction_id=transaction.transaction_id,
        score=score,
        level=level,
        reasons_json=json.dumps(reasons),
        recommendation=recommendation,
    )
    db.add(assessment)
    return assessment


def assessment_dict(row: RiskAssessment) -> dict:
    return {
        "transaction_id": row.transaction_id,
        "score": row.score,
        "level": row.level,
        "reasons": json.loads(row.reasons_json or "[]"),
        "recommendation": row.recommendation,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
