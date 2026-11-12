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
    w: float = 0.0  # width, as a fraction of the screen width

    @property
    def left(self) -> float:
        return self.x - self.w / 2


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


def at_home_top(boxes) -> bool:
    """The top of the home list: the icon row 订单 / 购物车 / 卡包 is visible. Exact labels only, since the
    page title 小店与卡包 also contains 卡包."""
    return any(b.y < 0.4 and b.text.strip() in ("订单", "购物车", "卡包", "限时秒杀") for b in boxes)


def home_search_box(boxes) -> Box | None:
    """The search bar on the home screen: the row of its 搜索 button."""
    return find(boxes, "搜索", y_max=0.25)


def shop_button(boxes) -> Box | None:
    """Product pages opened from search results: no shop name at the top, but a 小店 button in the
    bottom bar (next to 客服)."""
    for b in boxes:
        if b.y > 0.85 and b.x < 0.4 and b.text.strip() == "小店":
            return b
    return None


SHOP_INFO_WORDS = ("体验分", "新店开张", "已售", "近半年", "平均", "客服", "收藏")


def shop_name_on_shop(boxes) -> str:
    """The shop name on the shop page: the line just above 体验分 / 新店开张 (new shops have no 体验分)."""
    anchor = find(boxes, "体验分", y_max=0.45) or find(boxes, "新店开张", y_max=0.45)
    if not anchor:
        return ""
    above = [b for b in boxes if anchor.y - 0.06 < b.y < anchor.y - 0.008 and b.x > 0.25 and len(b.text) >= 2
             and not any(w in b.text for w in SHOP_INFO_WORDS)]
    return min(above, key=lambda b: abs(b.y - anchor.y)).text if above else ""


@dataclass
class Card:
    title: Box
    live: bool
    favorited: bool = False

    @property
    def key(self) -> str:
        return re.sub(r"\W", "", self.title.text)[:12]


CARD_NOISE = ("新客补贴", "立减", "朋友帮你", "跨店满", "先用后付", "今日热卖", "已售", "资深买家", "运费险", "人觉得")
DISCOUNT_RE = re.compile(r"\d(\.\d)?折")


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
        # the title is the first line above the price (one or two lines up, up to 0.13 of the screen);
        # skip the "共N款" line above it and the promo lines between title and price
        above = [b for b in boxes if (b.x < 0.5) == col and max(p.y - 0.13, top) < b.y < p.y - 0.005
                 and "¥" not in b.text and "￥" not in b.text and len(b.text) >= 4
                 and not re.fullmatch(r"[\d.]+", b.text) and not re.fullmatch(r"共\d+款", b.text.strip())
                 and not any(w in b.text for w in CARD_NOISE) and not DISCOUNT_RE.search(b.text)]
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



# ---------------------------------------------------------------- 客服消息 route: sold products of favourited shops

PREVIEW_WORDS = ("你好", "感谢", "，", "。", "[", "昨天", "星期", "前天")


def exact(boxes, *texts, y_min=0.0, y_max=1.0, x_min=0.0, x_max=1.0) -> Box | None:
    for b in boxes:
        if y_min <= b.y <= y_max and x_min <= b.x <= x_max and b.text.strip() in texts:
            return b
    return None


def is_me_page(boxes) -> bool:
    """WeChat's 我 tab: 小店与卡包 / 设置 in the list, the tab bar at the bottom."""
    return exact(boxes, "设置", y_min=0.3) is not None and exact(boxes, "通讯录", y_min=0.9) is not None


def is_chats_page(boxes) -> bool:
    """WeChat's 微信 tab (chats list): title 微信 / 微信(n) at the top, tab bar at the bottom."""
    top = any(b.y < 0.1 and re.fullmatch(r"微信(\(\d+\))?", b.text.strip()) for b in boxes)
    return top and exact(boxes, "通讯录", y_min=0.9) is not None


def kefu_entry(boxes) -> Box | None:
    """The 客服消息 「功能」 result in WeChat search."""
    for b in boxes:
        if b.text.strip() == "客服消息" and b.y > 0.12 and find(boxes, "功能", y_min=b.y, y_max=b.y + 0.06):
            return b
    return None


def is_kefu_list(boxes) -> bool:
    """The 客服消息 list: centred title 客服消息 at the top."""
    return exact(boxes, "客服消息", y_max=0.11, x_min=0.35, x_max=0.65) is not None


def kefu_shops(boxes) -> list[Box]:
    """Shop names in the 客服消息 list (one per row, the line above the greeting)."""
    out = []
    for b in boxes:
        t = b.text.strip()
        if (0.12 < b.y < 0.97 and 0.15 < b.x < 0.75 and len(t) >= 2 and not TIME_RE.match(t)
                and not any(w in t for w in PREVIEW_WORDS) and t != "客服消息"):
            out.append(b)
    return sorted(out, key=lambda b: b.y)


def chat_shop_title(boxes) -> Box | None:
    """In a shop chat: the shop name at the top (with 微信小店 under it)."""
    sub = find(boxes, "微信小店", y_max=0.13)
    cands = [b for b in boxes if b.y < (sub.y if sub else 0.1) and 0.25 < b.x < 0.75 and len(b.text) >= 2
             and not TIME_RE.match(b.text)]
    return min(cands, key=lambda b: abs(b.x - 0.5)) if cands else None


def profile_shop_row(boxes) -> Box | None:
    """Shop profile: the 小店 row under 微信小店信息."""
    return exact(boxes, "小店", y_min=0.2, y_max=0.7, x_max=0.25)


def shop_tab(boxes, name: str) -> Box | None:
    return exact(boxes, name, y_min=0.25, y_max=0.65)


def category_nav(boxes, tab: Box | None) -> list[Box]:
    """分类 tab: the category names in the left column."""
    top = (tab.y if tab else 0.4) + 0.04
    return sorted([b for b in boxes if b.x < 0.3 and b.y > top and len(b.text.strip()) >= 2
                   and not b.text.startswith("全部")], key=lambda b: b.y)


def all_link(boxes, tab: Box | None) -> Box | None:
    """分类 tab: 全部XXX > at the top of the right side."""
    top = (tab.y if tab else 0.4)
    cands = [b for b in boxes if b.x > 0.3 and b.y > top and b.text.strip().startswith("全部")]
    return min(cands, key=lambda b: b.y) if cands else None


SOLD_RE = re.compile(r"已售\s*([\d.]+)\s*(万)?")


def sold_of(text: str) -> int:
    m = SOLD_RE.search(text.replace(",", ""))
    if not m:
        return 0
    try:
        n = float(m.group(1))
    except ValueError:
        return 0
    return int(n * 10000) if m.group(2) else int(n)


def list_cards(boxes) -> list[tuple[Card, int]]:
    """Product cards of a category list with their 已售 count (0 when not shown)."""
    out = []
    for c in home_cards(boxes):
        col = c.title.x < 0.5
        sold = max([sold_of(b.text) for b in boxes if (b.x < 0.5) == col and "已售" in b.text
                    and c.title.y - 0.02 < b.y < c.title.y + 0.13] or [0])
        out.append((c, sold))
    return out

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
        xs = [p[0] for p in box]
        out.append(Box(str(text).strip(), sum(xs) / 4 / w, sum(p[1] for p in box) / 4 / h, (max(xs) - min(xs)) / w))
    return out


# ---------------------------------------------------------------- the scrcpy window (Windows)

BACK_ARROW = (0.065, 0.071)
HOME_SEARCH_ICON = (0.704, 0.076)  # 🔍 in the 小店与卡包 top bar when the list is scrolled
WX_TAB_CHATS = (0.128, 0.956)  # bottom tab 微信
WX_SEARCH_ICON = (0.824, 0.073)  # 🔍 at the top right of the 微信 chats list
CHAT_AVATAR = (0.099, 0.196)  # the shop's avatar next to its first message in a chat  # top-left back arrow, as fractions of the phone screen (see --probe)


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
        self._attach(found[0])

    def _attach(self, hwnd):
        self.hwnd = hwnd
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

    WHEEL_NOTCH = 120  # Windows wheel units per notch: pyautogui.scroll() sends raw units, scroll(-1) barely moves

    def wheel(self, notches: int, fx: float = 0.5, fy: float = 0.65):
        """Mouse wheel over the phone picture: negative = down, positive = up (scrcpy scrolls the phone)."""
        sx, sy = self._point(fx, fy, 0.02)
        self.gui.moveTo(sx, sy, duration=random.uniform(0.2, 0.5))
        step = self.WHEEL_NOTCH if notches > 0 else -self.WHEEL_NOTCH
        for _ in range(abs(notches)):
            self.gui.scroll(step)
            sleep(random.uniform(0.05, 0.15))

    def wheel_down(self, notches: int):
        self.wheel(-notches)

    def wheel_up(self, notches: int):
        self.wheel(notches)

    def drag(self, fx1, fy1, fx2, fy2, seconds=0.6):
        """Press, move, release: scrcpy turns this into a finger swipe (e.g. pull down to refresh)."""
        x1, y1 = self._point(fx1, fy1, 0.01)
        x2, y2 = self._point(fx2, fy2, 0.01)
        self.gui.moveTo(x1, y1, duration=random.uniform(0.2, 0.4))
        self.gui.mouseDown()
        try:
            self.gui.moveTo(x2, y2, duration=seconds)
            sleep(0.15)
        finally:
            self.gui.mouseUp()


# ---------------------------------------------------------------- the workflow

def pause(a, b):
    sleep(random.uniform(a, b))


def read_window(win) -> list[Box]:
    boxes = ocr(win.grab())
    b = blocked(boxes)
    if b:
        raise Abort(f"检测到「{b.text}」，可能触发了风控/验证，已停止。请手动处理后再运行。")
    return boxes


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
        return read_window(self.win)

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
        btn = shop_button(boxes)  # bottom 小店 button (search results); else the name at the top
        name = None if btn else shop_name_on_product(boxes)
        if not btn and not name:
            self.log("", "跳过：未识别到店名/小店按钮", title)
            return
        if name and name.text in self.done_shops:
            say(f"  [已处理过] {name.text}")
            return
        target = btn or name
        self.win.click(target.x, target.y)
        pause(2.5, 4.0)
        boxes = self.read()
        for _ in range(3):  # the shop page can take a few seconds to load
            if is_shop(boxes):
                break
            pause(1.5, 2.5)
            boxes = self.read()
        if not is_shop(boxes):
            self.log(name.text if name else "", "跳过：未进入店铺页", title)
            return
        shop = shop_name_on_shop(boxes) or (name.text if name else "")
        if btn and shop and shop in self.done_shops:
            say(f"  [已处理过] {shop}")
            return
        name = Box(shop, 0, 0)  # may be "" if the shop page name was not read
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

    def refresh_home(self) -> bool:
        """Back to the top of the home list, then pull down to load new recommendations."""
        say("回到首页顶部，下拉刷新推荐…")
        if not self.scroll_home_to_top():
            return False
        self.win.drag(0.5, 0.42, 0.5, 0.85, seconds=random.uniform(0.6, 0.9))
        pause(3.0, 4.5)
        return is_home(self.read())

    def browse(self, is_list, done, refresh=None) -> str:
        """Work through a product list (home feed or search results) until done() or it runs out.
        refresh: callable that reloads the list (home feed: back to the top + pull down); used every
        args.refresh_every products and when the list runs out."""
        self.seen = set()
        empty_rounds, in_batch, since_refresh, fruitless = 0, 0, 0, 0
        refresh_at = random.randint(*self.args.refresh_every) if refresh else None
        while not done():
            if refresh and since_refresh >= refresh_at:
                since_refresh, refresh_at = 0, random.randint(*self.args.refresh_every)
                if not refresh():
                    return "lost"
                self.seen = set()
                continue
            cards = [c for c in home_cards(self.read()) if c.key not in self.seen]
            if not cards:
                empty_rounds += 1
                if empty_rounds > 6:
                    if not refresh:
                        return "end"
                    fruitless += 1
                    if fruitless > 3:  # refreshing keeps showing nothing new
                        return "end"
                    empty_rounds, since_refresh = 0, 0
                    if not refresh():
                        return "lost"
                    self.seen = set()
                    continue
                self.win.wheel_down(self.args.scroll)
                pause(1.5, 2.5)
                continue
            empty_rounds, fruitless = 0, 0
            since_refresh += 1
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

    def scroll_home_to_top(self) -> bool:
        for _ in range(8):  # wheel up until the top row (订单 / 购物车 icons) shows
            if at_home_top(self.read()):
                return True
            self.win.wheel_up(15)
            pause(0.8, 1.5)
        return False

    def open_search(self) -> bool:
        """Home screen -> search input. The search box only shows at the top of the list; when the list
        is scrolled, the top bar has a 🔍 icon instead (next to the bell)."""
        box = home_search_box(self.read())
        if not box and self.scroll_home_to_top():
            box = home_search_box(self.read())
        if box:
            self.win.click(0.4, box.y, jitter=0.004)  # the input, left of the 搜索 button
        else:
            self.win.click(*HOME_SEARCH_ICON, jitter=0.004)
        pause(1.5, 2.5)
        return True

    def search(self, kw: str) -> bool:
        """From the home screen: open the search input, paste the keyword, search."""
        for _ in range(2):
            if not self.open_search():
                return False
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

    def run_home(self):
        say(f"\n=== 首页推荐：最多检查 {self.args.max} 家店 ===")
        reason = self.browse(is_home, lambda: self.counts["checked"] >= self.args.max, refresh=self.refresh_home)
        if reason == "end":
            say("首页没有更多新商品了。")
        elif reason == "lost":
            raise Abort("回不到首页了，已停止。请手动回到「小店与卡包」首页后再运行。")

    def run(self, keywords: list[str], home: bool | None = None, then_products: bool = False):
        """home=None: home feed only when there are no keywords; home=True with keywords: home feed first,
        then the keywords. then_products: afterwards open the sold products of favourited shops."""
        self.win.focus()
        pause(0.5, 1.0)
        if not is_home(self.read()):
            raise Abort("请先把手机停在「小店与卡包」首页（商品列表）再运行。")
        if home or (home is None and not keywords):
            self.run_home()
        if keywords:
            self.counts_before_keywords = dict(self.counts)
            self.run_keywords(keywords)
        say(f"完成：本次检查 {self.counts['checked']} 家店，收藏 {self.counts['favorited']} 家。记录在 {self.log_path}")
        if then_products:
            DesktopProductClicker(self.args, self).run()


class ProductClicker:
    """收藏店铺里有销量的商品，逐个点开一次（另外的采集软件采集）。
    Route: 小店与卡包 -> back to WeChat 我 -> 微信 tab -> 🔍 -> 客服消息 (功能) -> each shop's chat ->
    shop name/avatar -> 小店 -> 分类 -> each category: 全部XXX -> 销量 -> open every product with 已售 >= N."""
    UNSOLD_STREAK = 8  # stop a category after this many products in a row without sales
    BACK = BACK_ARROW
    SORT_Y_MAX = 0.3  # the 销量 sort tab is above this

    def __init__(self, win, args, bot):
        self.win, self.args, self.bot = win, args, bot
        self.done_shops = set()
        if os.path.exists(bot.log_path):
            with open(bot.log_path, encoding="utf-8-sig") as f:
                self.done_shops = {r["shop"] for r in csv.DictReader(f) if r.get("result") == "店铺商品已点完"}
        self.opened = 0

    def read(self):
        return read_window(self.win)

    def log(self, shop, result, product=""):
        kw, self.bot.keyword = self.bot.keyword, "客服消息"
        self.bot.log(shop, result, product)
        self.bot.keyword = kw

    def back(self, times=1):
        for _ in range(times):
            self.win.click(*self.BACK, jitter=0.004)
            pause(1.2, 2.0)

    def back_until(self, pred, tries=8) -> bool:
        for i in range(tries):
            boxes = self.read()
            if pred(boxes):
                return True
            if i < tries - 2:
                self.win.click(*self.BACK, jitter=0.004)
            else:
                self.win.right_click()
            pause(1.2, 2.2)
        return False

    def wait_for(self, pred, tries=4):
        for _ in range(tries):
            boxes = self.read()
            if pred(boxes):
                return boxes
            pause(1.0, 2.0)
        return None

    # ---- getting to the 客服消息 list
    def goto_kefu(self) -> bool:
        for _ in range(10):
            boxes = self.read()
            if is_kefu_list(boxes):
                return True
            if is_home(boxes):  # leave 小店 (its top-left arrow) -> WeChat 我
                self.win.click(*BACK_ARROW, jitter=0.004)
            elif is_me_page(boxes):
                self.win.click(*WX_TAB_CHATS, jitter=0.004)
            elif is_chats_page(boxes):
                self.win.click(*WX_SEARCH_ICON, jitter=0.004)
                pause(1.5, 2.5)
                self.win.paste_text("客服消息")
                pause(2.0, 3.0)
                entry = kefu_entry(self.read())
                if entry:
                    self.win.click(entry.x, entry.y)
                else:
                    say("  没找到「客服消息」功能，请看 probe.png")
                    return False
            else:
                self.win.click(*BACK_ARROW, jitter=0.004)
            pause(2.0, 3.0)
        return False

    # ---- one shop
    def open_shop(self, name: Box) -> bool:
        """客服消息 list -> chat -> shop profile -> 小店 page."""
        self.win.click(name.x, name.y)
        pause(2.0, 3.0)
        boxes = self.read()
        title = chat_shop_title(boxes)
        self.win.click(*((title.x, title.y) if title else CHAT_AVATAR))
        pause(2.0, 3.0)
        boxes = self.wait_for(lambda b: profile_shop_row(b) is not None or is_shop(b))
        if boxes is None:
            if title:  # the title did not open the profile: try the avatar
                self.win.click(*CHAT_AVATAR)
                pause(2.0, 3.0)
                boxes = self.wait_for(lambda b: profile_shop_row(b) is not None or is_shop(b))
            if boxes is None:
                return False
        if not is_shop(boxes):
            row = profile_shop_row(boxes)
            self.win.click(0.6, row.y + 0.02)
            pause(2.5, 3.5)
            if self.wait_for(is_shop) is None:
                return False
        return True

    def open_product(self, card: Card, shop: str, sold: int):
        self.win.click(card.title.x, card.title.y)
        pause(*self.args.product_wait)
        self.opened += 1
        self.log(shop, f"已点击商品（已售{sold}）", card.title.text)
        self.back()

    def do_category(self, shop: str, cat: str, sort_y_max: float | None = None) -> int:
        """In a product list (a category's 全部 list, or the shop's 商品 tab): sort by 销量, open each
        product with 已售 >= min_sold."""
        sort_y_max = sort_y_max or self.SORT_Y_MAX
        boxes = self.wait_for(lambda b: find(b, "销量", y_max=sort_y_max) is not None)
        if boxes is None:
            say(f"  [{cat}] 没有找到「销量」排序，跳过")
            return 0
        s = find(boxes, "销量", y_max=sort_y_max)
        self.win.click(s.x, s.y)
        pause(2.0, 3.0)
        seen, opened, unsold, empty = set(), 0, 0, 0
        while opened < self.args.per_category and self.opened < self.args.max_products:
            cards = [(c, n) for c, n in list_cards(self.read()) if c.key not in seen]
            if not cards:
                empty += 1
                if empty > 3:
                    break
                self.win.wheel_down(self.args.scroll)
                pause(1.5, 2.5)
                continue
            empty = 0
            card, sold = cards[0]
            seen.add(card.key)
            if card.live:
                continue
            if sold < self.args.min_sold:
                unsold += 1
                if unsold >= self.UNSOLD_STREAK:
                    break
                continue
            unsold = 0
            self.open_product(card, shop, sold)
            opened += 1
            if not self.back_until(lambda b: find(b, "销量", y_max=0.6) is not None, tries=4):
                break
            pause(*self.args.delay)
        return opened

    def do_shop(self, shop: str) -> None:
        boxes = self.read()
        tab = shop_tab(boxes, "分类")
        if not tab:  # some new shops only have 精选 / 商品 / 小店介绍: use the 商品 tab, sorted by 销量
            goods = shop_tab(boxes, "商品")
            if not goods:
                self.log(shop, "跳过：店铺没有「分类」或「商品」")
                return
            self.win.click(goods.x, goods.y)
            pause(2.0, 3.0)
            n = self.do_category(shop, "商品", sort_y_max=0.6)
            self.log(shop, "店铺商品已点完", f"共点击 {n} 个商品（商品页）")
            return
        self.win.click(tab.x, tab.y)
        pause(2.0, 3.0)
        done_cats, total, empty = set(), 0, 0
        while self.opened < self.args.max_products:
            boxes = self.read()
            tab = shop_tab(boxes, "分类")
            cats = [c for c in category_nav(boxes, tab) if c.text not in done_cats]
            if not cats:
                empty += 1
                if empty > 2:
                    break
                self.win.wheel(-4, 0.15, 0.75)  # scroll the left category column
                pause(1.2, 2.0)
                continue
            empty = 0
            cat = cats[0]
            done_cats.add(cat.text)
            self.win.click(cat.x, cat.y)
            pause(1.5, 2.5)
            link = all_link(self.read(), shop_tab(self.read(), "分类"))
            if not link:
                say(f"  [{cat.text}] 没有「全部」入口，跳过")
                continue
            self.win.click(link.x, link.y)
            pause(2.0, 3.0)
            n = self.do_category(shop, cat.text)
            total += n
            say(f"  [{cat.text}] 点击 {n} 个商品")
            # back to the shop's 分类 tab
            if not self.back_until(lambda b: shop_tab(b, "分类") is not None and category_nav(b, shop_tab(b, "分类")), tries=5):
                break
        self.log(shop, "店铺商品已点完", f"共点击 {total} 个商品")

    def run(self):
        say("\n=== 收藏店铺里有销量的商品（客服消息入口）===")
        if not self.goto_kefu():
            raise Abort("没能打开「客服消息」，请手动打开微信 → 搜索「客服消息」，停在店铺列表后再运行。")
        seen, empty = set(), 0
        while self.opened < self.args.max_products:
            names = [b for b in kefu_shops(self.read()) if b.text not in seen]
            if not names:
                empty += 1
                if empty > 3:
                    break
                self.win.wheel_down(5)
                pause(1.5, 2.5)
                continue
            empty = 0
            name = names[0]
            seen.add(name.text)
            if name.text in self.done_shops:
                say(f"  [已点完] {name.text}")
                continue
            say(f"店铺：{name.text}")
            if self.open_shop(name):
                self.do_shop(name.text)
            else:
                self.log(name.text, "跳过：没能进入店铺")
            if not self.back_until(is_kefu_list, tries=8) and not self.goto_kefu():
                raise Abort("回不到「客服消息」列表了，已停止。")
            pause(*self.args.delay)
        say(f"完成：共点击 {self.opened} 个有销量的商品。记录在 {self.bot.log_path}")


# ---------------------------------------------------------------- desktop WeChat (电脑版微信): 点商品
# Coordinates are fractions of the whole window (GetWindowRect).

LIST_TIME_RE = re.compile(r"(\d{1,2}[:：]\d{2}|昨天|前天|星期|周[一二三四五六日天]|\d{1,4}[/年-]\d{1,2})")


def wx_kefu_header(boxes) -> Box | None:
    """Desktop WeChat: 「< 客服消息」 at the top of the chat list (left column)."""
    return exact(boxes, "客服消息", y_max=0.2, x_max=0.36)


def wx_kefu_shops(boxes) -> list[Box]:
    """Desktop 客服消息 list: shop names (the row line that has the time on its right)."""
    head = wx_kefu_header(boxes)
    top = (head.y + 0.03) if head else 0.12
    times = [b for b in boxes if 0.25 < b.x < 0.4 and LIST_TIME_RE.search(b.text)]
    out = []
    for b in boxes:
        t = b.text.strip()
        if not (top < b.y < 0.98 and 0.06 < b.left < 0.2 and b.x < 0.3 and len(t) >= 2):
            continue
        if LIST_TIME_RE.match(t) or any(w in t for w in PREVIEW_WORDS) or t == "客服消息":
            continue
        if times and not any(abs(tb.y - b.y) < 0.015 for tb in times):
            continue  # a preview line (no time beside it)
        out.append(b)
    return sorted(out, key=lambda b: b.y)


def wx_chat_title(boxes) -> Box | None:
    """Desktop chat pane: the shop name at the top left of the right pane (微信小店 beside it)."""
    cands = [b for b in boxes if b.y < 0.1 and 0.33 < b.left < 0.7 and len(b.text.strip()) >= 2
             and b.text.strip() != "微信小店"]
    return min(cands, key=lambda b: b.left) if cands else None


def wx_first_message(boxes, title: Box | None) -> Box | None:
    """The first message bubble of the shop (its avatar is just left of it)."""
    left = (title.left - 0.01) if title else 0.36
    cands = [b for b in boxes if 0.1 < b.y < 0.8 and left < b.left < left + 0.2 and len(b.text.strip()) >= 2
             and not LIST_TIME_RE.match(b.text.strip())]
    return min(cands, key=lambda b: b.y) if cands else None


def wx_card_shop_row(boxes) -> tuple[Box, Box | None] | None:
    """The shop card that pops up from the avatar: 微信小店信息 / 小店  乐居良品. Returns (label, name)."""
    info = find(boxes, "微信小店信息")
    for b in boxes:
        if b.text.strip() == "小店" and (info is None or 0 < b.y - info.y < 0.08):
            name = [n for n in boxes if n is not b and abs(n.y - b.y) < 0.015 and n.x > b.x and len(n.text.strip()) >= 2]
            return b, (min(name, key=lambda n: n.x) if name else None)
    return None


class DesktopWindow(ScrcpyWindow):
    """A desktop WeChat window by handle. Coordinates are fractions of the whole window."""

    def __init__(self, hwnd):
        set_dpi_aware()
        import win32gui
        self.w32 = win32gui
        self._attach(hwnd)

    def rect(self):
        l, t, r, b = self.w32.GetWindowRect(self.hwnd)
        return l, t, r - l, b - t

    def scale(self) -> float:
        try:
            import ctypes
            return ctypes.windll.user32.GetDpiForWindow(self.hwnd) / 96 or 1.0
        except Exception:
            return 1.0

    def px(self, n: float) -> float:
        """n pixels (at 100% display scaling) as a fraction of the window width."""
        return n * self.scale() / max(self.rect()[2], 1)

    def focus(self):
        import win32con
        try:
            if self.w32.IsIconic(self.hwnd):
                self.w32.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            self.w32.SetForegroundWindow(self.hwnd)
        except Exception:
            pass

    def right_click(self):  # no phone BACK on the PC: Alt+Left goes back in the 小店 window
        self.focus()
        self.gui.hotkey("alt", "left")

    def close(self):
        import win32con
        try:
            self.w32.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass

    def alive(self) -> bool:
        return bool(self.w32.IsWindow(self.hwnd) and self.w32.IsWindowVisible(self.hwnd))


def top_windows() -> list[int]:
    """Visible top-level windows big enough to be a WeChat window."""
    import win32gui
    out = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h) and not win32gui.IsIconic(h):
            l, t, r, b = win32gui.GetWindowRect(h)
            if r - l >= 250 and b - t >= 300:
                out.append(h)
    win32gui.EnumWindows(cb, None)
    return out


WX_TITLES = ("微信", "weixin", "wechat")


def find_wechat_main() -> int:
    """The desktop WeChat main window: title 微信 / Weixin / WeChat (or its window class), the largest one."""
    set_dpi_aware()
    import win32gui
    cands = []

    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        title, cls = win32gui.GetWindowText(h).strip().lower(), win32gui.GetClassName(h)
        if title in WX_TITLES or "WeChatMainWnd" in cls:
            l, t, r, b = win32gui.GetWindowRect(h)
            cands.append(((r - l) * (b - t), h))
    win32gui.EnumWindows(cb, None)
    if not cands:
        raise Abort("找不到电脑版微信的窗口。请先打开并登录电脑版微信（不要最小化到托盘）。")
    return max(cands)[1]


class DesktopProductClicker(ProductClicker):
    """点商品 in desktop WeChat (not scrcpy): 客服消息 list -> shop chat -> its avatar -> card 小店 ->
    the 小店 window (分类 -> each category: 全部XXX -> 销量 -> products with 已售 >= N) -> close it."""
    BACK = (0.068, 0.139)  # the 小店 window: in-page back arrow (top left, below the 小店 bar)
    SORT_Y_MAX = 0.4

    def __init__(self, args, bot):
        import win32gui
        self.w32 = win32gui
        self.main = DesktopWindow(find_wechat_main())
        super().__init__(self.main, args, bot)
        self.shop_win: DesktopWindow | None = None

    def new_window(self, before: set[int], tries: int = 6) -> int | None:
        """A window that appeared since `before` (or became the foreground one), not the main window."""
        for _ in range(tries):
            pause(0.8, 1.2)
            new = [h for h in top_windows() if h not in before and h != self.main.hwnd]
            if new:
                return new[0]
            fg = self.w32.GetForegroundWindow()
            if fg and fg != self.main.hwnd and fg in top_windows() and "auto-favorite" not in self.w32.GetWindowText(fg).lower():
                l, t, r, b = self.w32.GetWindowRect(fg)
                if b - t > r - l:  # the 小店 window is phone shaped (taller than wide)
                    return fg
        return None

    def goto_kefu(self) -> bool:
        self.win = self.main
        self.main.focus()
        pause(0.5, 1.0)
        for attempt in range(2):
            boxes = self.read()
            if wx_kefu_header(boxes):
                return True
            search = exact(boxes, "搜索", y_max=0.12, x_max=0.36)
            if not search:
                return False
            self.main.click(search.x, search.y, jitter=0.002)
            pause(0.8, 1.2)
            self.main.paste_text("客服消息")
            pause(2.0, 3.0)
            hit = [b for b in self.read() if b.text.strip() == "客服消息" and b.y > search.y + 0.03 and b.x < 0.6]
            if not hit:
                self.main.gui.press("esc")
                return False
            self.main.click(hit[0].x, hit[0].y, jitter=0.002)
            pause(2.0, 3.0)
        return wx_kefu_header(self.read()) is not None

    def open_shop(self, name: Box) -> bool:
        """客服消息 row -> chat -> avatar -> card 小店 -> the 小店 window."""
        self.win = self.main
        self.main.click(name.x, name.y, jitter=0.003)
        pause(1.5, 2.5)
        boxes = self.read()
        title = wx_chat_title(boxes)
        msg = wx_first_message(boxes, title)
        if msg:
            ax, ay = msg.left - self.main.px(44), msg.y
        else:
            ax, ay = ((title.left if title else 0.377) + self.main.px(22)), 0.172
        self.main.click(ax, ay, jitter=0.002)
        pause(1.2, 2.0)
        row = None
        for _ in range(3):
            row = wx_card_shop_row(self.read())
            if row:
                break
            pause(0.8, 1.2)
        if not row:
            self.main.gui.press("esc")
            return False
        label, shop = row
        before = set(top_windows())
        self.main.click(*((shop.x, shop.y) if shop else (label.x + self.main.px(74), label.y)), jitter=0.002)
        h = self.new_window(before)
        if not h:
            self.main.gui.press("esc")
            return False
        self.shop_win = DesktopWindow(h)
        self.win = self.shop_win
        if self.wait_for(is_shop, tries=6) is None:
            self.close_shop()
            return False
        return True

    def close_shop(self):
        if self.shop_win:
            self.shop_win.close()
            self.shop_win = None
            pause(1.0, 1.5)
        self.win = self.main

    def open_product(self, card: Card, shop: str, sold: int):
        before = set(top_windows())
        self.win.click(card.title.x, card.title.y)
        pause(*self.args.product_wait)
        self.opened += 1
        self.log(shop, f"已点击商品（已售{sold}）", card.title.text)
        extra = [h for h in top_windows() if h not in before and h != self.main.hwnd]
        if extra:  # the product opened in its own window: close it
            for h in extra:
                DesktopWindow(h).close()
            pause(1.0, 1.5)
        else:
            self.back()

    def run(self):
        say("\n=== 收藏店铺里有销量的商品（电脑版微信 · 客服消息）===")
        if not self.goto_kefu():
            raise Abort("没能打开「客服消息」。请在电脑版微信里搜索「客服消息」并打开，"
                        "让左侧显示「< 客服消息」店铺列表，再点开始。")
        seen, empty = set(), 0
        while self.opened < self.args.max_products:
            self.win = self.main
            names = [b for b in wx_kefu_shops(self.read()) if b.text not in seen]
            if not names:
                empty += 1
                if empty > 3:
                    break
                self.main.wheel(-5, 0.2, 0.6)  # scroll the 客服消息 list
                pause(1.5, 2.5)
                continue
            empty = 0
            name = names[0]
            seen.add(name.text)
            if name.text in self.done_shops:
                say(f"  [已点完] {name.text}")
                continue
            say(f"店铺：{name.text}")
            try:
                if self.open_shop(name):
                    self.do_shop(name.text)
                else:
                    self.log(name.text, "跳过：没能打开店铺窗口")
            finally:
                self.close_shop()
            self.main.focus()
            if not wx_kefu_header(self.read()) and not self.goto_kefu():
                raise Abort("回不到「客服消息」列表了，已停止。")
            pause(*self.args.delay)
        say(f"完成：共点击 {self.opened} 个有销量的商品。记录在 {self.bot.log_path}")


def probe_desktop(path: str = "probe_wechat.png"):
    """Read the desktop WeChat window (and an open 小店 window) once and show what was recognised."""
    main = DesktopWindow(find_wechat_main())
    img = main.grab()
    img.save(path)
    boxes = ocr(img)
    say(f"电脑版微信窗口 {img.size[0]}x{img.size[1]}，已保存 {path}，识别到 {len(boxes)} 段文字：")
    for b in boxes:
        say(f"  ({b.x:.3f}, {b.y:.3f})  {b.text}")
    say(f"  客服消息列表：{'是' if wx_kefu_header(boxes) else '否'}；店铺：{'、'.join(b.text for b in wx_kefu_shops(boxes)) or '-'}")
    t = wx_chat_title(boxes)
    m = wx_first_message(boxes, t)
    say(f"  聊天标题：{t.text if t else '-'}；第一条消息：{m.text if m else '-'}")
    row = wx_card_shop_row(boxes)
    if row:
        say(f"  店铺卡片「小店」：{row[1].text if row[1] else '（店名没识别到）'}")
    import win32gui
    for h in top_windows():
        if h == main.hwnd:
            continue
        l, tp, r, btm = win32gui.GetWindowRect(h)
        if btm - tp <= r - l:
            continue
        w = DesktopWindow(h)
        sb = ocr(w.grab())
        if is_shop(sb) or find(sb, "销量", y_max=0.4):
            w.grab().save("probe_shop.png")
            say(f"小店窗口 {r - l}x{btm - tp}，已保存 probe_shop.png：")
            for b in sb:
                say(f"  ({b.x:.3f}, {b.y:.3f})  {b.text}")
            tab = shop_tab(sb, "分类")
            if tab:
                say(f"  分类导航：{'、'.join(b.text for b in category_nav(sb, tab))}")
            for c, n in list_cards(sb):
                say(f"  商品：{c.title.text}  已售 {n}")
            break


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
        btn = shop_button(boxes)
        n = shop_name_on_product(boxes)
        say(f"  底部「小店」按钮：{'找到' if btn else '没有'}；顶部店名：{n.text if n and not btn else '-'}")
    if is_kefu_list(boxes):
        say(f"  客服消息列表，店铺：{'、'.join(b.text for b in kefu_shops(boxes))}")
    if find(boxes, "销量", y_max=0.3) and not is_shop(boxes):
        for c, n in list_cards(boxes):
            say(f"  分类商品：{c.title.text}  已售 {n}")
    if is_shop(boxes):
        tab = shop_tab(boxes, "分类")
        if tab:
            say(f"  分类导航：{'、'.join(b.text for b in category_nav(boxes, tab))}；全部入口：{getattr(all_link(boxes, tab), 'text', '-')}")
        say(f"  店名：{shop_name_on_shop(boxes) or '未识别'}")
        say(f"  新店开张：{is_new_store(boxes)}，已收藏：{find(boxes, '已收藏', y_max=0.5) is not None}")


def main():
    ap = argparse.ArgumentParser(description="微信小店：自动收藏新店开张的小店（通过 scrcpy 窗口）")
    ap.add_argument("--window", default=os.environ.get("SCRCPY_WINDOW", "ANP-AN00"),
                    help="scrcpy 窗口标题（包含即可，默认 ANP-AN00）")
    ap.add_argument("--keywords", default="keywords.txt", help="关键词文件，每行一个（默认 keywords.txt）")
    ap.add_argument("--per-keyword", type=int, default=30, help="每个关键词收藏多少家新店后换下一个（默认 30）")
    ap.add_argument("--home", action="store_true", help="不用关键词，直接刷首页推荐列表")
    ap.add_argument("--home-then-keywords", action="store_true", help="先刷首页推荐，再按关键词搜索")
    ap.add_argument("--products", action="store_true", help="只做：打开收藏店铺里有销量的商品（客服消息入口）")
    ap.add_argument("--then-products", action="store_true", help="收藏完成后，接着打开收藏店铺里有销量的商品")
    ap.add_argument("--min-sold", type=int, default=1, help="商品已售至少多少才点击（默认 1）")
    ap.add_argument("--per-category", type=int, default=30, help="每个分类最多点击多少个商品（默认 30）")
    ap.add_argument("--max-products", type=int, default=300, help="本次最多点击多少个商品（默认 300）")
    ap.add_argument("--product-wait", type=float, nargs=2, default=(2.5, 4.0), help="商品页停留秒数")
    ap.add_argument("--max", type=int, default=30, help="首页模式：本次最多检查多少家店（默认 30）")
    ap.add_argument("--scroll", type=int, default=5, help="每次滚轮向下滚几格（默认 5）")
    ap.add_argument("--refresh-every", type=int, nargs=2, default=(30, 50),
                    help="首页模式：每看多少个商品回到顶部下拉刷新一次（随机范围，默认 30 50）")
    ap.add_argument("--delay", type=float, nargs=2, default=(2.0, 5.0), help="每个商品之间的随机等待秒数")
    ap.add_argument("--rest-every", type=int, default=12, help="每处理多少个商品休息一次")
    ap.add_argument("--rest", type=float, nargs=2, default=(40.0, 90.0), help="休息的随机秒数")
    ap.add_argument("--log", default="shops_log.csv", help="记录文件（已处理的店铺下次会跳过）")
    ap.add_argument("--dry-run", action="store_true", help="试运行：不点「收藏小店」")
    ap.add_argument("--probe", action="store_true", help="只截图识别一次，检查窗口和识别是否正常")
    ap.add_argument("--probe-wechat", action="store_true", help="识别一次电脑版微信窗口（点商品用），保存 probe_wechat.png")
    args = ap.parse_args()
    try:
        if args.probe_wechat:
            return probe_desktop()
        if args.products:  # desktop WeChat only: no scrcpy needed
            DesktopProductClicker(args, Bot(None, args)).run()
            return
        win = ScrcpyWindow(args.window)
        if args.probe:
            return probe(win)
        keywords = [] if args.home else load_keywords(args.keywords)
        if not args.home and not keywords:
            raise Abort(f"没有关键词：请在 {args.keywords} 里每行写一个关键词（# 开头的行是注释），或用 --home 刷首页。")
        if keywords:
            say(f"关键词（{len(keywords)} 个，每个收藏 {args.per_keyword} 家新店）：{'、'.join(keywords)}")
        Bot(win, args).run(keywords, home=True if args.home_then_keywords else None, then_products=args.then_products)
    except (KeyboardInterrupt, Stopped):
        say("\n已手动停止。")
    except Abort as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
