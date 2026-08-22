from google.cloud import bigquery

from retail_agent.errors import QueryExecutionError, QueryTooExpensiveError
from retail_agent.pii import strip_pii_columns, strip_pii_schema
from retail_agent.sql_safety import check_read_only

DEFAULT_DATASET = "bigquery-public-data.thelook_ecommerce"


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
        table = self._client.get_table(f"{self._default_dataset}.{table_name}")
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
