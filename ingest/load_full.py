"""Load the full Open Food Facts export into bronze.products_full.

The Parquet export (~8 GB, ~4.8M products, all countries) is read in place by
DuckDB. Only the columns the project uses are kept; nutrients come out of the
`nutriments` list of structs as one column each (value per 100 g / 100 ml).
"""
import sys
import time
import urllib.request

from common import NUTRIENTS, PARQUET_URL, RAW_PARQUET, connect


def download() -> None:
    if RAW_PARQUET.exists():
        return
    RAW_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {PARQUET_URL} (~8 GB)")
    tmp = RAW_PARQUET.with_suffix(".part")
    urllib.request.urlretrieve(PARQUET_URL, tmp)
    tmp.rename(RAW_PARQUET)


def nutrient(name: str, column: str) -> str:
    return f"list_filter(nutriments, lambda x: x.name = '{name}')[1].\"100g\" AS {column}"


def main() -> None:
    download()
    nutrients = ",\n        ".join(nutrient(n, c) for n, c in NUTRIENTS.items())
    sql = f"""
    CREATE OR REPLACE TABLE bronze.products_full AS
    SELECT
        code,
        -- names come as a list of (lang, text): French first, then the main language
        coalesce(
            list_filter(product_name, lambda x: x.lang = 'fr')[1].text,
            list_filter(product_name, lambda x: x.lang = 'main')[1].text
        ) AS product_name,
        lang,
        brands,
        brands_tags,
        categories_tags,
        countries_tags,
        created_t,
        last_modified_t,
        completeness,
        unique_scans_n,
        popularity_key,
        nutriscore_grade,
        nutriscore_score,
        nova_group,
        nutrition_data_per,
        no_nutrition_data,
        obsolete,
        data_quality_errors_tags AS off_quality_errors,
        data_quality_warnings_tags AS off_quality_warnings,
        quantity,
        {nutrients}
    FROM read_parquet('{RAW_PARQUET.as_posix()}')
    """
    con = connect()
    started = time.time()
    con.execute(sql)
    rows = con.execute("SELECT count(*) FROM bronze.products_full").fetchone()[0]
    con.execute("""
        CREATE TABLE IF NOT EXISTS bronze.load_log (
            source VARCHAR, file VARCHAR, rows BIGINT, loaded_at TIMESTAMP DEFAULT current_timestamp)
    """)
    con.execute("DELETE FROM bronze.load_log WHERE source = 'full'")
    con.execute("INSERT INTO bronze.load_log (source, file, rows) VALUES ('full', ?, ?)",
                [RAW_PARQUET.name, rows])
    print(f"bronze.products_full: {rows:,} rows in {time.time() - started:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
