import re

from retail_agent.errors import SQLSafetyError

_FORBIDDEN_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "CALL",
    "EXECUTE",
)

_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_KEYWORD_PATTERNS = {kw: re.compile(rf"\b{kw}\b", re.IGNORECASE) for kw in _FORBIDDEN_KEYWORDS}
_LEADING_STATEMENT_RE = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_USERS_TABLE_RE = re.compile(r"\busers\b", re.IGNORECASE)
_SELECT_STAR_RE = re.compile(r"SELECT\s+(\*|\w+\.\*)", re.IGNORECASE)


def check_read_only(sql: str) -> None:
    """Raise SQLSafetyError unless sql is a single, safe, read-only statement.

    A regex/keyword allowlist, not a full SQL parser — deliberately, per
    docs/design.md §3: the real backstop against adversarial SQL is the
    service account's IAM read-only role, so this only needs to catch the
    realistic case (an LLM accidentally generating DML/DDL), not defeat a
    determined attacker on its own.
    """
    stripped = _COMMENT_RE.sub(" ", sql).strip()
    if not stripped:
        raise SQLSafetyError("Empty query.")

    if not _LEADING_STATEMENT_RE.match(stripped):
        raise SQLSafetyError("Only SELECT/WITH statements are allowed.")

    body = stripped.rstrip(";").strip()
    if ";" in body:
        raise SQLSafetyError("Multiple statements are not allowed.")

    for keyword, pattern in _KEYWORD_PATTERNS.items():
        if pattern.search(stripped):
            raise SQLSafetyError(f"Disallowed keyword in query: {keyword}.")

    if _USERS_TABLE_RE.search(stripped) and _SELECT_STAR_RE.search(stripped):
        raise SQLSafetyError(
            "SELECT * against the users table is not allowed — select explicit, non-PII columns."
        )
