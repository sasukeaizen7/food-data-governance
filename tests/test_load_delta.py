"""Parsing of the daily delta records (newer Open Food Facts schema)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingest"))
from common import DELTA_COLUMNS  # noqa: E402
from load_delta import nutrients, to_row  # noqa: E402


def nutrient_set(source, per="100g", **values):
    return {"source": source, "per": per, "preparation": "as_sold",
            "nutrients": {k.replace("_", "-"): {"value": v} for k, v in values.items()}}


DOC = {
    "code": "3017620422003",
    "product_name": "Nutella",
    "countries_tags": ["en:france"],
    "nutrition": {
        "aggregated_set": {"per": "100g", "preparation": "as_sold",
                           "nutrients": {"sugars": {"value": 56.3}, "salt": {"value": 0.107}}},
        "input_sets": [
            nutrient_set("manufacturer", sugars=56.3, salt=0.107),
            nutrient_set("manufacturer", per="serving", sugars=8.4, salt=0.016),
            nutrient_set("packaging", sugars=60, salt=15),
        ],
    },
}


def test_published_value_comes_from_the_aggregated_set():
    values = nutrients(DOC)
    assert values["sugars_100g"] == 56.3
    assert values["salt_100g"] == 0.107


def test_per_source_values_are_per_100g():
    values = nutrients(DOC)
    assert values["mfr_sugars_100g"] == 56.3     # not the per-serving 8.4
    assert values["pkg_sugars_100g"] == 60.0
    assert values["pkg_salt_100g"] == 15.0
    assert values["mfr_energy_kcal_100g"] is None


def test_old_schema_falls_back_to_flat_nutriments():
    values = nutrients({"nutriments": {"sugars_100g": 12, "fat_100g": "n/a"}})
    assert values["sugars_100g"] == 12.0
    assert values["fat_100g"] is None


def test_row_matches_delta_columns():
    row = to_row(DOC)
    assert len(row) == len(DELTA_COLUMNS)
    assert row[0] == "3017620422003"
