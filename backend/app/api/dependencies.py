import hmac

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import get_db
from app.models import MarketplaceUser
from app.services.auth import SESSION_COOKIE, session_for_token
from app.services.permissions import WorkspaceActor, is_loopback_host


def optional_marketplace_user(
    request: Request,
    db: Session = Depends(get_db),
) -> MarketplaceUser | None:
    token = request.cookies.get(SESSION_COOKIE)
    session = session_for_token(db, token)
    if session is None or session.user is None or not session.user.is_active:
        return None
    return session.user


def current_marketplace_user(
    user: MarketplaceUser | None = Depends(optional_marketplace_user),
) -> MarketplaceUser:
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    return user


def roles_required(*roles: str):
    def dependency(user: MarketplaceUser = Depends(current_marketplace_user)) -> MarketplaceUser:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Your account role cannot perform this action")
        return user

    return dependency


def workspace_actor(
    request: Request,
    user: MarketplaceUser | None = Depends(optional_marketplace_user),
) -> WorkspaceActor:
    """Resolve a signed-in marketplace actor or the local workspace administrator."""
    if user is not None:
        return WorkspaceActor(user.role, user.id, user.email, user.full_name)

    settings = get_settings()
    supplied = request.headers.get("X-Workspace-Admin-Token", "")
    configured = settings.workspace_admin_token
    if configured and supplied and hmac.compare_digest(supplied, configured):
        return WorkspaceActor("admin")

    host = request.client.host if request.client else None
    if is_loopback_host(host, settings.app_env):
        return WorkspaceActor("admin")
    return WorkspaceActor("guest")


def require_workspace_data(actor: WorkspaceActor = Depends(workspace_actor)) -> WorkspaceActor:
    if actor.role == "guest":
        raise HTTPException(status_code=403, detail="Sign in to view workspace data")
    return actor


def require_workspace_admin(actor: WorkspaceActor = Depends(workspace_actor)) -> WorkspaceActor:
    if actor.role != "admin":
        raise HTTPException(status_code=403, detail="Workspace administrator access is required")
    return actor
