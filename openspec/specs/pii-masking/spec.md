# pii-masking Specification

## Purpose
Guarantees that personally identifying customer data from the `users` table never reaches the model, the user, logs, traces, or any MCP client, regardless of what SQL the model writes.

## Requirements

### Requirement: PII column registry
The system SHALL treat the following column names as PII: `email`, `first_name`, `last_name`, `street_address`, `latitude`, `longitude`, `postal_code`, `user_geom`.

#### Scenario: Every registered column is stripped
- **WHEN** a query result contains any of the eight registered PII columns
- **THEN** none of those columns appear in the result returned to the caller

#### Scenario: Columns found by inspecting the live schema are covered
- **WHEN** a query result contains `postal_code` or `user_geom`
- **THEN** those columns are stripped exactly like the other registered PII columns

### Requirement: Query results are PII-stripped before leaving the data layer
The system SHALL remove every PII column from a query result after execution and before the result is returned to the model, an MCP client, the conversation transcript, or any trace. Matching SHALL be case-insensitive and SHALL ignore a table qualifier.

#### Scenario: Directly selected PII column
- **WHEN** a query selects `email` and `country`
- **THEN** the returned rows contain `country` only
- **AND** the result reports `email` as a redacted column

#### Scenario: Case and table qualifier do not bypass stripping
- **WHEN** a query result contains a column named `USERS.Email`
- **THEN** that column is stripped

#### Scenario: No PII present
- **WHEN** a query result contains no PII columns
- **THEN** the result is returned unchanged with an empty redacted-columns list

### Requirement: Schema introspection hides PII fields
The system SHALL omit PII fields from any table schema it describes to the model or to an MCP client.

#### Scenario: Describing the users table
- **WHEN** the schema of `users` is requested
- **THEN** the returned field list contains no PII field names

### Requirement: Whole-row selection from users is rejected
The system SHALL reject any query that selects `*` or `<alias>.*` while referencing the `users` table, before the query is sent to BigQuery.

#### Scenario: SELECT star from users
- **WHEN** the model submits `SELECT * FROM users`
- **THEN** the query is rejected with a safety error and BigQuery is never called

#### Scenario: Explicit non-PII columns from users are allowed
- **WHEN** the model submits `SELECT id, country FROM users`
- **THEN** the query passes the safety check
