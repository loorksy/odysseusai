"""Route registration for the /status UI page.

Keeps app.py clean by grouping the status page handler here.
Mount by calling: register_status_route(app)
"""
from fastapi import FastAPI, Request
from src.app_helpers import serve_html_with_nonce


def register_status_route(app: FastAPI) -> None:
    """Register GET /status with the given FastAPI app."""

    @app.get("/status", include_in_schema=False)
    async def status_page(request: Request):
        """Serve the ShadowRealm system status page.

        Auth is enforced by AuthMiddleware (same as all other UI pages).
        The page fetches /api/diagnostics/services client-side; no data
        is embedded server-side, so no extra permission checks needed here.
        """
        return serve_html_with_nonce(request, "status.html")
