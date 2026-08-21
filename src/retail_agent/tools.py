from google.genai import types

RUN_QUERY = types.FunctionDeclaration(
    name="run_query",
    description=(
        "Execute a single read-only SQL SELECT/WITH statement against the "
        "thelook_ecommerce dataset (tables: orders, order_items, products, "
        "users) and return the results. PII columns are stripped automatically."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "A single read-only SELECT or WITH SQL statement.",
            }
        },
        "required": ["sql"],
    },
)

GET_SCHEMA = types.FunctionDeclaration(
    name="get_schema",
    description=(
        "Get the column names and types for one of the available tables. "
        "PII columns are never included in the result."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "enum": ["orders", "order_items", "products", "users"],
            }
        },
        "required": ["table_name"],
    },
)

TOOLS = [types.Tool(function_declarations=[RUN_QUERY, GET_SCHEMA])]
