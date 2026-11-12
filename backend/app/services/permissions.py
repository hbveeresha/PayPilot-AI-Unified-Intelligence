"""Actor identity and SQL scopes shared by workspace APIs and assistant tools."""

from dataclasses import dataclass
from ipaddress import ip_address

from sqlalchemy import func, or_, select
from sqlalchemy.sql import Select

from app.models import MarketplaceOrder, MarketplaceOrderItem, Product, Transaction


@dataclass(frozen=True)
class WorkspaceActor:
    role: str
    user_id: int | None = None
    email: str | None = None
    display_name: str | None = None

    @property
    def key(self) -> str:
        if self.user_id is not None:
            return f"{self.role}:{self.user_id}"
        return self.role


def transaction_scope(query: Select, actor: WorkspaceActor) -> Select:
    if actor.role == "admin":
        return query
    if actor.role == "customer" and actor.user_id is not None:
        own_order_transactions = select(MarketplaceOrder.transaction_id).where(
            MarketplaceOrder.user_id == actor.user_id,
            MarketplaceOrder.transaction_id.is_not(None),
        )
        email_match = func.lower(Transaction.customer_email) == actor.email.casefold() if actor.email else False
        return query.where(or_(email_match, Transaction.transaction_id.in_(own_order_transactions)))
    if actor.role == "merchant" and actor.user_id is not None:
        owned_transactions = (
            select(MarketplaceOrder.transaction_id)
            .join(MarketplaceOrderItem, MarketplaceOrderItem.order_id == MarketplaceOrder.id)
            .join(Product, Product.id == MarketplaceOrderItem.product_id)
            .where(Product.merchant_id == actor.user_id, MarketplaceOrder.transaction_id.is_not(None))
        )
        return query.where(Transaction.transaction_id.in_(owned_transactions))
    return query.where(False)


def order_scope(query: Select, actor: WorkspaceActor) -> Select:
    if actor.role == "admin":
        return query
    if actor.role == "customer" and actor.user_id is not None:
        return query.where(MarketplaceOrder.user_id == actor.user_id)
    if actor.role == "merchant" and actor.user_id is not None:
        owned_orders = (
            select(MarketplaceOrderItem.order_id)
            .join(Product, Product.id == MarketplaceOrderItem.product_id)
            .where(Product.merchant_id == actor.user_id)
        )
        return query.where(MarketplaceOrder.id.in_(owned_orders))
    return query.where(False)


def is_loopback_host(host: str | None, app_env: str) -> bool:
    if not host:
        return False
    if host.casefold() == "testclient":
        return app_env.casefold() in {"development", "test"}
    try:
        return ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return host.casefold() == "localhost"
