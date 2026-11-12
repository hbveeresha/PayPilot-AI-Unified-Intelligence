"""Safe additive SQLite schema upgrades that preserve existing PayPilot records."""

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.database.session import Base
from app.models import ApplicationSetting  # noqa: F401 - register all table metadata


def apply_schema_migrations(bind: Engine) -> None:
    """Create missing tables and add new nullable columns without rebuilding tables."""
    Base.metadata.create_all(bind=bind)
    inspector = inspect(bind)
    additions = {
        "assistant_conversations": {
            "session_key": "VARCHAR(64)",
            "actor_key": "VARCHAR(160)",
            "context_json": "TEXT",
        },
        "assistant_messages": {"metadata_json": "TEXT"},
        "paypal_sandbox_orders": {
            "user_id": "INTEGER REFERENCES marketplace_users(id)",
            "marketplace_order_id": "INTEGER REFERENCES marketplace_orders(id)",
        },
        "products": {"image_path": "VARCHAR(255)"},
        "marketplace_order_items": {"image_path": "VARCHAR(255)"},
    }
    existing = {table: {column["name"] for column in inspector.get_columns(table)} for table in additions}
    with bind.begin() as connection:
        for table, columns in additions.items():
            for name, sql_type in columns.items():
                if name not in existing[table]:
                    connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
    with bind.begin() as connection:
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_assistant_conversations_session_key "
            "ON assistant_conversations (session_key)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_assistant_conversations_actor_key "
            "ON assistant_conversations (actor_key)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_paypal_sandbox_orders_user_id "
            "ON paypal_sandbox_orders (user_id)"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_paypal_sandbox_orders_marketplace_order_id "
            "ON paypal_sandbox_orders (marketplace_order_id)"
        )
