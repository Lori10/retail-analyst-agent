import pandas as pd

from retail_agent.pii import PII_COLUMNS, strip_pii_columns, strip_pii_schema


def test_strips_known_pii_columns():
    df = pd.DataFrame({"id": [1], "email": ["a@b.com"], "order_count": [3]})
    clean, redacted = strip_pii_columns(df)
    assert redacted == ["email"]
    assert list(clean.columns) == ["id", "order_count"]


def test_case_insensitive_and_table_qualified_names():
    df = pd.DataFrame({"users.Email": ["a@b.com"], "First_Name": ["A"], "id": [1]})
    clean, redacted = strip_pii_columns(df)
    assert set(redacted) == {"users.Email", "First_Name"}
    assert list(clean.columns) == ["id"]


def test_no_pii_columns_present_is_a_noop():
    df = pd.DataFrame({"id": [1], "total_revenue": [100.0]})
    clean, redacted = strip_pii_columns(df)
    assert redacted == []
    assert list(clean.columns) == ["id", "total_revenue"]


def test_all_claude_md_pii_columns_are_covered():
    for col in [
        "email",
        "first_name",
        "last_name",
        "street_address",
        "latitude",
        "longitude",
        "postal_code",
        "user_geom",
    ]:
        assert col in PII_COLUMNS


def test_strips_columns_found_by_inspecting_live_schema():
    df = pd.DataFrame({"id": [1], "postal_code": ["94105"], "user_geom": ["POINT(1 1)"]})
    clean, redacted = strip_pii_columns(df)
    assert set(redacted) == {"postal_code", "user_geom"}
    assert list(clean.columns) == ["id"]


def test_strip_pii_schema_excludes_pii_fields():
    schema = [
        {"name": "id", "type": "INTEGER"},
        {"name": "email", "type": "STRING"},
        {"name": "latitude", "type": "FLOAT"},
    ]
    clean_schema = strip_pii_schema(schema)
    assert {field["name"] for field in clean_schema} == {"id"}
