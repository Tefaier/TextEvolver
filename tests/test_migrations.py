import sqlite3
import subprocess
import sys
from pathlib import Path


def test_sqlite_flyway_baseline_is_fresh_and_complete(tmp_path: Path):
    migration_sql = Path("migrations/sql/sqlite/V1__initial_schema.sql").read_text(encoding="utf-8")
    assert "PRAGMA foreign_keys" not in migration_sql
    database = sqlite3.connect(tmp_path / "schema.db")
    database.executescript(migration_sql)
    tables = {
        row[0]
        for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    }
    assert tables == {
        "user_account",
        "user_password",
        "setting",
        "fandom",
        "unit_conversion",
        "phrase_conversion",
        "image_conversion",
        "processing_job",
    }
    indexes = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "ux_processing_job_active_user" in indexes
    assert database.execute("PRAGMA foreign_key_list(setting)").fetchone() is not None
    phrase_columns = {
        column[1]: column for column in database.execute("PRAGMA table_info(phrase_conversion)")
    }
    assert phrase_columns["regex"][3] == 1
    assert phrase_columns["regex"][4].upper() == "FALSE"
    for table in tables:
        id_column = next(column for column in database.execute(f"PRAGMA table_info({table})") if column[1] == "id")
        assert id_column[3] == 1
        assert id_column[5] == 1


def test_postgresql_and_sqlite_baselines_declare_the_same_tables():
    expected = {
        "user_account",
        "user_password",
        "setting",
        "fandom",
        "unit_conversion",
        "phrase_conversion",
        "image_conversion",
        "processing_job",
    }
    for dialect in ("postgresql", "sqlite"):
        sql = Path(f"migrations/sql/{dialect}/V1__initial_schema.sql").read_text(encoding="utf-8").lower()
        assert {table for table in expected if f"create table {table}" in sql} == expected

    postgresql_sql = Path("migrations/sql/postgresql/V1__initial_schema.sql").read_text(encoding="utf-8").upper()
    assert postgresql_sql.count("ID BIGSERIAL PRIMARY KEY") == len(expected)
    assert "REGEX BOOLEAN NOT NULL DEFAULT FALSE" in postgresql_sql
    assert "ENCODED_PASSWORD BYTEA NOT NULL" in postgresql_sql
    assert "NONCE BYTEA NOT NULL" in postgresql_sql
    assert "CONSTRAINT UQ_USER_PASSWORD_NONCE UNIQUE (NONCE)" in postgresql_sql


def test_sqlalchemy_model_generation_is_deterministic(tmp_path: Path):
    database_path = tmp_path / "models.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(Path("migrations/sql/sqlite/V1__initial_schema.sql").read_text(encoding="utf-8"))
    connection.close()
    outputs = [tmp_path / "models-one.py", tmp_path / "models-two.py"]
    tables = (
        "user_account,user_password,setting,fandom,unit_conversion,"
        "phrase_conversion,image_conversion,processing_job"
    )
    sqlacodegen = str(Path(sys.executable).with_name("sqlacodegen"))
    for output in outputs:
        subprocess.run(
            [
                sqlacodegen,
                f"sqlite:///{database_path}",
                "--generator",
                "declarative",
                "--tables",
                tables,
                "--outfile",
                str(output),
            ],
            check=True,
        )
    generated = outputs[0].read_text(encoding="utf-8")
    assert generated == outputs[1].read_text(encoding="utf-8")
    assert generated == Path("src/text_evolver/db/models.py").read_text(encoding="utf-8")
    assert "Mapped[Optional[int]] = mapped_column(Integer, primary_key=True)" not in generated
