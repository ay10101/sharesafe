from fastapi import Request
from app.main import app

@app.middleware("http")
async def vercel_path_normalizer(request: Request, call_next):
    path = request.scope.get("path", "")
    if path.startswith("/api/index.py"):
        path = path[len("/api/index.py"):] or "/"
        request.scope["path"] = path
    if not path.startswith("/api") and path not in ("/", "/docs", "/openapi.json", "/health", "/redoc"):
        request.scope["path"] = f"/api{path}"
    elif path.startswith("/api/api/"):
        request.scope["path"] = path[4:]
    return await call_next(request)
