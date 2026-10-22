"""Export the published tables to Parquet for the Power BI report (powerbi/).

Power BI reads Parquet natively but not every DuckDB type, so lists become
comma-separated text, HUGEINT sums become BIGINT and timestamps lose their time zone.
Refreshing the report after a pipeline run picks up the new files.
"""
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent
SERVING = ROOT / "data" / "serving.duckdb"
OUT = ROOT / "data" / "powerbi"

TABLES = {
    "products": """
        SELECT code, product_name, brand, brand_key, category,
               cast(last_modified_at AS TIMESTAMP) AS last_modified_at,
               unique_scans, nutriscore_grade, nova_group, trust_level,
               n_blocking, n_major, n_minor, is_internal_code,
               array_to_string(failed_rules, ', ') AS failed_rules,
               energy_kcal_100g, fat_100g, saturated_fat_100g, carbohydrates_100g,
               sugars_100g, fiber_100g, proteins_100g, salt_100g
        FROM fct_products""",
    # one row per (product, failed rule): lets the report filter products by rule or owner
    "product_issues": """
        SELECT p.code, r.rule_id
        FROM fct_products p, unnest(p.failed_rules) AS r(rule_id)""",
    "rules": """
        SELECT rule_id, rule_id || ' ' || rule_name AS rule_label, rule_name, dimension, severity,
               owner_role, description, n_checked, n_failed, pct_failed / 100 AS pct_failed,
               scans_on_failed, pct_of_all_scans / 100 AS pct_of_all_scans
        FROM dq_rule_summary""",
    "owners": "SELECT owner_role, title, responsibility FROM data_owners",
    "brands": """
        SELECT brand_key, brand, n_products, cast(scans AS BIGINT) AS scans,
               pct_trusted / 100 AS pct_trusted, pct_caution / 100 AS pct_caution,
               pct_untrusted / 100 AS pct_untrusted, top_issue_rule, top_issue_products
        FROM brand_quality""",
    "quality_history": """
        SELECT cast(run_date AS DATE) AS run_date, rule_id, n_checked, n_failed, pct_failed / 100 AS pct_failed
        FROM dq_history""",
    "exclusion_reasons": """
        SELECT rule_id, rule_name, severity, owner_role, excluded_products, pct_of_excluded / 100 AS pct_of_excluded
        FROM dq_exclusion_reasons WHERE rule_id <> 'ALL'""",
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(SERVING), read_only=True)
    for name, sql in TABLES.items():
        target = OUT / f"{name}.parquet"
        con.execute(f"COPY ({sql}) TO '{target.as_posix()}' (FORMAT parquet)")
        rows = con.execute(f"SELECT count(*) FROM '{target.as_posix()}'").fetchone()[0]
        print(f"  {name}.parquet: {rows:,} rows")
    con.close()


if __name__ == "__main__":
    main()
