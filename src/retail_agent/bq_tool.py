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
    try:
        return fn()
    except QueryTransientError:
        raise
    except Exception as exc:
        typed = _classify(exc)
        logger.warning("bq_call_failed", extra={"stage": stage, "error_class": type(typed).__name__})
        raise typed from exc


class BigQueryTool:
    """Safety/cost/PII wrapper around a bigquery.Client."""

    def __init__(
        self,
        client: bigquery.Client,
        max_bytes_billed: int,
        row_limit: int,
        timeout_seconds: float,
        default_dataset: str = DEFAULT_DATASET,
    ) -> None:
        self._client = client
        self._default_dataset = default_dataset
        self.max_bytes_billed = max_bytes_billed
        self.row_limit = row_limit
        self.timeout_seconds = timeout_seconds

    def get_schema(self, table_name: str) -> list[dict]:
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
