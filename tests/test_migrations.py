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
        "browser_session",
        "browser_session_flash",
        "google_identity",
        "passkey",
        "auth_flow",
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
        "setting_image_deletion",
        "processing_job",
    }
    indexes = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "ux_processing_job_active_user" in indexes
    flow_index = next(
        row for row in database.execute("PRAGMA index_list(auth_flow)") if row[1] == "uq_auth_flow_session_purpose"
    )
    assert flow_index[2] == 1
    assert flow_index[4] == 0
    assert [row[2] for row in database.execute("PRAGMA index_info(uq_auth_flow_session_purpose)")] == [
        "browser_session_token_hash", "purpose",
    ]
    assert database.execute("PRAGMA foreign_key_list(setting)").fetchone() is not None
    phrase_columns = {column[1]: column for column in database.execute("PRAGMA table_info(phrase_conversion)")}
    assert phrase_columns["phrase_from"][2].upper() == "TEXT"
    assert phrase_columns["phrase_to"][2].upper() == "TEXT"
    assert phrase_columns["regex"][3] == 1
    assert phrase_columns["regex"][4].upper() == "FALSE"
    image_file_foreign_key = database.execute("PRAGMA foreign_key_list(image_conversion_file)").fetchone()
    assert image_file_foreign_key is not None
    assert image_file_foreign_key[2] == "image_conversion"
    assert image_file_foreign_key[6].upper() == "CASCADE"
    for table in tables - {
        "login_failure",
        "login_failure_by_account",
        "user_password",
        "browser_session",
        "google_identity",
        "setting_image_deletion",
    }:
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
    google_columns = {row[1]: row for row in database.execute("PRAGMA table_info(google_identity)")}
    assert set(google_columns) == {"user_id", "issuer", "subject"}
    assert {name for name, row in google_columns.items() if row[5]} == {"user_id"}
    google_fk = database.execute("PRAGMA foreign_key_list(google_identity)").fetchone()
    assert google_fk[2:5] == ("user_account", "user_id", "id")
    assert google_fk[6] == "CASCADE"
    session_columns = {row[1]: row for row in database.execute("PRAGMA table_info(browser_session)")}
    assert {name for name, row in session_columns.items() if row[5]} == {"token_hash"}
    assert "id" not in session_columns
    assert "data" not in session_columns
    assert "csrf_token" not in session_columns
    account_columns = {row[1]: row for row in database.execute("PRAGMA table_info(user_account)")}
    assert account_columns["id"][2] == "TEXT"
    assert "webauthn_handle" not in account_columns
    for table in tables:
        columns = {row[1]: row for row in database.execute(f"PRAGMA table_info({table})")}
        for foreign_key in database.execute(f"PRAGMA foreign_key_list({table})"):
            if foreign_key[2] == "user_account":
                assert columns[foreign_key[3]][2] == "TEXT"
    for table in ("auth_flow", "browser_session_flash"):
        foreign_key = next(
            row for row in database.execute(f"PRAGMA foreign_key_list({table})") if row[2] == "browser_session"
        )
        assert foreign_key[3:7] == ("browser_session_token_hash", "token_hash", "CASCADE", "CASCADE")


def test_postgresql_and_sqlite_baselines_declare_the_same_tables():
    expected = {
        "browser_session",
        "browser_session_flash",
        "google_identity",
        "passkey",
        "auth_flow",
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
        "setting_image_deletion",
        "processing_job",
    }
    for dialect in ("postgresql", "sqlite"):
        sql = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(Path(f"migrations/sql/{dialect}").glob("V*.sql"))
        ).lower()
        assert {table for table in expected if f"create table {table}" in sql} == expected
        assert "varchar" not in sql

    postgresql_sql = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(Path("migrations/sql/postgresql").glob("V*.sql"))
    ).upper()
    assert postgresql_sql.count("ID BIGSERIAL PRIMARY KEY") == len(expected) - 7
    assert "REGEX BOOLEAN NOT NULL DEFAULT FALSE" in postgresql_sql
    assert "PASSWORD_HASH TEXT NOT NULL" in postgresql_sql
    assert "ID UUID PRIMARY KEY" in postgresql_sql
    assert "USER_ID UUID PRIMARY KEY REFERENCES USER_ACCOUNT(ID) ON DELETE CASCADE" in postgresql_sql
    assert "WEBAUTHN_HANDLE" not in postgresql_sql
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
        "browser_session,browser_session_flash,google_identity,passkey,auth_flow,user_account,user_password,setting,fandom,unit_conversion,"
        "login_failure,login_failure_by_account,phrase_conversion,image_conversion,image_conversion_file,"
        "setting_image_deletion,processing_job"
    )
    for output in outputs:
        subprocess.run(
            [
                sys.executable,
                "migrations/generate_models.py",
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
