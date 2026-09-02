-- Creates a second, separate database for the test suite, so running
-- `uv run pytest` never truncates reports saved during local interactive
-- use of `uv run retail-agent` against the main POSTGRES_DB.
CREATE DATABASE retail_agent_reports_test;
