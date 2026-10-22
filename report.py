"""Figures and charts for the README, read from the published tables only.

Writes results/*.csv (the numbers quoted in the README) and images/*.png.
"""
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt

import charts

ROOT = Path(__file__).resolve().parent
SERVING = ROOT / "data" / "serving.duckdb"
RESULTS, IMAGES = ROOT / "results", ROOT / "images"
SOURCE = "Open Food Facts (ODbL), products sold in France; quality rules in transform/seeds/dq_rules.csv"

RULE_LABELS = {
    "R01": "Invalid barcode",
    "R03": "Duplicate barcode",
    "R04": "No product name",
    "R05": "Broken text encoding",
    "R06": "No brand",
    "R07": "No category",
    "R08": "No nutrition table",
    "R09": "Impossible value",
    "R10": "Sugars > carbohydrates",
    "R11": "Saturated fat > fat",
    "R12": "Nutrients > 100 g per 100 g",
    "R13": "Energy ≠ macronutrients",
    "R14": "kJ ≠ kcal × 4.184",
    "R15": "Salt ≠ sodium × 2.5",
    "R16": "Not updated in 5 years",
    "R17": "Manufacturer ≠ packaging",
}

QUERIES = {
    "rule_summary": """
        SELECT rule_id, rule_name, dimension, severity, owner_role, n_checked, n_failed, pct_failed,
               scans_on_failed, pct_of_all_scans
        FROM dq_rule_summary ORDER BY rule_id""",
    "trust_levels": """
        SELECT trust_level, count(*) AS products,
               round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct_products,
               sum(unique_scans) AS scans,
               round(100.0 * sum(unique_scans) / sum(sum(unique_scans)) OVER (), 1) AS pct_scans
        FROM fct_products GROUP BY 1 ORDER BY 1""",
    "most_scanned_brands": """
        SELECT brand, n_products, scans, pct_trusted, pct_caution, pct_untrusted, top_issue_rule
        FROM brand_quality ORDER BY scans DESC LIMIT 15""",
    "examples": """
        SELECT rule_id, code, product_name, brand, unique_scans, trust_level
        FROM (
            SELECT r.rule_id, p.*, row_number() OVER (PARTITION BY r.rule_id ORDER BY p.unique_scans DESC) AS rn
            FROM fct_products p, unnest(p.failed_rules) AS r(rule_id)
        ) WHERE rn <= 3 ORDER BY rule_id, unique_scans DESC""",
}


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    con = duckdb.connect(str(SERVING), read_only=True)
    frames = {}
    for name, sql in QUERIES.items():
        frames[name] = con.execute(sql).df()
        frames[name].to_csv(RESULTS / f"{name}.csv", index=False)
    con.close()

    charts.style()

    rules = frames["rule_summary"]
    rules = rules[rules["severity"] != "info"].sort_values("pct_failed", ascending=False)
    fig, ax = plt.subplots(figsize=(9, 0.38 * len(rules) + 1))
    charts.bar_h(ax, [f"{RULE_LABELS[r]} ({r})" for r in rules["rule_id"]], rules["pct_failed"],
                 fmt=lambda v: f"{v:.1f}%")
    ax.set_title("How often each quality rule fails")
    charts.save(fig, IMAGES / "rule_failures.png", SOURCE,
                "Share of the products each rule applies to that fail it (a rule applies only when its data exists)")

    brands = frames["most_scanned_brands"]
    not_trusted = brands["pct_caution"] + brands["pct_untrusted"]
    order = not_trusted.sort_values(ascending=False).index
    fig, ax = plt.subplots(figsize=(9, 0.38 * len(brands) + 1))
    charts.bar_h(ax, brands.loc[order, "brand"], not_trusted[order], fmt=lambda v: f"{v:.0f}%")
    ax.set_title("Records with a major or blocking issue, 15 most-scanned brands")
    charts.save(fig, IMAGES / "brands_not_trusted.png", SOURCE,
                "Share of the brand's Open Food Facts records (crowd-sourced, not brand data) that are not 'trusted'")

    print(frames["trust_levels"].to_string(index=False))


if __name__ == "__main__":
    main()
