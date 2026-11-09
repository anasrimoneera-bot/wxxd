"""Publish worker. Run several copies (worker-001, worker-002, ...): each claims one
pending task item at a time, so tasks are spread across workers product by product.

    python -m app.worker --name worker-001
"""
import argparse
import logging
import os
import socket
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select, update

from .config import get_settings
from .db import SessionLocal
from .models import LibraryProduct, PublishedRecord, PublishTask, PublishTaskItem, Store
from .security import decrypt_secret
from .services.publisher import PublishFailed, Skipped, StoreContext, publish, title_hash
from .services.template_config import TemplateConfig
from .services.wechat import WeChatClient, explain

log = logging.getLogger("worker")
STALE_AFTER = timedelta(minutes=10)


class Worker:
    def __init__(self, name: str, client_factory=WeChatClient):
        self.name = name
        self.client_factory = client_factory
        self.contexts: dict[int, StoreContext] = {}

    def context(self, store: Store) -> StoreContext:
        ctx = self.contexts.get(store.id)
        if ctx is None:
            client = self.client_factory(store.appid, decrypt_secret(store.secret_enc))
            ctx = StoreContext(client=client, store_name=store.name,
                               freight_template_id=store.freight_template_id, address_id=store.address_id)
            self.contexts[store.id] = ctx
        ctx.freight_template_id, ctx.address_id = store.freight_template_id, store.address_id
        return ctx

    def claim(self):
        with SessionLocal() as db, db.begin():
            item = db.execute(
                select(PublishTaskItem).where(PublishTaskItem.status == "pending")
                .order_by(PublishTaskItem.id).limit(1).with_for_update(skip_locked=True)
            ).scalar_one_or_none()
            if not item:
                return None
            item.status, item.worker, item.started_at = "running", self.name, datetime.now()
            task = db.get(PublishTask, item.task_id)
            if task.status == "pending":
                task.status, task.started_at, task.worker = "running", datetime.now(), self.name
            return item.id

    def requeue_stale(self):
        with SessionLocal() as db, db.begin():
            db.execute(update(PublishTaskItem)
                       .where(PublishTaskItem.status == "running",
                              PublishTaskItem.started_at < datetime.now() - STALE_AFTER)
                       .values(status="pending", worker=""))

    def process(self, item_id: int):
        with SessionLocal() as db:
            item = db.get(PublishTaskItem, item_id)
            task = db.get(PublishTask, item.task_id)
            store = db.get(Store, task.store_id)
            product = db.get(LibraryProduct, item.library_product_id)
            if store is None or product is None:
                self._finish(db, item, task, "failed", "店铺已删除" if store is None else "云库商品已删除")
                return
            cfg = TemplateConfig.model_validate(task.template_snapshot)
            key = f"{product.platform}:{product.source_product_id}"

            def is_duplicate(norm):
                return db.execute(select(PublishedRecord.id).where(
                    PublishedRecord.store_id == store.id,
                    (PublishedRecord.source_key == key) | (PublishedRecord.title_hash == title_hash(norm["title"])),
                ).limit(1)).first() is not None

            status, counter = "failed", "failed"
            try:
                res = publish(product.normalized, cfg, self.context(store), is_duplicate)
                status, counter = "success", "success"
                item.wx_product_id, item.wx_category, item.message = res.wx_product_id, res.wx_category, res.message
                db.add(PublishedRecord(store_id=store.id, source_key=key,
                                       title_hash=title_hash(product.normalized["title"]),
                                       wx_product_id=res.wx_product_id))
            except Skipped as e:
                status, item.message = "skipped", str(e)
            except PublishFailed as e:
                item.message = str(e)
            except Exception as e:  # unexpected: keep the reason visible
                log.exception("item %s failed", item_id)
                item.message = f"系统错误：{explain(e)}"[:1000]
            self._finish(db, item, task, status, item.message, counter)

    def _finish(self, db, item, task, status, message, counter="failed"):
        item.status, item.finished_at, item.message = status, datetime.now(), (message or "")[:1000]
        db.execute(update(PublishTask).where(PublishTask.id == task.id)
                   .values({counter: getattr(PublishTask, counter) + 1}))
        db.commit()
        self.finish_if_done(db, task.id)

    @staticmethod
    def finish_if_done(db, task_id: int):
        left = db.scalar(select(func.count()).select_from(PublishTaskItem).where(
            PublishTaskItem.task_id == task_id, PublishTaskItem.status.in_(("pending", "running"))))
        if left:
            return
        task = db.get(PublishTask, task_id)
        db.refresh(task)
        if task.finished_at:
            return
        task.status = "success" if task.failed == 0 else ("failed" if task.success == 0 else "partial")
        task.finished_at = datetime.now()
        db.commit()

    def run_forever(self):
        s = get_settings()
        log.info("%s started", self.name)
        last_requeue = 0.0
        while True:
            if time.time() - last_requeue > 60:
                self.requeue_stale()
                last_requeue = time.time()
            item_id = self.claim()
            if item_id is None:
                time.sleep(s.worker_poll_seconds)
                continue
            self.process(item_id)
            time.sleep(s.worker_item_interval_seconds)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--name", default=os.environ.get("WORKER_NAME") or socket.gethostname())
    Worker(p.parse_args().name).run_forever()


if __name__ == "__main__":
    main()
