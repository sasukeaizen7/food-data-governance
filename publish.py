"""Publish the data products: copy every dbt model or seed marked `meta.published: true`
into data/serving.duckdb, with a `catalog` table of their documented columns.

Consumers (the agent, a BI tool) read serving.duckdb only. Bronze, silver and the
row-level quality results stay in the warehouse: what is not published is not used.
"""
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "transform" / "target" / "manifest.json"
WAREHOUSE = ROOT / "data" / "warehouse.duckdb"
SERVING = ROOT / "data" / "serving.duckdb"


def published_nodes(manifest: dict) -> list[dict]:
    nodes = [
        n for n in manifest["nodes"].values()
        if n["resource_type"] in ("model", "seed") and (n.get("meta") or n["config"].get("meta") or {}).get("published")
    ]
    return sorted(nodes, key=lambda n: n["name"])


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    nodes = published_nodes(manifest)
    tmp = SERVING.with_suffix(".tmp.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    con.execute(f"ATTACH '{WAREHOUSE.as_posix()}' AS wh (READ_ONLY)")
    catalog = []
    for node in nodes:
        name, schema = node["name"], node["schema"]
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM wh.{schema}.{name}")
        meta = node.get("meta") or node["config"].get("meta") or {}
        types = dict(con.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
            [name]).fetchall())
        rows = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
        catalog.append((name, None, None, node.get("description") or "", meta.get("owner"), rows))
        for column, data_type in types.items():
            doc = (node["columns"].get(column) or {}).get("description") or ""
            catalog.append((name, column, data_type, doc, None, None))
    con.execute("""CREATE TABLE catalog (
        table_name VARCHAR, column_name VARCHAR, data_type VARCHAR,
        description VARCHAR, owner_role VARCHAR, row_count BIGINT)""")
    con.executemany("INSERT INTO catalog VALUES (?, ?, ?, ?, ?, ?)", catalog)
    con.execute("DETACH wh")
    con.close()
    tmp.replace(SERVING)
    print(f"published {len(nodes)} tables to {SERVING.relative_to(ROOT)}: {', '.join(n['name'] for n in nodes)}")


if __name__ == "__main__":
    main()
