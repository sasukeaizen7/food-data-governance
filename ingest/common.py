"""Shared settings for the ingestion scripts."""
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW_PARQUET = DATA / "raw" / "food.parquet"
DELTA_DIR = DATA / "delta"
WAREHOUSE = DATA / "warehouse.duckdb"
SERVING = DATA / "serving.duckdb"

PARQUET_URL = "https://huggingface.co/datasets/openfoodfacts/product-database/resolve/main/food.parquet"
DELTA_BASE_URL = "https://static.openfoodfacts.org/data/delta/"

# Nutrients kept per 100 g / 100 ml, named as Open Food Facts names them.
NUTRIENTS = {
    "energy-kcal": "energy_kcal_100g",
    "energy-kj": "energy_kj_100g",
    "fat": "fat_100g",
    "saturated-fat": "saturated_fat_100g",
    "carbohydrates": "carbohydrates_100g",
    "sugars": "sugars_100g",
    "fiber": "fiber_100g",
    "proteins": "proteins_100g",
    "salt": "salt_100g",
    "sodium": "sodium_100g",
    "alcohol": "alcohol_100g",
    "polyols": "polyols_100g",
}

# Columns of bronze.products_full and bronze.products_delta, in order.
BRONZE_COLUMNS = [
    ("code", "VARCHAR"),
    ("product_name", "VARCHAR"),
    ("lang", "VARCHAR"),
    ("brands", "VARCHAR"),
    ("brands_tags", "VARCHAR[]"),
    ("categories_tags", "VARCHAR[]"),
    ("countries_tags", "VARCHAR[]"),
    ("created_t", "BIGINT"),
    ("last_modified_t", "BIGINT"),
    ("completeness", "DOUBLE"),
    ("unique_scans_n", "INTEGER"),
    ("popularity_key", "BIGINT"),
    ("nutriscore_grade", "VARCHAR"),
    ("nutriscore_score", "INTEGER"),
    ("nova_group", "INTEGER"),
    ("nutrition_data_per", "VARCHAR"),
    ("no_nutrition_data", "BOOLEAN"),
    ("obsolete", "BOOLEAN"),
    ("off_quality_errors", "VARCHAR[]"),
    ("off_quality_warnings", "VARCHAR[]"),
    ("quantity", "VARCHAR"),
] + [(col, "DOUBLE") for col in NUTRIENTS.values()]

# Nutrients compared between sources (delta files only: the full export has no per-source values).
SOURCE_NUTRIENTS = {
    "energy-kcal": "energy_kcal_100g",
    "fat": "fat_100g",
    "sugars": "sugars_100g",
    "salt": "salt_100g",
}
DELTA_COLUMNS = BRONZE_COLUMNS + [
    (f"{prefix}_{col}", "DOUBLE") for prefix in ("mfr", "pkg") for col in SOURCE_NUTRIENTS.values()
]


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    DATA.mkdir(exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE), read_only=read_only)
    if not read_only:
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    return con
