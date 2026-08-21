from google.cloud import bigquery

from provided.bq_runner import BigQueryRunner
from retail_agent.errors import QueryExecutionError, QueryTooExpensiveError
from retail_agent.pii import strip_pii_columns, strip_pii_schema
from retail_agent.sql_safety import check_read_only


class BigQueryTool:
    """Safety/cost/PII wrapper around the company-supplied BigQueryRunner.

    bq_runner.execute_query() has no hooks for job config (dry-run, cost cap,
    timeout, row limit), so real execution goes through runner.client
    directly instead of through execute_query() — bq_runner.py itself stays
    unmodified per constraint, this class just doesn't call that one method.
    """

    def __init__(
        self,
        runner: BigQueryRunner,
        max_bytes_billed: int,
        row_limit: int,
        timeout_seconds: float,
    ) -> None:
        self._runner = runner
        self._client = runner.client
        # bq_runner.py accepts dataset_id but never wires it into query
        # execution, so unqualified table names ("users") would otherwise
        # fail with "must be qualified with a dataset" — set it ourselves.
        self._default_dataset = runner.dataset_id
        self.max_bytes_billed = max_bytes_billed
        self.row_limit = row_limit
        self.timeout_seconds = timeout_seconds

    def get_schema(self, table_name: str) -> list[dict]:
        schema = self._runner.get_table_schema(table_name)
        return strip_pii_schema(schema)

    def run_query(self, sql: str) -> dict:
        check_read_only(sql)

        dry_run_config = bigquery.QueryJobConfig(
            dry_run=True, use_query_cache=False, default_dataset=self._default_dataset
        )
        try:
            dry_run_job = self._client.query(sql, job_config=dry_run_config)
        except Exception as exc:
            raise QueryExecutionError(str(exc)) from exc

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
        try:
            query_job = self._client.query(sql, job_config=run_config)
            result = query_job.result(timeout=self.timeout_seconds, max_results=self.row_limit)
            df = result.to_dataframe()
        except Exception as exc:
            raise QueryExecutionError(str(exc)) from exc

        clean_df, redacted_columns = strip_pii_columns(df)
        return {
            "dataframe": clean_df,
            "row_count": len(clean_df),
            "bytes_processed": bytes_estimate,
            "redacted_columns": redacted_columns,
        }
