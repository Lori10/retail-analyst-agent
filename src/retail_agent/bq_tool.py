import logging

from google.api_core import exceptions as gax
from google.cloud import bigquery

from retail_agent.errors import (
    QueryExecutionError,
    QueryPermissionError,
    QuerySyntaxError,
    QueryTooExpensiveError,
    QueryTransientError,
)
from retail_agent.pii import strip_pii_columns, strip_pii_schema
from retail_agent.resilience import bounded_backoff
from retail_agent.sql_safety import check_read_only

DEFAULT_DATASET = "bigquery-public-data.thelook_ecommerce"

logger = logging.getLogger(__name__)

# BigQuery/api_core exception buckets. Order matters: PermissionDenied and
# Unauthorized subclass ClientError like BadRequest does, so permission is
# checked first to avoid being misclassified as a syntax error.
_PERMISSION_EXCEPTIONS = (gax.Forbidden, gax.Unauthorized)
_SYNTAX_EXCEPTIONS = (gax.BadRequest, gax.NotFound, gax.Conflict)
_TRANSIENT_EXCEPTIONS = (gax.ServerError, gax.TooManyRequests, gax.RetryError, TimeoutError, ConnectionError)


def _classify(exc: Exception) -> QueryExecutionError:
    """Map a raw exception from the BigQuery client into a typed AgentError.

    Args:
        exc: The exception raised by a `bigquery.Client` call.

    Returns:
        A `QueryPermissionError`, `QuerySyntaxError`, or `QueryTransientError`
        if `exc` matches a known bucket; otherwise the base
        `QueryExecutionError`, logged as `bq_unclassified_exception` so a
        recurring case can get its own typed subclass.
    """
    if isinstance(exc, _PERMISSION_EXCEPTIONS):
        return QueryPermissionError(str(exc))
    if isinstance(exc, _SYNTAX_EXCEPTIONS):
        return QuerySyntaxError(str(exc))
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        return QueryTransientError(str(exc))
    logger.warning("bq_unclassified_exception", extra={"exc_type": type(exc).__name__})
    return QueryExecutionError(str(exc))


@bounded_backoff(retry_on=QueryTransientError, attempts=2, logger=logger)
def _call_bq(fn, *, stage: str):
    """Run a BigQuery client call, classifying failures and retrying
    transient ones (via the `bounded_backoff` decorator).

    Args:
        fn: Zero-argument callable that performs the actual client call
            (e.g. `client.query(...)` or `client.get_table(...)`).
        stage: Short label identifying the call site, used only in the
            `bq_call_failed` log line (e.g. `"dry_run"`, `"execute"`,
            `"get_schema"`).

    Returns:
        Whatever `fn()` returns, unchanged, on success.

    Raises:
        QueryPermissionError: `fn()` raised a permission-denied error.
        QuerySyntaxError: `fn()` raised a bad-request/not-found/conflict
            error.
        QueryTransientError: `fn()` raised a transient/timeout error on
            every attempt.
        QueryExecutionError: `fn()` raised an exception not in any known
            bucket.
    """
    try:
        return fn()
    except QueryTransientError:
        raise
    except Exception as exc:
        typed = _classify(exc)
        logger.warning("bq_call_failed", extra={"stage": stage, "error_class": type(typed).__name__})
        raise typed from exc


class BigQueryTool:
    """Safety/cost/PII wrapper around a bigquery.Client.

    Attributes:
        max_bytes_billed: Dry-run byte cap; a query estimated over this
            raises `QueryTooExpensiveError` before it ever runs billably.
        row_limit: Maximum rows fetched per query.
        timeout_seconds: Client-side wait timeout for query execution.
    """

    def __init__(
        self,
        client: bigquery.Client,
        max_bytes_billed: int,
        row_limit: int,
        timeout_seconds: float,
        default_dataset: str = DEFAULT_DATASET,
    ) -> None:
        """Initialize the wrapper around an already-authenticated client.

        Args:
            client: An authenticated `bigquery.Client`, owned directly by
                this wrapper rather than via `provided/bq_runner.py`.
            max_bytes_billed: See `max_bytes_billed` attribute.
            row_limit: See `row_limit` attribute.
            timeout_seconds: See `timeout_seconds` attribute.
            default_dataset: Fully-qualified `project.dataset` used to
                resolve unqualified table names in queries.
        """
        self._client = client
        self._default_dataset = default_dataset
        self.max_bytes_billed = max_bytes_billed
        self.row_limit = row_limit
        self.timeout_seconds = timeout_seconds

    def get_schema(self, table_name: str) -> list[dict]:
        """Look up a table's column names/types, with PII columns removed.

        Args:
            table_name: Bare table name within `default_dataset` (e.g.
                `"users"`).

        Returns:
            A list of `{"name", "type", "mode", "description"}` dicts, one
            per non-PII column.

        Raises:
            QuerySyntaxError: The table doesn't exist.
            QueryPermissionError: The service account lacks access.
            QueryTransientError: The BigQuery call failed transiently on
                every retry attempt.
        """
        table = _call_bq(
            lambda: self._client.get_table(f"{self._default_dataset}.{table_name}"),
            stage="get_schema",
        )
        schema = [
            {
                "name": field.name,
                "type": field.field_type,
                "mode": field.mode,
                "description": field.description or "",
            }
            for field in table.schema
        ]
        return strip_pii_schema(schema)

    def run_query(self, sql: str) -> dict:
        """Validate, cost-check, execute, and PII-strip a read-only query.

        Order matters for cost: the read-only check runs first (free), then
        a dry run estimates cost before anything billable happens, then the
        real query only runs if both gates pass.

        Args:
            sql: A single read-only `SELECT`/`WITH` statement.

        Returns:
            A dict with `dataframe` (PII columns dropped), `row_count`,
            `bytes_processed` (from the dry-run estimate), and
            `redacted_columns` (names of any columns that were stripped).

        Raises:
            SQLSafetyError: `sql` fails the read-only allowlist.
            QueryTooExpensiveError: The dry-run byte estimate exceeds
                `max_bytes_billed`.
            QuerySyntaxError: The query is malformed or references a
                nonexistent table/column.
            QueryPermissionError: The service account lacks access.
            QueryTransientError: The BigQuery call failed transiently on
                every retry attempt.
        """
        check_read_only(sql)

        dry_run_config = bigquery.QueryJobConfig(
            dry_run=True, use_query_cache=False, default_dataset=self._default_dataset
        )
        dry_run_job = _call_bq(lambda: self._client.query(sql, job_config=dry_run_config), stage="dry_run")

        bytes_estimate = dry_run_job.total_bytes_processed or 0
        if bytes_estimate > self.max_bytes_billed:
            raise QueryTooExpensiveError(
                f"This query would scan about {bytes_estimate:,} bytes, over the "
                f"{self.max_bytes_billed:,} byte cap. Try narrowing the date range "
                "or the columns selected."
            )

        run_config = bigquery.QueryJobConfig(
            maximum_bytes_billed=self.max_bytes_billed, default_dataset=self._default_dataset
        )

        def _execute():
            query_job = self._client.query(sql, job_config=run_config)
            result = query_job.result(timeout=self.timeout_seconds, max_results=self.row_limit)
            return result.to_dataframe()

        df = _call_bq(_execute, stage="execute")

        clean_df, redacted_columns = strip_pii_columns(df)
        return {
            "dataframe": clean_df,
            "row_count": len(clean_df),
            "bytes_processed": bytes_estimate,
            "redacted_columns": redacted_columns,
        }
