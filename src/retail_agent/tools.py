RUN_QUERY = {
    "name": "run_query",
    "description": (
        "Execute a single read-only SQL SELECT/WITH statement against the "
        "thelook_ecommerce dataset (tables: orders, order_items, products, "
        "users) and return the results. PII columns are stripped automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "A single read-only SELECT or WITH SQL statement.",
            }
        },
        "required": ["sql"],
    },
}

GET_SCHEMA = {
    "name": "get_schema",
    "description": (
        "Get the column names and types for one of the available tables. "
        "PII columns are never included in the result."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "enum": ["orders", "order_items", "products", "users"],
            }
        },
        "required": ["table_name"],
    },
}

SAVE_REPORT = {
    "name": "save_report",
    "description": "Save a report (with optional action items) to the user's saved reports library.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short title for the report."},
            "content": {"type": "string", "description": "The full report text."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional free-text tags (e.g. client or topic names) to help find this report later.",
            },
        },
        "required": ["title", "content"],
    },
}

LIST_REPORTS = {
    "name": "list_reports",
    "description": "List the reports the current user has saved.",
    "parameters": {"type": "object", "properties": {}},
}

DELETE_REPORTS = {
    "name": "delete_reports",
    "description": (
        "Delete one or more of the user's saved reports. This is destructive: after calling this "
        "tool, the system automatically pauses and shows the user the exact reports that would be "
        "deleted, asking them to confirm before anything is actually removed. Never ask the user to "
        "confirm yourself before or after calling this tool — the system's own confirmation step "
        "already handles that."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["conversation", "all"],
                "description": (
                    "'conversation' to only consider reports saved earlier in this same conversation "
                    "(e.g. 'delete all the reports we made in this conversation'); 'all' to consider "
                    "every report the user has ever saved."
                ),
            },
            "title_contains": {
                "type": "string",
                "description": (
                    "Optional substring to match against report titles (e.g. 'delete all reports "
                    "mentioning Client X' -> title_contains='Client X')."
                ),
            },
        },
        "required": ["scope"],
    },
}

TOOLS = [RUN_QUERY, GET_SCHEMA, SAVE_REPORT, LIST_REPORTS, DELETE_REPORTS]
