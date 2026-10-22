# LEARN: the design decisions, explained

## 1. Why governance before the AI agent

An agent that writes SQL is easy to build. An agent whose answers you can trust is not:
it will happily average a column where some rows are wrong by a factor of 4 (kJ typed
as kcal) and present the result with confidence. So the project is built in this order:

1. **Agree on meanings** (glossary): what is "a product", "a brand", "trusted data"?
2. **Name owners** (data_owners): who fixes a wrong barcode, who fixes a wrong sugar value?
3. **Write the rules down** (dq_rules), each with a dimension, a severity and an owner.
4. **Measure** every rule on every product, every day (dq_results, dq_history).
5. **Publish** only documented tables (serving.duckdb), with the quality status on each row.
6. **Then** let an agent read the published tables, with instructions to use that status.

## 2. Layers

| Layer | Where | What |
|---|---|---|
| bronze | `bronze.products_full`, `bronze.products_delta` | raw, all countries, one row per load |
| staging | `stg_products` (view) | latest version of each product across full + daily loads |
| silver | `silver.products` | products sold in France, typed and cleaned |
| quality | `quality.dq_results` | one row per (product, rule) the rule applies to |
| gold | `fct_products`, `dq_rule_summary`, `brand_quality`, `dq_history` | what consumers use |
| governance | `dq_rules`, `glossary`, `data_owners` (dbt seeds) | the registry, versioned in git |
| serving | `data/serving.duckdb` | only the tables marked `meta.published: true` |

## 3. Full load + daily deltas

Open Food Facts publishes a full export (~8 GB Parquet) and one small JSONL file per day
with the products changed that day. The full export is loaded once; each run loads the new
daily files (`bronze.load_log` makes it idempotent) and `stg_products` keeps, per barcode,
the most recent version (`qualify row_number() over (partition by code order by
last_modified_t desc)`).

**A schema change in the source.** The daily files use a newer product schema than the
full export: nutrients sit in `nutrition.input_sets`, one set per *source*
(`manufacturer`, `packaging` typed in by a contributor, `estimate` from the ingredients),
plus an `aggregated_set` holding the value Open Food Facts publishes. The first version of
the loader read the `packaging` set and found Nutella with 60 g of sugar for 15 g of
carbohydrates and 15 g of salt. That was a contributor's typo, not the published value:
the published value comes from the manufacturer (56.3 g, 0.107 g). The loader now uses the
aggregated set (the same value the full export holds), and keeps the manufacturer and
packaging values side by side for rule R17.

## 4. How a quality rule is built

Every rule in `dq_results.sql` has three parts:

- **applies**: does the product have the data this rule needs? A product with no sugar
  value is *not checked* by "sugars ≤ carbohydrates". Counting it as a pass would inflate
  the pass rate with products we know nothing about.
- **failed**: the business condition, with a tolerance (rounding on labels is legal:
  `sugars > carbs + 0.5`, energy within 20 % *and* 20 kcal).
- **observed**: the values behind a failure, so a steward can act without re-querying.

The severity decides the product's `trust_level`:

| Severity | Meaning | Effect |
|---|---|---|
| blocking | the data is certainly wrong (physically impossible) | `untrusted` |
| major | probably wrong, or missing what most uses need | `caution` |
| minor | incomplete, still usable | stays `trusted` |
| info | worth knowing, not an error (in-store barcode) | none |

The energy rule uses the EU conversion factors (fat 9 kcal/g, carbohydrates and protein 4,
fibre 2, polyols 2.4, alcohol 7). The GTIN rule implements the GS1 check digit in SQL
(`macros/gtin.sql`): weights 3, 1, 3, 1 … from the right, check digit = (10 − sum mod 10) mod 10.

**Calibrating a rule on real data.** The first kJ/kcal rule demanded kJ = 4.184 × kcal
within 5 % and failed 14.8 % of products. Labels compute each unit separately from each
nutrient (fat 37 kJ / 9 kcal, carbohydrates 17 / 4, fibre 8 / 2), so correct ratios span
4.0 to 4.25. The rule now flags ratios outside 3.5–5.0 (5.6 %), which still catches every
kJ value typed in the kcal field. Same story for "no nutrition table": water, alcohol,
fresh produce, spices, salt, tea, coffee, vinegar and honey are exempt under EU regulation
1169/2011 (28.8 % → 12.8 %). Look at the top failures of every new rule before trusting it.

## 5. Testing the rules themselves

`tests/test_rules.py` builds a tiny bronze layer of hand-made products, each with exactly
one planted defect (a wrong check digit, kJ typed as kcal, sugars above carbohydrates…),
runs the real dbt project on it, and asserts that **exactly** the expected rules fail. A
rule that fires too often is as harmful as one that never fires: people stop reading alerts.

## 6. Data contracts and tests in dbt

- `dq_rule_summary` has an enforced **contract**: column names and types are checked at
  build time, so a change that breaks consumers fails the build instead of the dashboard.
- Generic tests: unique / not_null keys, accepted values (severity, trust level, status),
  relationships (every rule has an owner that exists, every result points to a rule).
- `products_snapshot` keeps an **SCD type 2** history of the business fields, so "when did
  this product's sugar value change, and from what?" has an answer.
- `dq_history` is an **incremental** model: each run appends that day's failure rates.

## 7. Publishing and the agent's guard rails

`publish.py` copies only models marked `meta.published: true` into `serving.duckdb`, with a
`catalog` table built from the dbt documentation. The agent:

- opens that file **read-only** with `enable_external_access = false` (no files, URLs or
  other databases);
- runs **one SELECT** per call (checked with `duckdb.extract_statements`), 50 rows max;
- logs every query to `data/agent_audit.jsonl` (who asked what, which SQL ran);
- is instructed to compute figures on `trusted` products and to say what it left out.

The agent uses Claude (`claude-opus-5-5`) through the Anthropic SDK's tool runner with three
tools: `list_datasets`, `describe_dataset`, `run_sql`. `agent_local.py` runs the same tools
and instructions on a local model through Ollama's chat API.

**Don't let the model recompute a governed metric.** Asked why products were excluded, the
local model summed rule failures per severity. A product failing two rules was counted
twice: 283,060 instead of 259,653. The fix is not a better prompt but a published metric,
`dq_exclusion_reasons`, computed once in dbt at the right grain (distinct products) and
documented. This is what a semantic layer is for.

**Check numbers in code, not in the prompt.** Telling a model "every number must come from a
query" is not enough: an 8B model still invents numbers in side remarks. `check_answer`
extracts every number from the answer (French and English formats), and accepts it only if it
matches a query result, or a sum, difference or percentage of results (percentages kept
between 0 and 100: with dozens of known numbers, unbounded ratios match almost anything,
which first let an invented number through). Anything else is flagged under the answer.

## 8. Exercises

1. Add a rule: "Nutri-Score is A but the product is a soda". Where does it go, what
   severity, which owner? Add its test case first.
2. Make `brand_quality` weight products by scans. Does the ranking change?
3. Run the pipeline two days in a row and query `snapshots.products_snapshot` for the
   products whose sugar value changed.
4. Ask the agent a question where trusted and all products give different answers. Does
   it say so?

## 9. Interview pitch (2 minutes)

"I built a data quality and governance layer on Open Food Facts, 1.3 million food products
sold in France. Seventeen business rules, each with an owner and a severity, run on every
product every day in dbt, and each product gets a trust level. Only documented tables are
published, and an AI agent answers questions on them, but it computes figures on trusted
data and says what it excluded. The most interesting finding came from the data itself:
the source switched to a schema with one nutrient set per source, and the manufacturer and
contributors disagree on about one product in ten. Nutella had 15 g of salt in the
contributor data. That's the golden-record problem every company has with its own
product, customer or supplier data."
