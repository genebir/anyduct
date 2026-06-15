"""Run read-only package (Step 8.5e).

Runs are written by the worker engine (Step 9). The API surface here is
intentionally read-only — the UI lists, filters, drills into runs, and
streams logs, but never mutates them.
"""
