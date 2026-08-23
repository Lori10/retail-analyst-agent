"""Deliberately does NOT call load_dotenv() here.

GOOGLE_CLOUD_PROJECT/GEMINI_API_KEY commonly live in a local .env file for
everyday development (config.py loads it for the CLI). If this conftest
loaded .env too, just having .env configured would make `uv run pytest`
silently run live, billed integration tests on every invocation — the
206s slowdown that prompted this change. Requiring real, explicitly
exported environment variables (not a .env file) makes running this
suite an opt-in action instead of an accidental side effect of running
the full test suite.

To run these tests:
    set -a && source .env && set +a && uv run pytest tests/integration
"""
