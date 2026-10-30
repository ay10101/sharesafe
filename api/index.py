from starlette.requests import Request
from app.main import app

@app.middleware("http")
async def vercel_routing_middleware(request: Request, call_next):
    scope = request.scope
    path = scope.get("path", "")

    # If Vercel rewrote request to /api/index.py
    if path == "/api/index.py" or path.startswith("/api/index.py/"):
        orig = (
            request.headers.get("x-matched-path")
            or request.headers.get("x-forwarded-uri")
            or request.headers.get("x-vercel-matched-path")
        )
        if orig and not orig.startswith("/api/index.py"):
            path = orig
        else:
            sub = path[len("/api/index.py"):]
            path = sub if sub else "/"
        scope["path"] = path

    # If Vercel stripped /api prefix (e.g. /users instead of /api/users)
    if not path.startswith("/api") and path not in ("/", "/docs", "/openapi.json", "/health", "/redoc", "/favicon.ico"):
        scope["path"] = f"/api{path}"

    return await call_next(request)
