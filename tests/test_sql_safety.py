import pytest

from retail_agent.errors import SQLSafetyError
from retail_agent.sql_safety import check_read_only


def test_allows_plain_select():
    check_read_only("SELECT id, sale_price FROM order_items")


def test_allows_with_cte():
    check_read_only("WITH t AS (SELECT 1 AS x) SELECT * FROM t")


def test_allows_select_star_from_non_users_table():
    check_read_only("SELECT * FROM order_items")


def test_keywords_inside_comments_do_not_trigger_false_positive():
    check_read_only("SELECT * FROM order_items -- please don't DELETE this data")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders WHERE id = 1",
        "UPDATE orders SET status = 'x'",
        "DROP TABLE orders",
        "INSERT INTO orders VALUES (1)",
        "TRUNCATE TABLE orders",
        "ALTER TABLE orders ADD COLUMN x STRING",
        "CREATE TABLE evil AS SELECT * FROM orders",
    ],
)
def test_rejects_mutating_statements(sql):
    with pytest.raises(SQLSafetyError):
        check_read_only(sql)


def test_rejects_multi_statement():
    with pytest.raises(SQLSafetyError):
        check_read_only("SELECT 1; DROP TABLE orders")


def test_rejects_non_select_leading_statement():
    with pytest.raises(SQLSafetyError):
        check_read_only("EXPLAIN SELECT * FROM orders")


def test_rejects_select_star_from_users():
    with pytest.raises(SQLSafetyError):
        check_read_only("SELECT * FROM users")


def test_rejects_qualified_select_star_from_users():
    with pytest.raises(SQLSafetyError):
        check_read_only("SELECT users.* FROM users")


def test_allows_explicit_non_pii_columns_from_users():
    check_read_only("SELECT id, state, created_at FROM users")


def test_empty_query_rejected():
    with pytest.raises(SQLSafetyError):
        check_read_only("   ")
