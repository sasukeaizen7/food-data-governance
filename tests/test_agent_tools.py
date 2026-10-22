"""Guard rails of the agent's data access (no LLM call involved)."""
import json
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
import tools  # noqa: E402


@pytest.fixture()
def serving(tmp_path):
    db = tmp_path / "serving.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE fct_products AS SELECT range AS code, 'trusted' AS trust_level FROM range(120)")
    con.execute("""CREATE TABLE catalog (table_name VARCHAR, column_name VARCHAR, data_type VARCHAR,
                   description VARCHAR, owner_role VARCHAR, row_count BIGINT)""")
    con.execute("""INSERT INTO catalog VALUES
        ('fct_products', NULL, NULL, 'Products', 'owner_product_catalog', 120),
        ('fct_products', 'code', 'BIGINT', 'Barcode', NULL, NULL)""")
    con.close()
    con = tools.connect(db)
    yield con, tmp_path / "audit.jsonl"
    con.close()


@pytest.mark.parametrize("sql", [
    "DELETE FROM fct_products",
    "DROP TABLE fct_products",
    "COPY fct_products TO 'out.csv'",
    "SELECT 1; SELECT 2",
    "ATTACH 'other.duckdb'",
])
def test_rejects_anything_but_one_select(sql):
    with pytest.raises(tools.QueryRejected):
        tools.check_query(sql)


def test_accepts_cte():
    assert tools.check_query("WITH t AS (SELECT 1 AS x) SELECT x FROM t;").startswith("WITH")


def test_cannot_read_files(serving, tmp_path):
    con, log = serving
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")
    out = tools.run_sql(con, f"SELECT * FROM read_csv('{secret.as_posix()}')", log)
    assert out.startswith("ERROR")


def test_rows_are_capped_and_queries_audited(serving):
    con, log = serving
    out = tools.run_sql(con, "SELECT code FROM fct_products ORDER BY code", log)
    lines = out.splitlines()
    assert lines[0] == "code"
    assert len(lines) == 1 + tools.MAX_ROWS + 1  # header, rows, truncation note
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert entries[-1]["rows"] == tools.MAX_ROWS
    assert "fct_products" in entries[-1]["sql"]


def test_rejected_query_is_audited(serving):
    con, log = serving
    assert tools.run_sql(con, "DELETE FROM fct_products", log).startswith("ERROR")
    assert "error" in json.loads(log.read_text(encoding="utf-8").splitlines()[-1])


def test_catalog(serving):
    con, _ = serving
    assert "owner_product_catalog" in tools.list_datasets(con)
    assert "Barcode" in tools.describe_dataset(con, "fct_products")
    assert tools.describe_dataset(con, "silver_products").startswith("ERROR")


SOURCES = ["avg_sugar | product_count\n29.0234 | 38468", "total\n44080", "ALL | 259653\nR04 | 120649"]


def test_numbers_from_results_are_verified():
    answer = "Moyenne 29,02 g/100g sur 38 468 produits ; 5 612 exclus (12,7 %) ; 259,653 au total."
    assert tools.unverified_numbers(answer, SOURCES) == []   # 5612 = 44080 - 38468, 12.7 = %


def test_invented_numbers_are_flagged():
    answer = "127 588 - 38 468 = 89 120 produits exclus, dont 12 000 bloquants"
    assert tools.unverified_numbers(answer, SOURCES) == [127588, 89120, 12000]
    assert "Chiffres non vérifiés" in tools.check_answer(answer, SOURCES)


def test_question_numbers_and_small_integers_are_ignored():
    assert tools.unverified_numbers("Les 3 règles pour 100 g en 2026", SOURCES, "en 2026") == []
