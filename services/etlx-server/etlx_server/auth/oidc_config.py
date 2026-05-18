"""OIDC provider configuration models (Step 8.2b, ADR-0023).

Lives in its own module so :mod:`etlx_server.settings` can depend on it
without pulling in the heavy ``OidcService`` implementation (and its
``httpx`` dependency) at import time.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from etlx_server.db.enums import AuthMethod


class OidcProviderConfig(BaseModel):
    """Configuration for a single OIDC identity provider.

    ``name`` is the lookup key the FE / API uses to choose a provider; it also
    determines which :class:`AuthMethod` newly-provisioned users get tagged
    with (see :meth:`auth_method`). Use one of the well-known values
    (``google``, ``azure``, ``okta``, ``github``) to land on a specific
    ``AuthMethod`` enum — anything else maps to ``oidc:generic``.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=32)
    display_name: str | None = Field(default=None, max_length=128)
    client_id: str = Field(min_length=1)
    client_secret: SecretStr
    discovery_url: str = Field(
        description="OpenID Connect discovery URL (``.well-known/openid-configuration``).",
    )
    redirect_uri: str = Field(
        description="Absolute callback URL registered with the provider — usually "
        "``{public_url}/auth/oidc/callback``.",
    )
    scopes: list[str] = Field(default_factory=lambda: ["openid", "email", "profile"])

    @property
    def auth_method(self) -> AuthMethod:
        """Map provider name → ``AuthMethod`` enum used in the ``users`` table."""
        return _PROVIDER_NAME_TO_AUTH_METHOD.get(self.name.lower(), AuthMethod.OIDC_GENERIC)


_PROVIDER_NAME_TO_AUTH_METHOD: dict[str, AuthMethod] = {
    "google": AuthMethod.OIDC_GOOGLE,
    "azure": AuthMethod.OIDC_AZURE,
    "okta": AuthMethod.OIDC_OKTA,
    "github": AuthMethod.OIDC_GITHUB,
}


__all__ = ["OidcProviderConfig"]
