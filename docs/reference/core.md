# Core API

Auto-generated from docstrings via `mkdocstrings`. Source lives under
`etl_plugins/core/`.

## Pipeline + Task

::: etl_plugins.core.pipeline.Pipeline
    options:
      members:
        - add
        - "on"
        - run
        - arun_stream
        - to_runtime

::: etl_plugins.core.pipeline.Task
    options:
      members:
        - extract
        - transform
        - load

::: etl_plugins.core.pipeline.RunResult

## Records & schema

::: etl_plugins.core.record.Record

::: etl_plugins.core.schema.Schema

::: etl_plugins.core.schema.Field

## Connector ABCs

::: etl_plugins.core.connector.Connector

::: etl_plugins.core.connector.BatchSource

::: etl_plugins.core.connector.BatchSink

::: etl_plugins.core.connector.StreamSource

::: etl_plugins.core.connector.StreamSink

## Registry

::: etl_plugins.core.registry.ConnectorRegistry

## Cursors

::: etl_plugins.core.cursor.Cursor

::: etl_plugins.core.cursor.CursorState

::: etl_plugins.core.cursor.InMemoryCursorState

::: etl_plugins.core.cursor.FileCursorState

::: etl_plugins.core.cursor.max_cursor_value

## Context + hooks

::: etl_plugins.core.context.Context

::: etl_plugins.core.pipeline.Hook

::: etl_plugins.core.pipeline.TransformFn

## Exceptions

::: etl_plugins.core.exceptions
    options:
      members:
        - ETLError
        - ConnectorError
        - ConnectError
        - ReadError
        - WriteError
        - RegistryError
        - RecordError
        - SecretError
        - ConfigError
        - PipelineError
        - TaskError
        - TransformError
