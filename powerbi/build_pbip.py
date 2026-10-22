"""Generate the Power BI project (PBIP: text files, versioned in git) from the Parquet
files written by export_powerbi.py.

    python powerbi/build_pbip.py      # then open powerbi/FoodDataQuality.pbip in Power BI Desktop

The semantic model (TMDL) is built from the real Parquet schemas, so column types cannot
drift from the data. The data folder is a model parameter (DataFolder), editable in
Power BI under Transform data > Manage parameters.
"""
import json
import shutil
import uuid
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "powerbi"
OUT = Path(__file__).resolve().parent
NAME = "FoodDataQuality"
MODEL_DIR = OUT / f"{NAME}.SemanticModel"
REPORT_DIR = OUT / f"{NAME}.Report"

TYPES = {"VARCHAR": "string", "INTEGER": "int64", "BIGINT": "int64", "DOUBLE": "double",
         "BOOLEAN": "boolean", "TIMESTAMP": "dateTime", "DATE": "dateTime"}

THEME_FILE = "FoodDataQuality.json"
THEME = {
    "name": "Food Data Quality",
    "dataColors": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
    "background": "#fcfcfb",
    "foreground": "#0b0b0b",
    "tableAccent": "#2a78d6",
}

PERCENT_COLUMNS = {"pct_failed", "pct_of_all_scans", "pct_trusted", "pct_caution", "pct_untrusted", "pct_of_excluded"}

MEASURES = {
    "products": [
        ("Products", "COUNTROWS(products)", "#,0"),
        ("Trusted products", 'CALCULATE([Products], products[trust_level] = "trusted")', "#,0"),
        ("Caution products", 'CALCULATE([Products], products[trust_level] = "caution")', "#,0"),
        ("Untrusted products", 'CALCULATE([Products], products[trust_level] = "untrusted")', "#,0"),
        ("Trusted %", "DIVIDE([Trusted products], [Products])", "0.0%"),
        ("Untrusted %", "DIVIDE([Untrusted products], [Products])", "0.0%"),
        ("Scans", "SUM(products[unique_scans])", "#,0"),
        ("Scans on trusted %",
         'DIVIDE(CALCULATE([Scans], products[trust_level] = "trusted"), [Scans])', "0.0%"),
        ("Avg sugars (trusted)",
         'CALCULATE(AVERAGE(products[sugars_100g]), products[trust_level] = "trusted")', "0.0"),
        ("Avg sugars (all)", "AVERAGE(products[sugars_100g])", "0.0"),
        ("Avg sugars (trusted, A-E)",
         'CALCULATE([Avg sugars (trusted)], KEEPFILTERS(products[nutriscore_grade] IN {"a", "b", "c", "d", "e"}))', "0.0"),
        ("Impossible sugar records", "CALCULATE([Products], products[sugars_100g] > 100)", "#,0"),
        ("Highest declared sugars", "MAX(products[sugars_100g])", "0.00E+00"),
    ],
    "rules": [
        ("Failure rate", "DIVIDE(SUM(rules[n_failed]), SUM(rules[n_checked]))", "0.0%"),
    ],
    "exclusion_reasons": [
        ("Excluded products", "SUM(exclusion_reasons[excluded_products])", "#,0"),
    ],
    "quality_history": [
        ("Last run", "MAX(quality_history[run_date])", "dd/mm/yyyy"),
    ],
}

# (from table.column, to table.column, cross filter both ways)
RELATIONSHIPS = [
    ("product_issues.code", "products.code", True),     # filter products by rule or owner
    ("product_issues.rule_id", "rules.rule_id", False),
    ("rules.owner_role", "owners.owner_role", False),
    ("quality_history.rule_id", "rules.rule_id", False),
    ("exclusion_reasons.rule_id", "rules.rule_id", False),
    ("products.brand_key", "brands.brand_key", False),
]


def tag(*parts: str) -> str:
    """Stable lineage tag, so regenerating the model does not change every line."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "food-data-governance/" + "/".join(parts)))


def quote(name: str) -> str:
    return name if name.replace("_", "").isalnum() else f"'{name}'"


# ---------------------------------------------------------------- semantic model (TMDL)

def table_tmdl(table: str, columns: list[tuple[str, str]]) -> str:
    lines = [f"table {table}", f"\tlineageTag: {tag(table)}", ""]
    for name, expr, fmt in MEASURES.get(table, []):
        lines += [f"\tmeasure {quote(name)} = {expr}", f"\t\tformatString: {fmt}",
                  f"\t\tlineageTag: {tag(table, 'measure', name)}", ""]
    for name, duck_type in columns:
        data_type = TYPES[duck_type]
        lines += [f"\tcolumn {quote(name)}", f"\t\tdataType: {data_type}"]
        if name in PERCENT_COLUMNS:
            lines.append("\t\tformatString: 0.0%")
        elif data_type == "int64":
            lines.append("\t\tformatString: #,0")
        elif duck_type == "DATE":
            lines.append("\t\tformatString: dd/mm/yyyy")
        lines += [f"\t\tlineageTag: {tag(table, name)}", "\t\tsummarizeBy: none",
                  f"\t\tsourceColumn: {name}", ""]
    lines += [
        f"\tpartition {table} = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        f'\t\t\t\t    Source = Parquet.Document(File.Contents(DataFolder & "{table}.parquet"))',
        "\t\t\t\tin",
        "\t\t\t\t    Source",
        "",
    ]
    return "\n".join(lines)


def build_model(schemas: dict[str, list[tuple[str, str]]]) -> None:
    definition = MODEL_DIR / "definition"
    (definition / "tables").mkdir(parents=True)
    (MODEL_DIR / "definition.pbism").write_text(json.dumps({"version": "4.0", "settings": {}}, indent=2))
    (definition / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1600\n")
    model = ["model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
             "\tdiscourageImplicitMeasures", "\tsourceQueryCulture: en-US", ""]
    model += [f"ref table {t}" for t in schemas] + [""]
    (definition / "model.tmdl").write_text("\n".join(model))
    folder = str(DATA) + "\\"
    (definition / "expressions.tmdl").write_text(
        f'expression DataFolder = "{folder}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'
        f"\tlineageTag: {tag('DataFolder')}\n\n\tannotation PBI_ResultType = Text\n")
    rels = []
    for src, dst, both in RELATIONSHIPS:
        rels += [f"relationship {tag('rel', src, dst)}"]
        if both:
            rels.append("\tcrossFilteringBehavior: bothDirections")
        rels += [f"\tfromColumn: {src}", f"\ttoColumn: {dst}", ""]
    (definition / "relationships.tmdl").write_text("\n".join(rels))
    for table, columns in schemas.items():
        (definition / "tables" / f"{table}.tmdl").write_text(table_tmdl(table, columns))


# ---------------------------------------------------------------- report (PBIR-Legacy report.json)

def ref(table: str, prop: str, measure: bool = False) -> tuple[str, str, bool]:
    return table, prop, measure


def expr(alias: str, prop: str, measure: bool) -> dict:
    return {("Measure" if measure else "Column"): {"Expression": {"SourceRef": {"Source": alias}}, "Property": prop}}


def literal(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def visual(name: str, vtype: str, pos: tuple[int, int, int, int], roles: dict, title: str,
           order_by: tuple | None = None, descending: bool = True, labels: bool = False,
           objects: dict | None = None) -> dict:
    x, y, w, h = pos
    fields = [f for refs in roles.values() for f in refs]
    tables = list(dict.fromkeys(t for t, _, _ in fields + ([order_by] if order_by else [])))
    alias = {t: f"t{i}" for i, t in enumerate(tables)}
    select = [{**expr(alias[t], p, m), "Name": f"{t}.{p}"} for t, p, m in dict.fromkeys(fields)]
    query = {"Version": 2, "From": [{"Name": alias[t], "Entity": t, "Type": 0} for t in tables], "Select": select}
    if order_by:
        t, p, m = order_by
        query["OrderBy"] = [{"Direction": 2 if descending else 1, "Expression": expr(alias[t], p, m)}]
    projections = {role: [{"queryRef": f"{t}.{p}", **({"active": True} if i == 0 and role == "Category" else {})}
                          for i, (t, p, _) in enumerate(refs)] for role, refs in roles.items()}
    config = {
        "name": name,
        "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": 0, "width": w, "height": h}}],
        "singleVisual": {
            "visualType": vtype,
            "projections": projections,
            "prototypeQuery": query,
            "drillFilterOtherVisuals": True,
            "vcObjects": {"title": [{"properties": {
                "show": literal("true"),
                "text": literal("'" + title.replace("'", "''") + "'"),
                "fontSize": literal("12D"),
                "fontColor": {"solid": {"color": literal("'#0b0b0b'")}},
                "bold": literal("true"),
            }}]},
        },
    }
    if labels:
        config["singleVisual"]["objects"] = {"labels": [{"properties": {
            "show": literal("true"), "labelDisplayUnits": literal("1D")}}]}
    if objects:
        config["singleVisual"].setdefault("objects", {}).update(objects)
    return {"x": x, "y": y, "z": 0, "width": w, "height": h, "config": json.dumps(config), "filters": "[]"}


def card(name, pos, table, measure, title):
    # display units 1 = show the full number (1,275,888, not "1M")
    return visual(name, "card", pos, {"Values": [ref(table, measure, True)]}, title,
                  objects={"labels": [{"properties": {"labelDisplayUnits": literal("1D")}}]})


# status colours: they mean the same thing on every page
TRUST_COLORS = {"trusted": "#1baf7a", "caution": "#eda100", "untrusted": "#e34948"}


def colour_by(table: str, column: str, colours: dict[str, str]) -> dict:
    """dataPoint objects that give each category value a fixed colour."""
    return {"dataPoint": [{
        "properties": {"fill": {"solid": {"color": literal(f"'{colour}'")}}},
        "selector": {"data": [{"scopeId": {"Comparison": {
            "ComparisonKind": 0,
            "Left": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": column}},
            "Right": {"Literal": {"Value": f"'{value}'"}},
        }}}]},
    } for value, colour in colours.items()]}


def in_filter(table: str, column: str, values: list[str]) -> dict:
    """A page filter keeping only `values` of table.column."""
    return {
        "name": f"filter_{table}_{column}",
        "expression": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": column}},
        "filter": {"Version": 2, "From": [{"Name": "f", "Entity": table, "Type": 0}], "Where": [{"Condition": {"In": {
            "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "f"}}, "Property": column}}],
            "Values": [[{"Literal": {"Value": f"'{v}'"}}] for v in values],
        }}}]},
        "type": "Categorical",
        "howCreated": 1,
    }


def page(name: str, display: str, ordinal: int, visuals: list[dict], filters: list[dict] | None = None) -> dict:
    return {"name": name, "displayName": display, "displayOption": 1, "width": 1280, "height": 720,
            "ordinal": ordinal, "config": "{}", "filters": json.dumps(filters or []), "visualContainers": visuals}


def build_report() -> None:
    REPORT_DIR.mkdir(parents=True)
    (REPORT_DIR / "definition.pbir").write_text(json.dumps(
        {"version": "1.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}}, indent=2))
    overview = page("overview", "Overview", 0, [
        card("c_products", (20, 20, 230, 110), "products", "Products", "Products sold in France"),
        card("c_trusted", (265, 20, 230, 110), "products", "Trusted %", "Trusted"),
        card("c_untrusted", (510, 20, 230, 110), "products", "Untrusted %", "Untrusted (blocking issue)"),
        card("c_scans", (755, 20, 250, 110), "products", "Scans on trusted %", "App scans on trusted products"),
        card("c_run", (1020, 20, 240, 110), "quality_history", "Last run", "Last pipeline run"),
        visual("d_trust", "donutChart", (20, 145, 380, 290),
               {"Category": [ref("products", "trust_level")], "Y": [ref("products", "Products", True)]},
               "Products by trust level", objects=colour_by("products", "trust_level", TRUST_COLORS)),
        visual("b_rules", "clusteredBarChart", (415, 145, 845, 555),
               {"Category": [ref("rules", "rule_label")], "Y": [ref("rules", "Failure rate", True)]},
               "Failure rate of each quality rule", order_by=ref("rules", "Failure rate", True), labels=True),
        visual("s_owner", "slicer", (20, 450, 380, 250), {"Values": [ref("owners", "title")]},
               "Owner (filters the rules and products they own)"),
    ])
    reasons = page("reasons", "Why products are excluded", 1, [
        visual("b_reasons", "clusteredBarChart", (20, 20, 760, 680),
               {"Category": [ref("exclusion_reasons", "rule_name")],
                "Y": [ref("exclusion_reasons", "Excluded products", True)]},
               "Excluded products failing each blocking or major rule (a product can fail several)",
               order_by=ref("exclusion_reasons", "Excluded products", True), labels=True),
        visual("t_owners", "tableEx", (795, 20, 465, 680),
               {"Values": [ref("owners", "title"), ref("owners", "responsibility")]},
               "Who fixes what", objects={"values": [{"properties": {"wordWrap": literal("true")}}],
                                          "columnHeaders": [{"properties": {"wordWrap": literal("true")}}]}),
    ])
    brands = page("brands", "Brands", 2, [
        visual("s_brand", "slicer", (20, 20, 300, 680), {"Values": [ref("brands", "brand")]}, "Brand"),
        visual("t_brands", "tableEx", (335, 20, 925, 680),
               {"Values": [ref("brands", "brand"), ref("brands", "n_products"), ref("brands", "scans"),
                           ref("brands", "pct_trusted"), ref("brands", "pct_caution"),
                           ref("brands", "pct_untrusted"), ref("brands", "top_issue_rule")]},
               "Brand scorecard (Open Food Facts records, not brand data)", order_by=ref("brands", "scans")),
    ])
    to_fix = page("to_fix", "Products to fix", 3, [
        visual("s_level", "slicer", (20, 20, 300, 150), {"Values": [ref("products", "trust_level")]}, "Trust level"),
        visual("s_rule", "slicer", (20, 185, 300, 515), {"Values": [ref("rules", "rule_label")]}, "Failed rule"),
        visual("t_products", "tableEx", (335, 20, 925, 680),
               {"Values": [ref("products", "code"), ref("products", "product_name"), ref("products", "brand"),
                           ref("products", "failed_rules"), ref("products", "unique_scans"),
                           ref("products", "trust_level")]},
               "Most-scanned products first: fix these first", order_by=ref("products", "unique_scans")),
    ], filters=[in_filter("products", "trust_level", ["caution", "untrusted"])])
    # One record (a coffee capsule declaring 1.17e33 g of sugar per 100 g) is enough to push the
    # plain average of 1.28M products to ~1e27: the page shows why averages run on trusted data.
    nutrition = page("nutrition", "Trusted vs all", 4, [
        card("c_sug_t", (20, 20, 300, 120), "products", "Avg sugars (trusted)", "Avg sugars, trusted (g/100g)"),
        card("c_sug_a", (335, 20, 300, 120), "products", "Avg sugars (all)",
             "Avg sugars, all products"),
        card("c_imp", (650, 20, 300, 120), "products", "Impossible sugar records",
             "Records with > 100 g sugar/100 g"),
        card("c_max", (965, 20, 295, 120), "products", "Highest declared sugars",
             "Highest sugar declared"),
        visual("b_nutri", "clusteredColumnChart", (20, 155, 520, 545),
               {"Category": [ref("products", "nutriscore_grade")],
                "Y": [ref("products", "Avg sugars (trusted, A-E)", True)]},
               "Average sugars by Nutri-Score grade, trusted products", labels=True),
        visual("t_extreme", "tableEx", (555, 155, 705, 545),
               {"Values": [ref("products", "product_name"), ref("products", "brand"), ref("products", "sugars_100g"),
                           ref("products", "trust_level"), ref("products", "failed_rules")]},
               "Highest declared sugars: the records behind the gap", order_by=ref("products", "sugars_100g")),
    ])
    # Power BI refuses to render a report without a theme; this one uses the same validated
    # palette as the README charts (charts.py).
    resources = REPORT_DIR / "StaticResources" / "RegisteredResources"
    resources.mkdir(parents=True)
    (resources / THEME_FILE).write_text(json.dumps(THEME, indent=2))
    report = {
        "config": json.dumps({
            "version": "5.59",
            "themeCollection": {"customTheme": {"name": THEME_FILE, "version": "5.59", "type": 1}},
            "activeSectionIndex": 0,
            "defaultDrillFilterOtherVisuals": True,
        }),
        "layoutOptimization": 0,
        "resourcePackages": [{"resourcePackage": {
            "name": "RegisteredResources", "type": 1, "disabled": False,
            "items": [{"type": 202, "path": THEME_FILE, "name": THEME_FILE}],
        }}],
        "sections": [overview, reasons, brands, to_fix, nutrition],
    }
    (REPORT_DIR / "report.json").write_text(json.dumps(report, indent=2))


def main() -> None:
    con = duckdb.connect()
    schemas = {}
    for path in sorted(DATA.glob("*.parquet"), key=lambda p: (p.stem != "products", p.stem)):
        schemas[path.stem] = con.execute(
            f"SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM '{path.as_posix()}')").fetchall()
    for d in (MODEL_DIR, REPORT_DIR):
        shutil.rmtree(d, ignore_errors=True)
    build_model(schemas)
    build_report()
    (OUT / f"{NAME}.pbip").write_text(json.dumps({
        "version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
        "settings": {"enableAutoRecovery": True}}, indent=2))
    print(f"wrote {OUT.relative_to(ROOT)}/{NAME}.pbip ({len(schemas)} tables)")


if __name__ == "__main__":
    main()
