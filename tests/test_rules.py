"""Each quality rule against hand-made products with one known defect each.

Builds a tiny bronze layer in a temporary DuckDB file, runs the real dbt project on
it, and checks which rules fail for which product.
"""
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingest"))
from common import BRONZE_COLUMNS, DELTA_COLUMNS  # noqa: E402

NOW = 1_790_000_000  # 2026-09
OK_NUTRITION = dict(energy_kcal_100g=250.0, energy_kj_100g=1046.0, fat_100g=10.0, saturated_fat_100g=4.0,
                    carbohydrates_100g=30.0, sugars_100g=12.0, fiber_100g=2.0, proteins_100g=8.0,
                    salt_100g=1.0, sodium_100g=0.4)
# 9*10 + 4*30 + 4*8 + 2*2 = 246 kcal, close to the declared 250

BASE = dict(product_name="Produit test", brands="Marque", brands_tags=["marque"],
            categories_tags=["en:snacks"], countries_tags=["en:france"], created_t=NOW,
            last_modified_t=NOW, unique_scans_n=10, obsolete=False, no_nutrition_data=False,
            **OK_NUTRITION)


def gtin(body: str) -> str:
    """Append the GS1 check digit to `body`."""
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return body + str((10 - total % 10) % 10)


NO_NAME, NO_BRAND, NO_NUTRITION = gtin("4000018"), gtin("4000020"), gtin("4000022")
SUGARS_ABOVE_CARBS, ENERGY_MISMATCH = gtin("4000024"), gtin("4000027")

# code -> (overrides, rules expected to fail)
CASES = {
    "3017620422003": ({}, set()),                                    # clean product, valid EAN-13
    "3017620422004": ({}, {"R01"}),                                  # wrong check digit
    "12345": ({}, {"R01"}),                                          # not a GTIN length
    "2000000000015": ({}, {"R02"}),                                  # in-store code (2xx prefix)
    "0000000000017": ({}, {"R03"}),                                  # same barcode as "17", padded
    NO_NAME: ({"product_name": None}, {"R04"}),
    gtin("4000019"): ({"product_name": "P�te à tartiner"}, {"R05"}),  # broken encoding
    NO_BRAND: ({"brands": None, "brands_tags": []}, {"R06"}),
    gtin("4000021"): ({"categories_tags": []}, {"R07"}),
    NO_NUTRITION: ({k: None for k in OK_NUTRITION}, {"R08"}),
    gtin("4000023"): ({"energy_kcal_100g": 1046.0}, {"R09", "R13", "R14"}),    # kJ typed as kcal
    SUGARS_ABOVE_CARBS: ({"sugars_100g": 45.0}, {"R10"}),
    gtin("4000025"): ({"saturated_fat_100g": 12.0}, {"R11"}),
    gtin("4000026"): ({"fat_100g": 50.0, "carbohydrates_100g": 50.0, "proteins_100g": 10.0,
                       "energy_kcal_100g": 690.0, "energy_kj_100g": 2887.0}, {"R12"}),
    ENERGY_MISMATCH: ({"energy_kcal_100g": 500.0, "energy_kj_100g": 2092.0}, {"R13"}),
    gtin("4000028"): ({"energy_kj_100g": 500.0}, {"R14"}),
    gtin("4000029"): ({"sodium_100g": 0.1}, {"R15"}),
    gtin("4000030"): ({"last_modified_t": NOW - 7 * 365 * 86400}, {"R16"}),
    # mineral water has no nutrition table, and the EU does not require one
    gtin("4000033"): ({**{k: None for k in OK_NUTRITION}, "categories_tags": ["en:beverages", "en:waters"]}, set()),
    # kJ / kcal = 4.4: off 4.184, but within what rounding and per-nutrient factors produce
    gtin("4000034"): ({"energy_kj_100g": 1100.0}, set()),
}
# R03: "17" and "0000000000017" are the same barcode with and without leading zeros
DUPLICATE_SHORT = "17"

# Delta-only product where the manufacturer and the packaging disagree (R17)
CONFLICT = (gtin("4000031"), {"brands_tags": ["xx:Marque"], "mfr_sugars_100g": 56.3, "pkg_sugars_100g": 60.0,
                            "mfr_salt_100g": 0.107, "pkg_salt_100g": 15.0}, {"R17"})
AGREE = (gtin("4000032"), {"mfr_sugars_100g": 12.0, "pkg_sugars_100g": 12.0}, set())


def row(columns, code, overrides):
    values = {**BASE, "code": code, **overrides}
    return tuple(values.get(name) for name, _ in columns)


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dq")
    db = tmp / "warehouse.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA bronze")
    con.execute(f"CREATE TABLE bronze.products_full ({', '.join(f'{n} {t}' for n, t in BRONZE_COLUMNS)})")
    con.execute(f"CREATE TABLE bronze.products_delta ({', '.join(f'{n} {t}' for n, t in DELTA_COLUMNS)}, delta_file VARCHAR)")
    marks = ", ".join("?" for _ in BRONZE_COLUMNS)
    rows = [row(BRONZE_COLUMNS, code, over) for code, (over, _) in CASES.items()]
    rows.append(row(BRONZE_COLUMNS, DUPLICATE_SHORT, {}))
    con.executemany(f"INSERT INTO bronze.products_full VALUES ({marks})", rows)
    dmarks = ", ".join("?" for _ in range(len(DELTA_COLUMNS) + 1))
    con.executemany(f"INSERT INTO bronze.products_delta VALUES ({dmarks})",
                    [row(DELTA_COLUMNS, code, over) + ("test",) for code, over, _ in (CONFLICT, AGREE)])
    con.close()

    env = {**os.environ, "WAREHOUSE_PATH": str(db), "DBT_PROFILES_DIR": str(ROOT / "transform")}
    dbt = Path(sys.executable).parent / "dbt"
    result = subprocess.run(
        [str(dbt), "build", "--exclude", "products_snapshot", "dq_history",
         "--target-path", str(tmp / "target"), "--log-path", str(tmp / "logs")],
        cwd=ROOT / "transform", env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout[-3000:]
    con = duckdb.connect(str(db), read_only=True)
    yield con
    con.close()


def failed_rules(con, code):
    return {r for (r,) in con.execute(
        "SELECT rule_id FROM quality.dq_results WHERE code = ? AND status = 'fail'", [code]).fetchall()}


@pytest.mark.parametrize("code", list(CASES))
def test_rule_flags_exactly_the_planted_defect(warehouse, code):
    assert failed_rules(warehouse, code) == CASES[code][1]


def test_duplicate_is_flagged_on_both_records(warehouse):
    # "17" is also too short to be a GTIN
    assert failed_rules(warehouse, DUPLICATE_SHORT) == {"R01", "R03"}


@pytest.mark.parametrize("case", [CONFLICT, AGREE], ids=["conflict", "agree"])
def test_source_conflict(warehouse, case):
    code, _, expected = case
    assert failed_rules(warehouse, code) == expected


def test_rule_not_applied_without_data(warehouse):
    # no nutrition at all: nutrition rules do not apply, they do not "pass"
    checked = {r for (r,) in warehouse.execute(
        "SELECT rule_id FROM quality.dq_results WHERE code = ?", [NO_NUTRITION]).fetchall()}
    assert not checked & {"R09", "R10", "R11", "R12", "R13", "R14", "R15", "R17"}


def test_brand_key_ignores_case_and_language_prefix(warehouse):
    keys = {k for (k,) in warehouse.execute(
        "SELECT DISTINCT brand_key FROM silver.products WHERE code IN (?, ?)", [NO_NAME, CONFLICT[0]]).fetchall()}
    assert keys == {"marque"}


def test_trust_level(warehouse):
    levels = dict(warehouse.execute("SELECT code, trust_level FROM gold.fct_products").fetchall())
    assert levels["3017620422003"] == "trusted"
    assert levels["2000000000015"] == "trusted"      # info-level rule only
    assert levels[SUGARS_ABOVE_CARBS] == "untrusted"  # blocking: sugars above carbs
    assert levels[ENERGY_MISMATCH] == "caution"       # major: energy mismatch
    assert levels[NO_BRAND] == "trusted"              # minor: no brand
