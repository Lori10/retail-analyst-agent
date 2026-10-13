# sql-safety Specification

## Purpose
Keeps every query the agent runs against BigQuery read-only, single-statement, and bounded in cost, time and size, so a wrong or adversarial model output cannot mutate data or run up a bill.

## Requirements

### Requirement: Only single read-only statements are executed
The system SHALL execute a query only if, after removing SQL comments, it is non-empty, starts with `SELECT` or `WITH`, contains exactly one statement, and contains none of `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `GRANT`, `REVOKE`, `CALL`, `EXECUTE` as a whole word.

#### Scenario: Plain SELECT and CTE are allowed
- **WHEN** the model submits a `SELECT` or a `WITH ... SELECT` query
- **THEN** the query passes the safety check

#### Scenario: Mutating statement is rejected
- **WHEN** the model submits a query containing `DELETE`, `DROP`, or any other forbidden keyword
- **THEN** the query is rejected with a self-correctable safety error and BigQuery is never called

#### Scenario: Multiple statements are rejected
- **WHEN** the model submits two statements separated by `;`
- **THEN** the query is rejected

#### Scenario: Non-SELECT leading statement is rejected
- **WHEN** the query does not start with `SELECT` or `WITH`
- **THEN** the query is rejected

#### Scenario: Keywords inside comments are ignored
- **WHEN** a forbidden keyword appears only inside a `--` or `/* */` comment
- **THEN** the query is not rejected for that keyword

#### Scenario: Empty query is rejected
- **WHEN** the query is empty or only comments
- **THEN** the query is rejected

### Requirement: Dry-run cost cap
The system SHALL dry-run every query first and SHALL refuse to execute it when the estimated bytes processed exceed the configured cap (`BQ_MAX_BYTES_BILLED`, default 1,000,000,000). Execution SHALL also carry the cap as BigQuery's maximum bytes billed.

#### Scenario: Query over the cap
- **WHEN** a query's dry-run estimate exceeds the cap
- **THEN** the query is not executed and a self-correctable "too expensive" error is returned

### Requirement: Execution timeout and row limit
The system SHALL bound each query's execution wait by `BQ_QUERY_TIMEOUT_SECONDS` (default 30) and SHALL return at most `BQ_ROW_LIMIT` rows (default 500).

#### Scenario: Large result
- **WHEN** a query would return more rows than the row limit
- **THEN** at most the row-limit number of rows is returned

### Requirement: Queries run only against the retail dataset
The system SHALL run queries with `bigquery-public-data.thelook_ecommerce` as the default dataset and SHALL describe schemas only for its `orders`, `order_items`, `products` and `users` tables.

#### Scenario: Unknown table
- **WHEN** a schema is requested for a table that does not exist in the dataset
- **THEN** a self-correctable syntax-class error is returned
