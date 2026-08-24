import sqlite3
import subprocess
import sys
from pathlib import Path


def test_sqlite_flyway_baseline_is_fresh_and_complete(tmp_path: Path):
    database = sqlite3.connect(tmp_path / "schema.db")
    database.executescript(Path("migrations/sql/sqlite/V1__initial_schema.sql").read_text(encoding="utf-8"))
    tables = {
        row[0]
        for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    }
    assert tables == {
        "user_account",
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


def test_postgresql_and_sqlite_baselines_declare_the_same_tables():
    expected = {
        "user_account",
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


def test_sqlalchemy_model_generation_is_deterministic(tmp_path: Path):
    database_path = tmp_path / "models.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(Path("migrations/sql/sqlite/V1__initial_schema.sql").read_text(encoding="utf-8"))
    connection.close()
    outputs = [tmp_path / "models-one.py", tmp_path / "models-two.py"]
    tables = "user_account,setting,fandom,unit_conversion,phrase_conversion,image_conversion,processing_job"
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
