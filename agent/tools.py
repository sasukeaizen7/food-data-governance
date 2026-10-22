"""The agent's data access: read-only, published tables only, every query logged.

Kept free of any LLM code so the guard rails can be unit-tested on their own.
"""
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SERVING = ROOT / "data" / "serving.duckdb"
AUDIT_LOG = ROOT / "data" / "agent_audit.jsonl"
MAX_ROWS = 50


class QueryRejected(ValueError):
    pass


def connect(path: Path = SERVING) -> duckdb.DuckDBPyConnection:
    # read_only: no writes; enable_external_access off: no reading files, URLs or other databases
    return duckdb.connect(str(path), read_only=True, config={"enable_external_access": False})


def check_query(sql: str) -> str:
    """Accept exactly one SELECT statement (WITH ... SELECT included)."""
    statements = duckdb.extract_statements(sql)
    if len(statements) != 1:
        raise QueryRejected("send exactly one SQL statement")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise QueryRejected(f"only SELECT queries are allowed, got {statements[0].type.name}")
    return statements[0].query.strip().rstrip(";")


def format_rows(columns: list[str], rows: list[tuple], truncated: bool) -> str:
    lines = [" | ".join(columns)]
    lines += [" | ".join("" if v is None else str(v) for v in row) for row in rows]
    if truncated:
        lines.append(f"... more rows not shown (first {MAX_ROWS}); aggregate or add LIMIT")
    return "\n".join(lines)


def audit(entry: dict, log: Path = AUDIT_LOG) -> None:
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_sql(con: duckdb.DuckDBPyConnection, sql: str, log: Path = AUDIT_LOG) -> str:
    started = time.time()
    try:
        query = check_query(sql)
        cur = con.execute(query)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchmany(MAX_ROWS + 1)
    except (QueryRejected, duckdb.Error) as exc:
        audit({"tool": "run_sql", "sql": sql, "error": str(exc)}, log)
        return f"ERROR: {exc}"
    truncated = len(rows) > MAX_ROWS
    audit({"tool": "run_sql", "sql": query, "rows": len(rows[:MAX_ROWS]),
           "ms": round(1000 * (time.time() - started))}, log)
    return format_rows(columns, rows[:MAX_ROWS], truncated)


_NUMBER = re.compile(
    r"\d{1,3}(?:[   ]\d{3})+(?:,\d+)?"   # French: 1 016 235 or 29,02 after groups
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?"               # English: 1,016,235.5
    r"|\d+(?:[.,]\d+)?"                            # plain: 38468, 29.02, 29,02
)


def numbers_in(text: str) -> list[float]:
    found = []
    for raw in _NUMBER.findall(text):
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", raw):
            value = raw.replace(",", "")
        else:
            value = re.sub(r"[   ]", "", raw).replace(",", ".")
        found.append(float(value))
    return found


def _close(a: float, b: float) -> bool:
    # equal up to the rounding a writer would apply (1 decimal, or 0.5 % for big numbers)
    return abs(a - b) <= max(0.051, 0.005 * abs(b))


def unverified_numbers(answer: str, sources: list[str], question: str = "") -> list[float]:
    """Numbers in `answer` that do not come from the tool results.

    A number is verified when it matches a result, or the sum, difference or percentage
    of two results. Small integers (<= 10) and numbers from the question are ignored.
    """
    known = sorted(set(numbers_in("\n".join(sources))))
    derived = set(known)
    # Percentages are kept only between 0 and 100: with dozens of known numbers, ratios
    # outside that range match almost anything and would let invented numbers through.
    percents = set()
    for a in known:
        for b in known:
            derived.update((a + b, a - b))
            if b:
                percents.add(100 * a / b)
    if len(known) <= 60:  # "5 612 excluded (12.7 %)": a percentage of a difference
        for a in known:
            for b in known:
                for c in known:
                    if c:
                        percents.update((100 * (a - b) / c, 100 * (a + b) / c))
    derived.update(p for p in percents if 0 < p <= 100)
    ignored = set(numbers_in(question))
    return [n for n in dict.fromkeys(numbers_in(answer))
            if n > 10 and n not in ignored and not any(_close(n, d) for d in derived)]


def check_answer(answer: str, sources: list[str], question: str = "") -> str:
    """The answer, followed by a warning listing any number the queries do not support."""
    suspect = unverified_numbers(answer, sources, question)
    if not suspect:
        return answer
    listed = ", ".join(f"{n:g}" for n in suspect)
    return f"{answer}\n\n⚠ Chiffres non vérifiés (absents des résultats de requête) : {listed}"


def list_datasets(con: duckdb.DuckDBPyConnection) -> str:
    rows = con.execute("""
        SELECT table_name, row_count, owner_role, description
        FROM catalog WHERE column_name IS NULL ORDER BY table_name
    """).fetchall()
    return format_rows(["table", "rows", "owner", "description"], rows, False)


def describe_dataset(con: duckdb.DuckDBPyConnection, table: str) -> str:
    rows = con.execute("""
        SELECT column_name, data_type, description
        FROM catalog WHERE table_name = ? AND column_name IS NOT NULL
    """, [table]).fetchall()
    if not rows:
        return f"ERROR: no published table named {table!r}; call list_datasets"
    return format_rows(["column", "type", "description"], rows, False)
