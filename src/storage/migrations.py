"""Database migration utility and DDL generator for PostgreSQL and SQLite."""

import asyncio
import sys
from sqlalchemy import text
from sqlalchemy.schema import CreateTable, CreateIndex

from src.storage.schema import (
    Base,
    StrategyConfigRecord,
    SessionRecord,
    InstrumentRecord,
    OrderRecord,
    OrderStateTransitionRecord,
    FillRecord,
    PositionRecord,
    RiskSnapshotRecord,
    DailyPnlSnapshotRecord,
    AuditEventRecord,
)
from src.storage.db import create_engine_and_factory, init_db


def generate_postgresql_ddl() -> str:
    """Generate raw PostgreSQL DDL scripts for all tables and indices."""
    from sqlalchemy.dialects import postgresql
    ddl_statements = []
    dialect = postgresql.dialect()

    for table in Base.metadata.sorted_tables:
        create_table = str(CreateTable(table).compile(dialect=dialect)).strip()
        ddl_statements.append(f"{create_table};")
        for index in table.indexes:
            create_index = str(CreateIndex(index).compile(dialect=dialect)).strip()
            ddl_statements.append(f"{create_index};")

    return "\n\n".join(ddl_statements)


async def run_migrations(database_url: str) -> None:
    """Apply migrations directly against database connection."""
    print(f"Applying schema migrations to: {database_url}")
    engine, _ = create_engine_and_factory(database_url)
    try:
        await init_db(engine)
        print("Schema migrations successfully applied.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if "--generate-sql" in sys.argv:
        print(generate_postgresql_ddl())
    else:
        url = sys.argv[1] if len(sys.argv) > 1 else "sqlite+aiosqlite:///./test_local.db"
        asyncio.run(run_migrations(url))
