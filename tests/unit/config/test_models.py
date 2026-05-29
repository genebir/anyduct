"""Config 모델 검증 테스트."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from etl_plugins.config.models import (
    ConnectionConfig,
    ConnectionsConfig,
    PipelineConfig,
    RetryConfig,
    SinkConfig,
    SourceConfig,
    TransformConfig,
)

# ---------- ConnectionConfig ----------


def test_connection_config_minimal() -> None:
    c = ConnectionConfig(type="postgres")
    assert c.type == "postgres"
    assert c.options() == {}


def test_connection_config_extra_allowed() -> None:
    # 커넥터별 임의 필드는 extra="allow"로 보존되어야 한다
    c = ConnectionConfig.model_validate(
        {
            "type": "postgres",
            "host": "db",
            "port": 5432,
            "options": {"pool_size": 10},
        }
    )
    opts = c.options()
    assert opts == {"host": "db", "port": 5432, "options": {"pool_size": 10}}
    assert "type" not in opts  # options()는 type 제외


def test_connection_config_requires_type() -> None:
    with pytest.raises(ValidationError, match="type"):
        ConnectionConfig.model_validate({"host": "db"})


# ---------- ConnectionsConfig ----------


def test_connections_config_empty() -> None:
    cc = ConnectionsConfig()
    assert cc.connections == {}


def test_connections_config_top_level_forbids_extra() -> None:
    with pytest.raises(ValidationError, match="extra"):
        ConnectionsConfig.model_validate({"connections": {}, "unexpected": 1})


def test_connections_config_full() -> None:
    cc = ConnectionsConfig.model_validate(
        {
            "connections": {
                "pg": {"type": "postgres", "host": "db"},
                "sf": {"type": "snowflake", "account": "acct"},
            }
        }
    )
    assert set(cc.connections.keys()) == {"pg", "sf"}
    assert cc.connections["pg"].type == "postgres"
    assert cc.connections["sf"].options()["account"] == "acct"


# ---------- SourceConfig / SinkConfig ----------


def test_source_config_minimum() -> None:
    s = SourceConfig(connection="pg")
    assert s.connection == "pg"
    assert s.query is None
    assert s.chunk_size == 10_000


def test_sink_config_defaults() -> None:
    s = SinkConfig(connection="sf")
    assert s.mode == "append"
    assert s.key_columns is None


def test_sink_extra_options_allowed() -> None:
    s = SinkConfig.model_validate(
        {"connection": "sf", "table": "T", "mode": "upsert", "buffer": {"max_records": 100}}
    )
    # extra=allow이므로 buffer가 통과
    assert s.model_dump().get("buffer") == {"max_records": 100}


def test_sink_auto_create_if_exists_accepts_canonical_values() -> None:
    """Phase AAG (2026-05-29): the Literal narrowing lets these
    canonical values through."""
    for v in ("skip", "drop", "error"):
        s = SinkConfig.model_validate(
            {"connection": "sf", "auto_create_table": True, "auto_create_if_exists": v}
        )
        assert s.auto_create_if_exists == v


def test_sink_auto_create_if_exists_rejects_typos() -> None:
    """Phase AAG: typos like ``"DROP"`` or ``"replace"`` used to slip
    through ``str`` and fail deep at runtime. Pydantic Literal
    validation now catches them at config-load time."""
    import pytest
    from pydantic import ValidationError

    for typo in ("DROP", "replace", "Skip"):
        with pytest.raises(ValidationError):
            SinkConfig.model_validate(
                {
                    "connection": "sf",
                    "auto_create_table": True,
                    "auto_create_if_exists": typo,
                }
            )


# ---------- PipelineConfig ----------


def test_pipeline_config_minimal() -> None:
    p = PipelineConfig.model_validate(
        {
            "name": "p1",
            "source": {"connection": "pg"},
            "sink": {"connection": "sf"},
        }
    )
    assert p.name == "p1"
    assert p.mode == "batch"
    assert p.transforms == []
    assert p.retry is None


def test_pipeline_config_full_batch() -> None:
    p = PipelineConfig.model_validate(
        {
            "name": "orders_to_dw",
            "schedule": "0 */1 * * *",
            "source": {"connection": "pg", "query": "SELECT *", "chunk_size": 5000},
            "transforms": [
                {"type": "rename", "mapping": {"a": "b"}},
                {"type": "python", "callable": "mod:fn"},
            ],
            "sink": {
                "connection": "sf",
                "table": "ORDERS",
                "mode": "upsert",
                "key_columns": ["id"],
            },
            "retry": {"max_attempts": 5, "backoff": "fixed"},
        }
    )
    assert p.schedule == "0 */1 * * *"
    assert p.source.chunk_size == 5000
    assert len(p.transforms) == 2
    assert p.transforms[0].type == "rename"
    assert p.sink.key_columns == ["id"]
    assert isinstance(p.retry, RetryConfig)
    assert p.retry.max_attempts == 5


def test_pipeline_config_forbids_top_level_extra() -> None:
    with pytest.raises(ValidationError, match="extra"):
        PipelineConfig.model_validate(
            {
                "name": "p",
                "source": {"connection": "pg"},
                "sink": {"connection": "sf"},
                "unknown": 1,
            }
        )


def test_pipeline_config_requires_name_source_sink() -> None:
    with pytest.raises(ValidationError):
        PipelineConfig.model_validate(
            {"source": {"connection": "pg"}, "sink": {"connection": "sf"}}
        )


def test_transform_type_required() -> None:
    with pytest.raises(ValidationError):
        TransformConfig.model_validate({"mapping": {"a": "b"}})
