"""The same quality-aware agent, run on a local open-source model through Ollama:
free, offline, and the data never leaves the machine.

    ollama pull qwen3:8b
    python agent/agent_local.py "Quelles marques ont les données les moins fiables ?"
    python agent/agent_local.py --model llama3.1:8b --show-sql "..."

Same tools, same guard rails (read-only, published tables only, audit log, every
number in the answer checked against the query results) and same instructions as
agent.py; only the model changes.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

import tools
from prompt import SYSTEM

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "qwen3:8b"
MAX_STEPS = 15

TOOLS = [
    {"type": "function", "function": {
        "name": "list_datasets",
        "description": "List the published tables with their row counts, owners and descriptions.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "describe_dataset",
        "description": "Show the documented columns of one published table.",
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string", "description": "Table name as returned by list_datasets, e.g. fct_products."},
        }, "required": ["table"]},
    }},
    {"type": "function", "function": {
        "name": "run_sql",
        "description": "Run one read-only DuckDB SELECT on the published tables and return up to 50 rows.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "A single SELECT (or WITH ... SELECT) statement."},
        }, "required": ["query"]},
    }},
]


def chat(model: str, messages: list[dict]) -> dict:
    body = json.dumps({
        "model": model, "messages": messages, "tools": TOOLS, "stream": False,
        # keep the model's reasoning on: without it qwen3 answers ~5x faster but states numbers
        # it never queried (tested: 20,000 "caution" products instead of 214,815)
        "think": True,
        "options": {"num_ctx": 16384, "temperature": 0},
    }).encode()
    req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read())["message"]


def call_tool(con, name: str, args: dict, show_sql: bool) -> str:
    if name == "list_datasets":
        return tools.list_datasets(con)
    if name == "describe_dataset":
        return tools.describe_dataset(con, str(args.get("table", "")))
    if name == "run_sql":
        query = str(args.get("query", ""))
        if show_sql:
            print(f"\n[sql]\n{query}\n", file=sys.stderr)
        return tools.run_sql(con, query)
    return f"ERROR: unknown tool {name!r}"


def ask(question: str, model: str = DEFAULT_MODEL, show_sql: bool = False) -> str:
    con = tools.connect()
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    results: list[str] = []
    for _ in range(MAX_STEPS):
        message = chat(model, messages)
        messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            return tools.check_answer((message.get("content") or "").strip(), results, question)
        for call in calls:
            fn = call.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            result = call_tool(con, fn.get("name", ""), args, show_sql)
            results.append(result)
            messages.append({"role": "tool", "tool_name": fn.get("name", ""), "content": result})
    return f"(stopped after {MAX_STEPS} steps without a final answer)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model (default {DEFAULT_MODEL})")
    parser.add_argument("--show-sql", action="store_true", help="print each query to stderr")
    args = parser.parse_args()
    try:
        print(ask(args.question, args.model, args.show_sql))
    except urllib.error.URLError:
        sys.exit("Ollama is not reachable on localhost:11434. Install it from ollama.com, then "
                 f"`ollama pull {args.model}`.")


if __name__ == "__main__":
    main()
