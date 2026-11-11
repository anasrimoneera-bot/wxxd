"""找货源 S1: search 1688 by product image; goods price only (shipping excluded, see docs/SOURCING.md)."""
import base64
import hashlib
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse

from ..db import SessionLocal
from ..models import SourcingCache, SourcingJob, SourcingQuery
from . import settings_store
from .ali1688 import Ali1688Client, Ali1688Error
from .images import normalize_for_upload
from .wechat import download

log = logging.getLogger(__name__)
CACHE_DAYS = 7
PAGE_SIZE = 20
SIMILARITY_FLOOR = 0.8  # 最优 only among offers at least 80% as similar as the best match

# Replaced in tests
client_factory = Ali1688Client
downloader = download


def get_client():
    ak = settings_store.get("ali1688_ak")
    if not ak:
        raise Ali1688Error("未设置 1688 AK：请管理员在「系统设置 → 1688 货源」中填写")
    return client_factory(ak, settings_store.get("ali1688_channel"))


def _cache_key(image_url: str, quantity: int, sort_type: str) -> str:
    return hashlib.sha256(f"{image_url}|{quantity}|{sort_type}".encode()).hexdigest()


def pick_best(offers: list[dict], quantity: int) -> str:
    """Cheapest offer among the closest matches whose MOQ allows the quantity."""
    if not offers:
        return ""
    top = max(o["score"] for o in offers) or 1
    ok = [o for o in offers if o["price"] is not None and o["score"] >= top * SIMILARITY_FLOOR
          and (not o["moq"] or o["moq"] <= quantity)]
    return min(ok, key=lambda o: (o["price"], -o["sold"]))["offer_id"] if ok else ""


def search_image(client, image_url: str, quantity: int, sort_type: str) -> list[dict]:
    host = urlparse(image_url).hostname or ""
    if host.endswith("alicdn.com"):  # 1688's own images: let the gateway fetch them
        return client.image_search(image_url=image_url, page_size=PAGE_SIZE, purchase_amount=quantity,
                                   sort_type=sort_type or None)
    # Other hosts (e.g. WeChat CDN): fetch here and send the image itself
    data = normalize_for_upload(downloader(image_url))
    return client.image_search(image_base64=base64.b64encode(data).decode(), page_size=PAGE_SIZE,
                               purchase_amount=quantity, sort_type=sort_type or None)


def run_job(job_id: int) -> None:
    with SessionLocal() as db:
        job = db.get(SourcingJob, job_id)
        job.status = "running"
        db.commit()
        try:
            client = get_client()
        except Exception as e:
            client, setup_error = None, str(e)
        queries = db.query(SourcingQuery).filter(SourcingQuery.job_id == job_id).order_by(SourcingQuery.id).all()
        for q in queries:
            try:
                if client is None:
                    raise Ali1688Error(setup_error)
                key = _cache_key(q.image_url, job.quantity, job.sort_type)
                cached = db.get(SourcingCache, key)
                if cached and cached.expires_at > datetime.now():
                    offers = cached.payload
                else:
                    offers = search_image(client, q.image_url, job.quantity, job.sort_type)
                    if cached:
                        cached.payload, cached.expires_at = offers, datetime.now() + timedelta(days=CACHE_DAYS)
                    else:
                        db.add(SourcingCache(key=key, payload=offers,
                                             expires_at=datetime.now() + timedelta(days=CACHE_DAYS)))
                # Owner rule: only show sources that have actually sold (已售 > 0).
                # The cache keeps everything, so this rule can change without new 1688 calls.
                shown = [o for o in offers if (o.get("sold") or 0) > 0]
                q.hidden_count = len(offers) - len(shown)
                q.offers, q.best_offer_id, q.status = shown, pick_best(shown, job.quantity), "done"
                if not offers:
                    q.error = "1688 未找到相似商品"
                elif not shown:
                    q.error = f"找到 {len(offers)} 个相似货源，但都没有销量，已全部隐藏"
            except Exception as e:
                log.warning("sourcing query %s failed: %s", q.id, e)
                q.status, q.error = "failed", (str(e) or e.__class__.__name__)[:500]
            job.done += 1
            db.commit()
        job.status = "done"
        db.commit()
