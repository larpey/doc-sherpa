"""Bearer-token auth middleware.

When `settings.auth_token` is set, every request except a small allowlist
must carry `Authorization: Bearer <token>` matching that value. When
unset, the middleware is a no-op (LAN-only / dev mode).

Why not HTTPBearer / FastAPI Security: those force a per-route opt-in.
Auth here is a *blanket* property of the deployment; middleware is the
right layer.
"""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from collections.abc import Callable

# Paths that are *always* open — k8s liveness probes need to work without
# tokens. The setup wizard is open ONLY when first-run setup has not yet
# completed (so the operator can fill it in before they have a token);
# once `is_wizard_complete()` returns True, /setup requires auth like
# any other state-changing endpoint.
_ALWAYS_OPEN_PATHS: frozenset[str] = frozenset({"/healthz"})
_FIRST_RUN_OPEN_PATHS: frozenset[str] = frozenset({"/setup"})


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Reject requests missing/wrong Authorization header.

    Uses `hmac.compare_digest` to prevent timing-attack token guessing.

    `is_wizard_complete` is a zero-arg callable that lets the middleware
    decide whether to open the `/setup` path. It's a callback rather
    than a flag because wizard state changes at runtime.
    """

    def __init__(
        self,
        app,
        *,
        expected_token: str | None,
        is_wizard_complete: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(app)
        self._expected = expected_token
        self._wizard_complete = is_wizard_complete or (lambda: False)

    async def dispatch(self, request: Request, call_next) -> Response:
        if self._expected is None:
            return await call_next(request)

        path = request.url.path
        if path in _ALWAYS_OPEN_PATHS:
            return await call_next(request)
        if path in _FIRST_RUN_OPEN_PATHS and not self._wizard_complete():
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return JSONResponse({"detail": "auth required"}, status_code=401)
        provided = header[len("bearer "):].strip()
        if not hmac.compare_digest(provided, self._expected):
            return JSONResponse({"detail": "invalid token"}, status_code=401)

        return await call_next(request)
