PII_COLUMNS = frozenset(
    {
        "email",
        "first_name",
        "last_name",
        "street_address",
        "latitude",
        "longitude",
        # Found by inspecting the live `users` schema, not in the original
        # brief: user_geom is a GEOGRAPHY point encoding the same location
        # latitude/longitude carry; postal_code is a standard quasi-identifier.
        "postal_code",
        "user_geom",
    }
)


def _bare_name(column: str) -> str:
    return column.rsplit(".", 1)[-1]


def strip_pii_columns(df):
    """Drop any column whose bare name matches a known PII field, case-insensitively.

    Catches direct selection under the schema name or a table-qualified name
    (e.g. `users.email`). It does not catch a deliberately renamed alias
    (`first_name AS x`) — that gap is covered by the input-side guardrail and
    IAM read-only role, not by this function; see docs/design.md §3.
    """
    redacted = [c for c in df.columns if _bare_name(c).lower() in PII_COLUMNS]
    if not redacted:
        return df, []
    return df.drop(columns=redacted), redacted


def strip_pii_schema(schema):
    """schema: list[dict] as returned by BigQueryTool.get_schema's field lookup."""
    return [field for field in schema if field["name"].lower() not in PII_COLUMNS]
