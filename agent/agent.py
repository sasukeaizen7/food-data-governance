"""Quality-aware data agent: answers questions about the French food catalogue in plain
language, using only the published tables, and says how reliable the data behind
each answer is.

    python agent/agent.py "Quelles marques ont les données les moins fiables ?"
    python agent/agent.py --show-sql "Combien de produits ont un Nutri-Score A ?"

Credentials come from the environment (ANTHROPIC_API_KEY or an `ant auth login` profile).
"""
import argparse
import sys

import anthropic
from anthropic import beta_tool

import tools
from prompt import SYSTEM

MODEL = "claude-opus-5-5"


def build_tools(con, results: list[str]):
    """The agent's tools; every result is also appended to `results` for the number check."""
    @beta_tool
    def list_datasets() -> str:
        """List the published tables with their row counts, owners and descriptions."""
        results.append(out := tools.list_datasets(con))
        return out

    @beta_tool
    def describe_dataset(table: str) -> str:
        """Show the documented columns of one published table.

        Args:
            table: Table name as returned by list_datasets, e.g. fct_products.
        """
        results.append(out := tools.describe_dataset(con, table))
        return out

    @beta_tool
    def run_sql(query: str) -> str:
        """Run one read-only DuckDB SELECT on the published tables and return up to 50 rows.

        Args:
            query: A single SELECT (or WITH ... SELECT) statement.
        """
        if show_sql:
            print(f"\n[sql]\n{query}\n", file=sys.stderr)
        results.append(out := tools.run_sql(con, query))
        return out

    return [list_datasets, describe_dataset, run_sql]


show_sql = False


def ask(question: str) -> str:
    client = anthropic.Anthropic()
    con = tools.connect()
    results: list[str] = []
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        tools=build_tools(con, results),
        messages=[{"role": "user", "content": question}],
        output_config={"effort": "medium"},
        cache_control={"type": "ephemeral"},
        # on a policy decline, re-run on Anthropic's recommended fallback model
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    final = None
    for message in runner:
        final = message
    if final is None:
        return "(no answer)"
    if final.stop_reason == "refusal":
        return "The model declined to answer this question."
    answer = "".join(block.text for block in final.content if block.type == "text").strip()
    return tools.check_answer(answer, results, question)


def main() -> None:
    global show_sql
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--show-sql", action="store_true", help="print each query to stderr")
    args = parser.parse_args()
    show_sql = args.show_sql
    try:
        print(ask(args.question))
    except anthropic.AuthenticationError:
        sys.exit("No valid Anthropic credentials: set ANTHROPIC_API_KEY or run `ant auth login`.")
    except anthropic.RateLimitError:
        sys.exit("Rate limited by the API; try again in a minute.")
    except anthropic.APIStatusError as exc:
        sys.exit(f"API error {exc.status_code}: {exc.message}")
    except anthropic.APIConnectionError:
        sys.exit("Could not reach the Anthropic API.")


if __name__ == "__main__":
    main()
