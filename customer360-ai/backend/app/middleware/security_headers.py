"""Response security headers.

`docs/security.md` claimed these were in place while the app set none of them, so
this module is what makes the claim true. The set is deliberately small: each
header is one more way for the API to be embedded or sniffed, and headers nobody
reasoned about are headers nobody can defend.

Two are conditional:
- HSTS only in production. Sending it over plain HTTP in development is
  ignored by browsers at best, and it pins `localhost` to HTTPS at worst.
- CSP only on the docs routes, because Swagger UI loads its bundle from a CDN
  and inline bootstrap script. A global CSP would either break /docs or, if
  loosened to accommodate it, weaken every JSON response for nothing.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

# Swagger UI's assets come from jsDelivr, and its bootstrap is an inline script.
DOCS_CSP = (
    "default-src 'none'; "
    "script-src 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "font-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'"
)

BASE_HEADERS = {
    # Stops a browser second-guessing a response's declared type, which is the
    # primitive behind MIME-sniffing XSS.
    "X-Content-Type-Options": "nosniff",
    # The API has no legitimate framing use; DENY blocks clickjacking entirely.
    "X-Frame-Options": "DENY",
    # Paths in API responses are frequently customer identifiers; a referrer
    # leaking them to a third-party host is a quiet tenant-data leak.
    "Referrer-Policy": "no-referrer",
    # Deny the browser features this app never invokes, so injected markup
    # cannot reach for a camera or geolocation.
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    # Keeps a cross-origin opener from getting a handle on the window object,
    # which is the reverse-tabnabbing primitive.
    "Cross-Origin-Opener-Policy": "same-origin",
    # Sensitive responses must not sit in a shared cache after logout.
    "Cross-Origin-Resource-Policy": "same-origin",
}

HSTS_HEADER = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        if not settings.security_headers_enabled:
            return response

        for header, value in BASE_HEADERS.items():
            response.headers.setdefault(header, value)

        if settings.is_production:
            response.headers.setdefault("Strict-Transport-Security", HSTS_HEADER)
        if request.url.path.startswith(DOCS_PATHS):
            # setdefault: an inner layer that chose a stricter policy wins.
            response.headers.setdefault("Content-Security-Policy", DOCS_CSP)

        return response
