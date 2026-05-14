"""Standard test record datasets. Every connector contract test uses these.

Keep the shapes stable — any change to ``sample_records`` flows through every
contract test, so additive changes only.
"""

from __future__ import annotations

from datetime import UTC, datetime

from etl_plugins import Record


def sample_records() -> list[Record]:
    """Small (3 rows) basic dataset. The default for contract tests."""
    return [
        Record(data={"id": 1, "name": "Alice", "age": 30, "active": True}),
        Record(data={"id": 2, "name": "Bob", "age": 25, "active": False}),
        Record(data={"id": 3, "name": "Carol", "age": 35, "active": True}),
    ]


def large_records(n: int = 1000) -> list[Record]:
    """Synthetic dataset for chunking / scaling tests."""
    return [Record(data={"id": i, "value": f"v{i}"}) for i in range(n)]


def mixed_types_records() -> list[Record]:
    """Records covering int / float / str / bool / None / datetime / nested."""
    return [
        Record(
            data={
                "int_pos": 42,
                "int_neg": -7,
                "int_zero": 0,
                "float_val": 3.14,
                "str_normal": "hello",
                "str_empty": "",
                "str_unicode": "한글 ETL",
                "bool_true": True,
                "bool_false": False,
                "none_val": None,
                "ts": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
                "nested": {"a": 1, "b": [1, 2, 3]},
            }
        ),
        Record(
            data={
                "int_pos": 1,
                "int_neg": -100,
                "int_zero": 0,
                "float_val": -0.0,
                "str_normal": "world",
                "str_empty": "",
                "str_unicode": "",
                "bool_true": False,
                "bool_false": True,
                "none_val": None,
                "ts": datetime(1970, 1, 1, tzinfo=UTC),
                "nested": {},
            }
        ),
    ]
