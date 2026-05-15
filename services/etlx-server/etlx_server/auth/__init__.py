"""Authentication subpackage (Step 8.2). ADR-0023.

Layered as service classes (``JwtService``, ``PasswordService``) +
a repository (``UserRepository``) + a router (``etlx_server.routers.auth``).
Services are constructed once at app startup and shared via
``app.state``; endpoints reach them through ``Depends(...)`` helpers.
"""
