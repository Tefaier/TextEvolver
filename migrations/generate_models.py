"""Run sqlacodegen with portable UUID types for SQLite's TEXT account IDs."""

from sqlacodegen.cli import main
from sqlalchemy import Table, Uuid, event


@event.listens_for(Table, "column_reflect")
def reflect_account_uuid(inspector, table, column_info):
    # SQLite has no UUID type. Reflect the account PK and its foreign keys as
    # SQLAlchemy Uuid, which uses native UUID on PostgreSQL and hex on SQLite.
    is_account_id = table.name == "user_account" and column_info["name"] == "id"
    references_account = any(
        foreign_key["referred_table"] == "user_account"
        and column_info["name"] in foreign_key["constrained_columns"]
        for foreign_key in inspector.get_foreign_keys(table.name)
    )
    if is_account_id or references_account:
        column_info["type"] = Uuid()


if __name__ == "__main__":
    main()
