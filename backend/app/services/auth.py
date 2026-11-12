import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MarketplaceSession, MarketplaceUser


SESSION_COOKIE = "paypilot_market_session"
SESSION_DAYS = 14


def hash_password(password: str) -> str:
    """Hash a password with scrypt using a fresh per-password salt."""
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64)
    encode = lambda value: base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
    return f"scrypt$16384$8$1${encode(salt)}${encode(derived)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = stored.split("$", maxsplit=5)
        if algorithm != "scrypt":
            return False
        decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        expected = decode(digest_text)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=decode(salt_text),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, MemoryError):
        return False


def create_session(db: Session, user: MarketplaceUser) -> tuple[str, datetime]:
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    db.add(MarketplaceSession(user_id=user.id, token_hash=token_hash, expires_at=expires_at))
    db.commit()
    return raw_token, expires_at


def session_for_token(db: Session, raw_token: str | None) -> MarketplaceSession | None:
    if not raw_token or len(raw_token) > 128:
        return None
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    return db.scalar(
        select(MarketplaceSession)
        .where(MarketplaceSession.token_hash == token_hash, MarketplaceSession.expires_at > datetime.now(timezone.utc))
    )


def public_user(user: MarketplaceUser) -> dict:
    return {"id": user.id, "name": user.full_name, "email": user.email, "role": user.role}
