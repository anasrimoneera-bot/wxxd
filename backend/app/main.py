import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import engine
from .migrate import upgrade
from .routers import admin, auth, library, products, stores, tasks, templates

logging.basicConfig(level=logging.INFO)



@asynccontextmanager
async def lifespan(_app):
    s = get_settings()
    if not s.database_url.startswith("sqlite") and (
            s.jwt_secret == "change-me" or s.secret_key_b64.startswith("MDEyMzQ1Njc4")):
        raise RuntimeError("JWT_SECRET and SECRET_KEY_B64 must be set in .env before running in production")
    upgrade(engine)
    yield


app = FastAPI(title="微信小店助手 API", version="0.1.0", lifespan=lifespan)
for r in (admin.router, products.router, auth.router, auth.sub_router, stores.router, library.router, templates.router, tasks.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/meta")
def meta():
    return {"server_public_ip": get_settings().server_public_ip}


# Serve the built frontend when present (production image copies it to ./static).
_static = Path(__file__).resolve().parent.parent / "static"
if _static.exists():
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = _static / path
        return FileResponse(f if path and f.is_file() else _static / "index.html")
