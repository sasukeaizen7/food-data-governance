"""Daily run: ingest -> dbt build (quality checks included) -> snapshot -> publish -> report.

    python run_pipeline.py            # daily run (full export loaded only the first time)
    python run_pipeline.py --reload   # also reload the full export

Each step stops the run on failure, so nothing is published from a broken build.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BIN = Path(sys.executable).parent


def step(name: str, cmd: list[str], cwd: Path = ROOT) -> None:
    print(f"\n== {name}")
    started = time.time()
    env = {**os.environ, "DBT_PROFILES_DIR": str(ROOT / "transform"), "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if result.returncode != 0:
        sys.exit(f"step '{name}' failed (exit {result.returncode}); nothing published")
    print(f"   {name}: {time.time() - started:.0f}s")


def full_export_loaded() -> bool:
    import duckdb
    db = ROOT / "data" / "warehouse.duckdb"
    if not db.exists():
        return False
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute("""
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'bronze' AND table_name = 'load_log'
        """).fetchone()[0] == 1 and con.execute(
            "SELECT count(*) FROM bronze.load_log WHERE source = 'full'").fetchone()[0] > 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reload", action="store_true", help="reload the full export")
    args = parser.parse_args()
    py, dbt = str(BIN / "python"), str(BIN / "dbt")
    transform = ROOT / "transform"

    if args.reload or not full_export_loaded():
        step("load full export", [py, "load_full.py"], ROOT / "ingest")
    step("load daily deltas", [py, "load_delta.py"], ROOT / "ingest")
    step("dbt build (models + quality tests)", [dbt, "build", "--exclude", "products_snapshot"], transform)
    step("dbt snapshot (SCD2 history)", [dbt, "snapshot"], transform)
    step("dbt docs", [dbt, "docs", "generate"], transform)
    step("publish data products", [py, "publish.py"])
    step("report", [py, "report.py"])


if __name__ == "__main__":
    main()
