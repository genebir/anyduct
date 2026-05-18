"""Pydantic request/response models for the auth router."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=512)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenPair(BaseModel):
    """OAuth2-shaped response. ``token_type`` is always ``bearer``."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    """Lifetime of ``access_token`` in seconds."""


class CurrentUser(BaseModel):
    """Minimal user identity injected into protected endpoints."""

    id: UUID
    email: EmailStr
    name: str
    is_superadmin: bool


class OidcProviderSummary(BaseModel):
    """Public-safe provider info — never leaks ``client_secret``."""

    name: str
    display_name: str | None = None


class OidcAuthorizeResponse(BaseModel):
    """Returned from ``GET /auth/oidc/login`` — the FE redirects the browser
    to ``authorize_url`` and stores nothing (state is embedded in the URL)."""

    authorize_url: str
    state: str


class OidcCallbackResponse(TokenPair):
    """Same shape as ``TokenPair`` plus the original ``return_to`` so the FE
    can redirect to the page the user clicked from."""

    return_to: str | None = None


_SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
_COLOR_PATTERN = r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$"


class WorkspaceSummary(BaseModel):
    """Compact workspace identity used by RBAC-aware endpoints."""

    id: UUID
    name: str
    slug: str
    color_hex: str
    role: str | None = None
    """Caller's role in this workspace; ``None`` means SuperAdmin bypass."""


class WorkspaceCreateRequest(BaseModel):
    """Body of ``POST /workspaces``."""

    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=64, pattern=_SLUG_PATTERN)
    color_hex: str = Field(default="#FF3D8B", pattern=_COLOR_PATTERN)


class WorkspaceUpdateRequest(BaseModel):
    """PATCH-style body of ``PATCH /workspaces/{id}`` — all fields optional."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=64, pattern=_SLUG_PATTERN)
    color_hex: str | None = Field(default=None, pattern=_COLOR_PATTERN)

    def as_field_dict(self) -> dict[str, Any]:
        """Return only the fields the caller actually set (``model_dump(exclude_unset=True)``)."""
        return self.model_dump(exclude_unset=True)


class AuditLogEntry(BaseModel):
    """One row of the ``audit_log`` table, shaped for the ``/audit`` response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_user_id: UUID | None
    workspace_id: UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    before_json: dict[str, Any] | None
    after_json: dict[str, Any] | None
    ip: str | None
    user_agent: str | None
    created_at: datetime


__all__ = [
    "AuditLogEntry",
    "CurrentUser",
    "LoginRequest",
    "OidcAuthorizeResponse",
    "OidcCallbackResponse",
    "OidcProviderSummary",
    "RefreshRequest",
    "TokenPair",
    "WorkspaceCreateRequest",
    "WorkspaceSummary",
    "WorkspaceUpdateRequest",
]
