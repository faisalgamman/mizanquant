"""Regression tests for raw DB migration SQL across dialects."""

from __future__ import annotations

from app.db.database import _datetime_sql_type


def test_datetime_sql_type_for_sqlite():
    assert _datetime_sql_type("sqlite") == "DATETIME"


def test_datetime_sql_type_for_postgresql():
    assert _datetime_sql_type("postgresql") == "TIMESTAMP WITH TIME ZONE"
