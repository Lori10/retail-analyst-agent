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

TOOLS = [RUN_QUERY, GET_SCHEMA]
