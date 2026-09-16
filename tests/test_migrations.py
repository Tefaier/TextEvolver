import sqlite3
import subprocess
import sys
from pathlib import Path


def test_sqlite_flyway_baseline_is_fresh_and_complete(tmp_path: Path):
    migration_sql = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(Path("migrations/sql/sqlite").glob("V*.sql"))
    )
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
        "login_failure",
        "login_failure_by_account",
        "setting",
        "fandom",
        "unit_conversion",
        "phrase_conversion",
        "image_conversion",
        "image_conversion_file",
        "processing_job",
    }
    indexes = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "ux_processing_job_active_user" in indexes
    assert database.execute("PRAGMA foreign_key_list(setting)").fetchone() is not None
    phrase_columns = {
        column[1]: column for column in database.execute("PRAGMA table_info(phrase_conversion)")
    }
    assert phrase_columns["phrase_from"][2].upper() == "TEXT"
    assert phrase_columns["phrase_to"][2].upper() == "TEXT"
    assert phrase_columns["regex"][3] == 1
    assert phrase_columns["regex"][4].upper() == "FALSE"
    image_file_foreign_key = database.execute("PRAGMA foreign_key_list(image_conversion_file)").fetchone()
    assert image_file_foreign_key is not None
    assert image_file_foreign_key[2] == "image_conversion"
    assert image_file_foreign_key[6].upper() == "CASCADE"
    for table in tables - {"login_failure", "login_failure_by_account", "user_password"}:
        id_column = next(column for column in database.execute(f"PRAGMA table_info({table})") if column[1] == "id")
        assert id_column[3] == 1
        assert id_column[5] == 1
    login_failure_primary_key = {
        column[1] for column in database.execute("PRAGMA table_info(login_failure)") if column[5]
    }
    assert login_failure_primary_key == {"username", "ip"}
    account_failure_primary_key = {
        column[1] for column in database.execute("PRAGMA table_info(login_failure_by_account)") if column[5]
    }
    assert account_failure_primary_key == {"username"}
    user_password_primary_key = {
        column[1] for column in database.execute("PRAGMA table_info(user_password)") if column[5]
    }
    assert user_password_primary_key == {"user_id"}


def test_postgresql_and_sqlite_baselines_declare_the_same_tables():
    expected = {
        "user_account",
        "user_password",
        "login_failure",
        "login_failure_by_account",
        "setting",
        "fandom",
        "unit_conversion",
        "phrase_conversion",
        "image_conversion",
        "image_conversion_file",
        "processing_job",
    }
    for dialect in ("postgresql", "sqlite"):
        sql = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(Path(f"migrations/sql/{dialect}").glob("V*.sql"))
        ).lower()
        assert {table for table in expected if f"create table {table}" in sql} == expected
        assert "varchar" not in sql

    postgresql_sql = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(Path("migrations/sql/postgresql").glob("V*.sql"))
    ).upper()
    assert postgresql_sql.count("ID BIGSERIAL PRIMARY KEY") == len(expected) - 3
    assert "REGEX BOOLEAN NOT NULL DEFAULT FALSE" in postgresql_sql
    assert "PASSWORD_HASH TEXT NOT NULL" in postgresql_sql
    assert "USER_ID BIGINT PRIMARY KEY REFERENCES USER_ACCOUNT(ID) ON DELETE CASCADE" in postgresql_sql
    assert "ENCODED_PASSWORD" not in postgresql_sql
    assert "NONCE" not in postgresql_sql
    assert "KEY_VERSION" not in postgresql_sql
    assert "PRIMARY KEY (USERNAME, IP)" in postgresql_sql
    assert "USERNAME TEXT NOT NULL PRIMARY KEY" in postgresql_sql


def test_sqlalchemy_model_generation_is_deterministic(tmp_path: Path):
    database_path = tmp_path / "models.db"
    connection = sqlite3.connect(database_path)
    for migration_path in sorted(Path("migrations/sql/sqlite").glob("V*.sql")):
        connection.executescript(migration_path.read_text(encoding="utf-8"))
    connection.close()
    outputs = [tmp_path / "models-one.py", tmp_path / "models-two.py"]
    tables = (
        "user_account,user_password,setting,fandom,unit_conversion,"
        "login_failure,login_failure_by_account,phrase_conversion,image_conversion,image_conversion_file,processing_job"
    )
    sqlacodegen = str(Path(sys.executable).with_name("sqlacodegen"))
    for output in outputs:
        subprocess.run(
            [
                sqlacodegen,
                f"sqlite:///{database_path}",
                "--generator",
                "declarative",
                "--options",
                "nojoined",
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
    assert "String(" not in generated
