"""Load the daily Open Food Facts delta exports into bronze.products_delta.

Open Food Facts publishes one gzipped JSONL file per day with every product
changed that day (`openfoodfacts_products_<from>_<to>.json.gz`). A file is
loaded once (tracked in bronze.load_log), and only if it covers changes newer
than the full export, so re-running the script is safe.

The delta files use the newer product schema: nutrients sit in
`nutrition.input_sets`, not in the flat `nutriments` dict of the full export.
"""
import gzip
import json
import re
import urllib.request

import pandas as pd

from common import DELTA_BASE_URL, DELTA_COLUMNS, DELTA_DIR, NUTRIENTS, SOURCE_NUTRIENTS, connect

FILE_RE = re.compile(r"openfoodfacts_products_(\d+)_(\d+)\.json\.gz")


def product_name(doc: dict) -> str | None:
    return doc.get("product_name_fr") or doc.get("product_name") or None


def number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def per_100g(nutrient_set: dict | None) -> dict | None:
    """The nutrients of a set, if the set is per 100 g / 100 ml of the product as sold."""
    if not nutrient_set:
        return None
    if nutrient_set.get("per", "100g") not in ("100g", "100ml"):
        return None
    if nutrient_set.get("preparation", "as_sold") != "as_sold":
        return None
    return nutrient_set.get("nutrients") or {}


def source_set(doc: dict, source: str) -> dict | None:
    sets = (doc.get("nutrition") or {}).get("input_sets") or []
    for s in sets:
        if s.get("source") == source and (found := per_100g(s)) is not None:
            return found
    return None


def nutrients(doc: dict) -> dict[str, float | None]:
    """Per-100 g reference values, plus what the manufacturer and the packaging each say.

    The newer schema keeps one nutrient set per source (manufacturer, packaging photo
    typed in by a contributor, estimate from ingredients) and an `aggregated_set`: the
    value Open Food Facts publishes. The full export only has that published value, so
    it is the one used everywhere; the per-source values feed the source-conflict rule.
    """
    values: dict[str, float | None] = {}
    aggregated = per_100g((doc.get("nutrition") or {}).get("aggregated_set"))
    if aggregated is not None:
        for name, col in NUTRIENTS.items():
            values[col] = number((aggregated.get(name) or {}).get("value"))
    else:
        flat = doc.get("nutriments") or {}  # older schema
        for name, col in NUTRIENTS.items():
            values[col] = number(flat.get(f"{name}_100g"))
    for source, prefix in (("manufacturer", "mfr"), ("packaging", "pkg")):
        found = source_set(doc, source) or {}
        for name, col in SOURCE_NUTRIENTS.items():
            values[f"{prefix}_{col}"] = number((found.get(name) or {}).get("value"))
    return values


def as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_row(doc: dict) -> tuple:
    row = {
        "code": str(doc.get("code") or ""),
        "product_name": product_name(doc),
        "lang": doc.get("lang"),
        "brands": doc.get("brands"),
        "brands_tags": doc.get("brands_tags") or [],
        "categories_tags": doc.get("categories_tags") or [],
        "countries_tags": doc.get("countries_tags") or [],
        "created_t": as_int(doc.get("created_t")),
        "last_modified_t": as_int(doc.get("last_modified_t")),
        "completeness": doc.get("completeness"),
        "unique_scans_n": as_int(doc.get("unique_scans_n")),
        "popularity_key": as_int(doc.get("popularity_key")),
        "nutriscore_grade": doc.get("nutriscore_grade"),
        "nutriscore_score": as_int(doc.get("nutriscore_score")),
        "nova_group": as_int(doc.get("nova_group")),
        "nutrition_data_per": doc.get("nutrition_data_per"),
        "no_nutrition_data": doc.get("no_nutrition_data") in ("on", True, 1),
        "obsolete": doc.get("obsolete") in ("on", True, 1),
        "off_quality_errors": doc.get("data_quality_errors_tags") or [],
        "off_quality_warnings": doc.get("data_quality_warnings_tags") or [],
        "quantity": doc.get("quantity"),
        **nutrients(doc),
    }
    return tuple(row[name] for name, _ in DELTA_COLUMNS)


def available_files() -> list[str]:
    with urllib.request.urlopen(DELTA_BASE_URL + "index.txt") as resp:
        return [line.strip() for line in resp.read().decode().splitlines() if FILE_RE.fullmatch(line.strip())]


def main() -> None:
    con = connect()
    columns = ", ".join(f"{name} {kind}" for name, kind in DELTA_COLUMNS)
    con.execute(f"CREATE TABLE IF NOT EXISTS bronze.products_delta ({columns}, delta_file VARCHAR)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS bronze.load_log (
            source VARCHAR, file VARCHAR, rows BIGINT, loaded_at TIMESTAMP DEFAULT current_timestamp)
    """)
    loaded = {f for (f,) in con.execute("SELECT file FROM bronze.load_log WHERE source = 'delta'").fetchall()}
    watermark = con.execute("SELECT coalesce(max(last_modified_t), 0) FROM bronze.products_full").fetchone()[0]

    todo = sorted(
        (f for f in available_files() if f not in loaded and int(FILE_RE.fullmatch(f)[2]) > watermark),
        key=lambda f: int(FILE_RE.fullmatch(f)[1]),
    )
    DELTA_DIR.mkdir(parents=True, exist_ok=True)
    names = [name for name, _ in DELTA_COLUMNS] + ["delta_file"]
    for name in todo:
        path = DELTA_DIR / name
        if not path.exists():
            urllib.request.urlretrieve(DELTA_BASE_URL + name, path)
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            rows = [to_row(json.loads(line)) + (name,) for line in fh if line.strip()]
        batch = pd.DataFrame(rows, columns=names)  # noqa: F841 - read by DuckDB below
        con.execute("BEGIN")
        con.execute("DELETE FROM bronze.products_delta WHERE delta_file = ?", [name])
        con.execute("INSERT INTO bronze.products_delta BY NAME SELECT * FROM batch")
        con.execute("INSERT INTO bronze.load_log (source, file, rows) VALUES ('delta', ?, ?)", [name, len(rows)])
        con.execute("COMMIT")
        print(f"{name}: {len(rows):,} products")
    print(f"{len(todo)} new delta file(s); watermark of the full export: {watermark}")


if __name__ == "__main__":
    main()
