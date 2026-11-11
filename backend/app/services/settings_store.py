"""Platform settings stored in the database, cached briefly so every worker picks up
changes within CACHE_SECONDS without a restart."""
import time

from ..db import SessionLocal
from ..models import SystemSetting
from ..security import decrypt_secret, encrypt_secret

CACHE_SECONDS = 30
SECRET_KEYS = {"deepseek_api_key", "ali1688_ak"}
DEFAULTS = {
    "deepseek_api_key": "",
    "deepseek_base_url": "https://api.deepseek.com",
    "deepseek_model": "deepseek-flash",
    "ali1688_ak": "",
    "ali1688_channel": "github",
}
_cache: dict[str, str] = {}
_loaded_at = 0.0


def _load() -> None:
    global _loaded_at
    with SessionLocal() as db:
        rows = {r.key: r.value for r in db.query(SystemSetting).all()}
    fresh = dict(DEFAULTS)
    for k, v in rows.items():
        if k in SECRET_KEYS and v:
            try:
                v = decrypt_secret(v)
            except Exception:
                v = ""
        fresh[k] = v
    _cache.clear()
    _cache.update(fresh)
    _loaded_at = time.time()


def get(key: str) -> str:
    if time.time() - _loaded_at > CACHE_SECONDS or key not in _cache:
        _load()
    return _cache.get(key, DEFAULTS.get(key, ""))


def set_many(values: dict[str, str]) -> None:
    global _loaded_at
    with SessionLocal() as db:
        for k, v in values.items():
            stored = encrypt_secret(v) if (k in SECRET_KEYS and v) else v
            row = db.get(SystemSetting, k)
            if row:
                row.value = stored
            else:
                db.add(SystemSetting(key=k, value=stored))
        db.commit()
    _loaded_at = 0.0  # reload on next read in this process


def mask(secret: str) -> str:
    if not secret:
        return ""
    return secret[:3] + "****" + secret[-4:] if len(secret) > 8 else "****"
