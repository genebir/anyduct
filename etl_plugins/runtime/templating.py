"""Back-compat shim — runtime templating moved to ``etl_plugins.core.templating``.

The ``{{ }}`` renderer is a pure, core-level concern (ADR-0097): the core
``Pipeline`` itself now renders deferred ``{{ xcom.* }}`` references per task
at execution time, and the import contract forbids ``core`` importing
``runtime``. The implementation therefore lives in
:mod:`etl_plugins.core.templating`; this module re-exports it so existing
``etl_plugins.runtime.templating`` imports keep working.
"""

from __future__ import annotations

from etl_plugins.core.templating import (
    DEFERRED_NAMESPACES,
    TEMPLATE_REF,
    RuntimeContext,
    has_template,
    references_namespace,
    render_config_templates,
    render_templates,
    template_namespaces,
    template_paths,
)

__all__ = [
    "DEFERRED_NAMESPACES",
    "TEMPLATE_REF",
    "RuntimeContext",
    "has_template",
    "references_namespace",
    "render_config_templates",
    "render_templates",
    "template_namespaces",
    "template_paths",
]
