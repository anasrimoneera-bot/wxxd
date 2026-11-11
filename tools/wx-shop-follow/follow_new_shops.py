"""微信小店：自动收藏「新店开张」的小店。

Drives the scrcpy window on Windows like a person would: reads the phone screen from the window
(OCR, offline), clicks with the mouse, scrolls with the mouse wheel. Nothing is sent to the phone
except what scrcpy itself forwards for those clicks.

Two modes:
  keywords (keywords.txt): type each keyword into the home search box and work through the results
      until --per-keyword new shops (default 30) are favourited, then the next keyword; stops when
      every keyword has reached it. Progress is kept in the log, so a restart continues.
  home feed (no keywords): work through the 推荐 list until --max shops are checked.

Per product (in both modes):
  list -> open the next product card (直播中 / 收藏的店 cards skipped)
  -> product page: click the shop name at the top
  -> shop page: 新店开张 + 收藏小店 -> click 收藏小店; 已收藏 or not new -> skip
  -> click the back arrow (top-left) until the list is back -> next card; wheel-scroll for more.

Usage:  python follow_new_shops.py --probe        (check that the window is found and read)
        python follow_new_shops.py --dry-run      (everything except the 收藏 click)
        python follow_new_shops.py                (keywords from keywords.txt, 30 new shops each)
        python follow_new_shops.py --home         (home feed instead of keywords, --max 30)
Stop:   Ctrl+C, or move the mouse into a screen corner (pyautogui fail-safe).
"""
from __future__ import annotations

import argparse
import csv
import difflib
import os
import random
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime


class Abort(Exception):
    """Stop the run with a message for the user."""


class Stopped(Exception):
    """The user pressed 停止 (or Ctrl+C)."""


STOP = threading.Event()  # set by the desktop app's 停止 button
say = print  # the desktop app replaces this to show messages in its log


def sleep(t: float):
    if STOP.wait(max(t, 0)):
        raise Stopped()


def set_dpi_aware():
    """Windows display scaling (125%, 150%...): use real pixels everywhere, so window positions,
    screenshots and mouse clicks agree."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# ---------------------------------------------------------------- screen reading (pure logic)


@dataclass
class Box:
    text: str
    x: float  # centre, as a fraction of the phone screen width
    y: float  # centre, as a fraction of the phone screen height


def find(boxes: list[Box], *words: str, y_min: float = 0.0, y_max: float = 1.0) -> Box | None:
    for b in boxes:
        if y_min <= b.y <= y_max and any(w in b.text for w in words):
            return b
    return None


BLOCK_WORDS = ("操作频繁", "请完成验证", "安全验证", "拖动滑块", "访问过于频繁", "稍后再试")
LIVE_WORDS = ("直播中", "人在看", "人观看", "正在直播", "说点什么", "关注主播")
TIME_RE = re.compile(r"^\d{1,2}[:：]\d{2}")


def is_home(boxes) -> bool:
    return find(boxes, "小店与卡包", y_max=0.2) is not None


def is_product(boxes) -> bool:
    return find(boxes, "购买", "送朋友", y_min=0.45) is not None


def is_live(boxes) -> bool:
    return find(boxes, *LIVE_WORDS) is not None


def is_shop(boxes) -> bool:
    return (find(boxes, "收藏小店", "已收藏", y_max=0.5) is not None
            and find(boxes, "客服", y_max=0.5) is not None)


def is_new_store(boxes) -> bool:
    return find(boxes, "新店开张", y_max=0.4) is not None


def blocked(boxes) -> Box | None:
    return find(boxes, *BLOCK_WORDS)


def shop_name_on_product(boxes) -> Box | None:
    """The shop name next to the avatar in the top bar of a product page."""
    cands = [b for b in boxes if 0.04 < b.y < 0.16 and 0.08 < b.x < 0.75 and len(b.text) >= 2
             and not TIME_RE.match(b.text)]
    return min(cands, key=lambda b: (b.y, b.x)) if cands else None


def norm(t: str) -> str:
    return re.sub(r"\W", "", t)


def shows_keyword(boxes, kw: str, y_max: float = 0.16) -> bool:
    """The keyword in the search bar at the top (OCR may miss a character)."""
    k = norm(kw)
    for b in boxes:
        t = norm(b.text)
        if b.y <= y_max and k and t and (k in t or difflib.SequenceMatcher(None, k, t).ratio() >= 0.7):
            return True
    return False


def is_results(boxes, kw: str) -> bool:
    """Search results for kw: the keyword at the top and product prices below (not the input page)."""
    return (shows_keyword(boxes, kw) and not is_home(boxes) and not is_shop(boxes) and not is_product(boxes)
            and any(("¥" in b.text or "￥" in b.text) and b.y > 0.2 for b in boxes))


def home_search_box(boxes) -> Box | None:
    """The search bar on the home screen: the row of its 搜索 button."""
    return find(boxes, "搜索", y_max=0.25)


@dataclass
class Card:
    title: Box
    live: bool
    favorited: bool = False

    @property
    def key(self) -> str:
        return re.sub(r"\W", "", self.title.text)[:12]


def home_cards(boxes) -> list[Card]:
    """Product cards on the home list, top to bottom. A card is found by its price line (¥);
    the title is the first text line above it in the same column."""
    tabs = find(boxes, "推荐", "购物直播", y_max=0.5)
    top = (tabs.y + 0.03) if tabs else 0.12
    cards = []
    for p in boxes:
        if not ("¥" in p.text or "￥" in p.text) or p.y < top:
            continue
        col = p.x < 0.5
        above = [b for b in boxes if (b.x < 0.5) == col and p.y - 0.09 < b.y < p.y - 0.005
                 and "¥" not in b.text and "￥" not in b.text and len(b.text) >= 4
                 and not re.fullmatch(r"[\d.]+", b.text)]
        if not above:
            continue
        title = min(above, key=lambda b: b.y)
        if not (top < title.y < 0.92):
            continue
        # The card spans from the previous card's price line in this column (or the list top) down to
        # its own price. The orange 直播中 badge sits in the top-left of the image, far above the title.
        prev = [b.y for b in boxes if (b.x < 0.5) == col and ("¥" in b.text or "￥" in b.text) and b.y < title.y]
        start = max(prev) if prev else top  # never above the tab bar (its 购物直播 tab is not a badge)
        inside = [b for b in boxes if (b.x < 0.5) == col and start < b.y <= p.y + 0.03]
        live = any(any(w in b.text for w in ("直播",) + LIVE_WORDS) for b in inside)
        favorited = any("收藏的店" in b.text for b in inside)  # 11.11 收藏的店: shop already in favourites
        cards.append(Card(title, live, favorited))
    return sorted(cards, key=lambda c: (round(c.title.y, 2), c.title.x))


# ---------------------------------------------------------------- OCR

_engine = None


def ocr(img) -> list[Box]:
    """img: PIL image of the phone screen."""
    global _engine
    import numpy as np
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    w, h = img.size
    res, _ = _engine(np.array(img.convert("RGB"))[:, :, ::-1].copy())
    out = []
    for box, text, _score in res or []:
        out.append(Box(str(text).strip(), sum(p[0] for p in box) / 4 / w, sum(p[1] for p in box) / 4 / h))
    return out


# ---------------------------------------------------------------- the scrcpy window (Windows)

BACK_ARROW = (0.065, 0.071)  # top-left back arrow, as fractions of the phone screen (see --probe)


class ScrcpyWindow:
    def __init__(self, title: str):
        set_dpi_aware()
        import win32gui
        self.w32 = win32gui
        found = []

        def cb(h, _):
            if win32gui.IsWindowVisible(h) and title.lower() in win32gui.GetWindowText(h).lower():
                found.append(h)
        win32gui.EnumWindows(cb, None)
        if not found:
            raise Abort(f"找不到 scrcpy 窗口（标题包含「{title}」）。请先打开 scrcpy，或用 --window 指定窗口标题。")
        self.hwnd = found[0]
        import pyautogui
        pyautogui.FAILSAFE = True  # mouse into a screen corner = stop
        self.gui = pyautogui

    def rect(self):
        l, t, r, b = self.w32.GetClientRect(self.hwnd)
        x, y = self.w32.ClientToScreen(self.hwnd, (0, 0))
        return x, y, r - l, b - t

    def focus(self):
        try:
            self.w32.SetForegroundWindow(self.hwnd)
        except Exception:
            pass

    def grab(self):
        import mss
        from PIL import Image
        x, y, w, h = self.rect()
        with mss.mss() as s:
            shot = s.grab({"left": x, "top": y, "width": w, "height": h})
        return Image.frombytes("RGB", shot.size, shot.rgb)

    def _point(self, fx, fy, jitter=0.008):
        x, y, w, h = self.rect()
        fx += random.uniform(-jitter, jitter)
        fy += random.uniform(-jitter, jitter) * w / h
        return x + fx * w, y + fy * h

    def click(self, fx, fy, jitter=0.008):
        sx, sy = self._point(fx, fy, jitter)
        self.gui.moveTo(sx, sy, duration=random.uniform(0.15, 0.45))
        sleep(random.uniform(0.05, 0.15))
        self.gui.click()

    def right_click(self):  # scrcpy: right click = Android BACK
        sx, sy = self._point(0.5, 0.5)
        self.gui.moveTo(sx, sy, duration=random.uniform(0.15, 0.4))
        self.gui.click(button="right")

    def paste_text(self, text: str):
        """Chinese cannot be typed as key presses: put it on the PC clipboard and press Ctrl+V;
        scrcpy copies the clipboard to the phone and pastes it."""
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        self.focus()
        sleep(0.3)
        self.gui.hotkey("ctrl", "v")

    def enter(self):
        self.gui.press("enter")

    def wheel_down(self, notches: int):
        sx, sy = self._point(0.5, 0.65, 0.03)
        self.gui.moveTo(sx, sy, duration=random.uniform(0.2, 0.5))
        for _ in range(notches):
            self.gui.scroll(-1)
            sleep(random.uniform(0.05, 0.15))


# ---------------------------------------------------------------- the workflow

def pause(a, b):
    sleep(random.uniform(a, b))


class Bot:
    LOG_HEADER = ["time", "keyword", "shop", "result", "product"]

    def __init__(self, win: ScrcpyWindow, args):
        self.win, self.args = win, args
        self.seen: set[str] = set()
        self.log_path = args.log
        self.keyword = ""
        self.done_shops: set[str] = set()
        self.per_keyword: dict[str, int] = {}  # new shops favourited per keyword (from the log)
        self._load_log()
        self.counts = {"checked": 0, "favorited": 0}

    def _load_log(self):
        if not os.path.exists(self.log_path):
            return
        with open(self.log_path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
            fields = rows and list(rows[0].keys())
        if fields and "keyword" not in fields:  # log from the first version: add the column
            with open(self.log_path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, self.LOG_HEADER)
                w.writeheader()
                w.writerows({**r, "keyword": ""} for r in rows)
        for r in rows:
            if r.get("shop"):
                self.done_shops.add(r["shop"])
            if r.get("keyword") and r.get("result") == "新店，已收藏":
                self.per_keyword[r["keyword"]] = self.per_keyword.get(r["keyword"], 0) + 1

    def log(self, shop: str, result: str, product: str = ""):
        new = not os.path.exists(self.log_path)
        with open(self.log_path, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            if new:
                w.writerow(self.LOG_HEADER)
            w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.keyword, shop, result, product])
        if shop:
            self.done_shops.add(shop)
        if self.keyword and result == "新店，已收藏":
            self.per_keyword[self.keyword] = self.per_keyword.get(self.keyword, 0) + 1
        say(f"  [{result}] {shop} {('· ' + product) if product else ''}")

    def read(self) -> list[Box]:
        boxes = ocr(self.win.grab())
        b = blocked(boxes)
        if b:
            raise Abort(f"检测到「{b.text}」，可能触发了风控/验证，已停止。请手动处理后再运行。")
        return boxes

    def back_to(self, is_list) -> bool:
        """Click the top-left back arrow until the list page shows. Never clicks it on the home screen
        (that arrow would leave 小店). Falls back to scrcpy's right-click BACK (e.g. live rooms)."""
        for i in range(9):
            boxes = self.read()
            if is_list(boxes):
                return True
            if is_home(boxes):  # went back past the list (e.g. search results closed)
                return False
            if i < 5:
                self.win.click(*BACK_ARROW, jitter=0.004)
            else:
                self.win.right_click()
            pause(1.5, 2.5)
        return False

    def back_to_home(self) -> bool:
        return self.back_to(is_home)

    def visit(self, card: Card) -> None:
        title = card.title.text
        self.win.click(card.title.x, card.title.y)
        pause(2.5, 4.0)
        boxes = self.read()
        if is_live(boxes) or not is_product(boxes):
            self.log("", "跳过：直播/非商品页", title)
            return
        name = shop_name_on_product(boxes)
        if not name:
            self.log("", "跳过：未识别到店名", title)
            return
        if name.text in self.done_shops:
            say(f"  [已处理过] {name.text}")
            return
        self.win.click(name.x, name.y)
        pause(2.5, 4.0)
        boxes = self.read()
        if not is_shop(boxes):
            self.log(name.text, "跳过：未进入店铺页", title)
            return
        self.counts["checked"] += 1
        if find(boxes, "已收藏", y_max=0.5):
            self.log(name.text, "已收藏，跳过", title)
            return
        if not is_new_store(boxes):
            self.log(name.text, "非新店", title)
            return
        btn = find(boxes, "收藏小店", y_max=0.5)
        if not btn:
            self.log(name.text, "新店，但没找到收藏按钮", title)
            return
        if self.args.dry_run:
            self.log(name.text, "新店（试运行，未收藏）", title)
            return
        self.win.click(btn.x, btn.y)
        pause(1.5, 2.5)
        ok = find(self.read(), "已收藏", y_max=0.5) is not None
        self.counts["favorited"] += ok
        self.log(name.text, "新店，已收藏" if ok else "新店，收藏未确认", title)

    def browse(self, is_list, done) -> str:
        """Work through a product list (home feed or search results) until done() or it runs out."""
        self.seen = set()
        empty_rounds, in_batch = 0, 0
        while not done():
            cards = [c for c in home_cards(self.read()) if c.key not in self.seen]
            if not cards:
                empty_rounds += 1
                if empty_rounds > 6:
                    return "end"
                self.win.wheel_down(self.args.scroll)
                pause(1.5, 2.5)
                continue
            empty_rounds = 0
            card = cards[0]
            self.seen.add(card.key)
            if card.live:  # orange 直播中 badge: never opened
                self.log("", "跳过：直播", card.title.text)
                continue
            if card.favorited:  # 收藏的店 on the card: already a favourite, no need to open it
                self.log("", "跳过：已收藏的店", card.title.text)
                continue
            say(f"打开：{card.title.text}")
            self.visit(card)
            if not self.back_to(is_list):
                return "lost"
            pause(*self.args.delay)
            in_batch += 1
            if in_batch >= self.args.rest_every:  # take a longer break now and then
                in_batch = 0
                t = random.uniform(*self.args.rest)
                say(f"休息 {t:.0f} 秒…")
                sleep(t)
        return "done"

    def search(self, kw: str) -> bool:
        """From the home screen: click the search bar, paste the keyword, search."""
        for _ in range(2):
            box = home_search_box(self.read())
            if not box:
                return False
            self.win.click(0.4, box.y, jitter=0.004)  # the input, left of the 搜索 button
            pause(1.5, 2.5)
            self.win.paste_text(kw)
            pause(1.0, 1.8)
            btn = find(self.read(), "搜索", y_max=0.16)
            if btn:
                self.win.click(btn.x, btn.y, jitter=0.004)
            else:
                self.win.enter()
            pause(3.0, 4.5)
            if is_results(self.read(), kw):
                return True
            if not self.back_to_home():
                return False
        return False

    def run_keywords(self, keywords: list[str]):
        target = self.args.per_keyword
        for kw in keywords:
            self.keyword = kw
            have = self.per_keyword.get(kw, 0)
            if have >= target:
                say(f"关键词「{kw}」已完成（{have}/{target}），跳过。")
                continue
            say(f"\n=== 关键词「{kw}」：已收藏 {have}/{target} 家新店 ===")
            if not self.back_to_home():
                raise Abort("回不到首页了，已停止。请手动回到「小店与卡包」首页后再运行。")
            if not self.search(kw):
                say(f"  搜索「{kw}」没有打开结果页，跳过这个关键词。")
                continue
            reason = self.browse(lambda b: is_results(b, kw), lambda: self.per_keyword.get(kw, 0) >= target)
            got = self.per_keyword.get(kw, 0)
            if reason == "done":
                say(f"关键词「{kw}」完成：{got}/{target}")
            elif reason == "end":
                say(f"关键词「{kw}」的结果已看完，只找到 {got}/{target} 家新店，换下一个关键词。")
            else:
                say(f"关键词「{kw}」：回不到搜索结果页（{got}/{target}），换下一个关键词。")
        self.keyword = ""
        self.back_to_home()
        left = [k for k in keywords if self.per_keyword.get(k, 0) < target]
        say("\n全部关键词都已收藏满 %d 家新店，结束。" % target if not left else
              f"\n本轮结束。未满 {target} 家的关键词：{'、'.join(left)}（结果不够多，可换关键词）")

    def run(self, keywords: list[str]):
        self.win.focus()
        pause(0.5, 1.0)
        if not is_home(self.read()):
            raise Abort("请先把手机停在「小店与卡包」首页（商品列表）再运行。")
        if keywords:
            self.run_keywords(keywords)
        else:
            reason = self.browse(is_home, lambda: self.counts["checked"] >= self.args.max)
            if reason == "end":
                say("没有更多新商品了，结束。")
            elif reason == "lost":
                raise Abort("回不到首页了，已停止。请手动回到「小店与卡包」首页后再运行。")
        say(f"完成：本次检查 {self.counts['checked']} 家店，收藏 {self.counts['favorited']} 家。记录在 {self.log_path}")


def load_keywords(path: str) -> list[str]:
    """One keyword per line; blank lines and lines starting with # are ignored.
    keywords.txt is created from keywords.example.txt the first time (it is yours: git never touches it)."""
    if not os.path.exists(path):
        example = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keywords.example.txt")
        if os.path.exists(example) and os.path.basename(path) == "keywords.txt":
            with open(example, encoding="utf-8") as src, open(path, "w", encoding="utf-8") as dst:
                dst.write(src.read())
        else:
            return []
    with open(path, encoding="utf-8-sig") as f:
        words = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
    return list(dict.fromkeys(words))


def probe(win: ScrcpyWindow, path: str = "probe.png"):
    img = win.grab()
    img.save(path)
    boxes = ocr(img)
    say(f"窗口画面 {img.size[0]}x{img.size[1]}，已保存 {path}，识别到 {len(boxes)} 段文字：")
    for b in boxes:
        say(f"  ({b.x:.3f}, {b.y:.3f})  {b.text}")
    say("首页" if is_home(boxes) else "商品页" if is_product(boxes) else "店铺页" if is_shop(boxes) else "其他页面")
    if is_home(boxes):
        box = home_search_box(boxes)
        say(f"  搜索框：{'找到' if box else '未找到'}")
    if is_home(boxes) or (not is_product(boxes) and not is_shop(boxes)):
        for c in home_cards(boxes):
            say(f"  商品卡片：{c.title.text}{'（直播，跳过）' if c.live else ''}{'（已收藏的店，跳过）' if c.favorited else ''}")
    if is_product(boxes):
        n = shop_name_on_product(boxes)
        say(f"  店名：{n.text if n else '未识别'}")
    if is_shop(boxes):
        say(f"  新店开张：{is_new_store(boxes)}，已收藏：{find(boxes, '已收藏', y_max=0.5) is not None}")


def main():
    ap = argparse.ArgumentParser(description="微信小店：自动收藏新店开张的小店（通过 scrcpy 窗口）")
    ap.add_argument("--window", default=os.environ.get("SCRCPY_WINDOW", "ANP-AN00"),
                    help="scrcpy 窗口标题（包含即可，默认 ANP-AN00）")
    ap.add_argument("--keywords", default="keywords.txt", help="关键词文件，每行一个（默认 keywords.txt）")
    ap.add_argument("--per-keyword", type=int, default=30, help="每个关键词收藏多少家新店后换下一个（默认 30）")
    ap.add_argument("--home", action="store_true", help="不用关键词，直接刷首页推荐列表")
    ap.add_argument("--max", type=int, default=30, help="首页模式：本次最多检查多少家店（默认 30）")
    ap.add_argument("--scroll", type=int, default=5, help="每次滚轮向下滚几格（默认 5）")
    ap.add_argument("--delay", type=float, nargs=2, default=(2.0, 5.0), help="每个商品之间的随机等待秒数")
    ap.add_argument("--rest-every", type=int, default=12, help="每处理多少个商品休息一次")
    ap.add_argument("--rest", type=float, nargs=2, default=(40.0, 90.0), help="休息的随机秒数")
    ap.add_argument("--log", default="shops_log.csv", help="记录文件（已处理的店铺下次会跳过）")
    ap.add_argument("--dry-run", action="store_true", help="试运行：不点「收藏小店」")
    ap.add_argument("--probe", action="store_true", help="只截图识别一次，检查窗口和识别是否正常")
    args = ap.parse_args()
    try:
        win = ScrcpyWindow(args.window)
        if args.probe:
            return probe(win)
        keywords = [] if args.home else load_keywords(args.keywords)
        if not args.home and not keywords:
            raise Abort(f"没有关键词：请在 {args.keywords} 里每行写一个关键词（# 开头的行是注释），或用 --home 刷首页。")
        if keywords:
            say(f"关键词（{len(keywords)} 个，每个收藏 {args.per_keyword} 家新店）：{'、'.join(keywords)}")
        Bot(win, args).run(keywords)
    except (KeyboardInterrupt, Stopped):
        say("\n已手动停止。")
    except Abort as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
