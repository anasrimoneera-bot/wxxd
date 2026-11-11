"""Auto-Favorite: desktop app (Windows) for follow_new_shops.py.

A window around the same engine: settings, keywords, 开始 / 停止, per-keyword progress and a live log.
Built into Auto-Favorite.exe with PyInstaller (see build.bat / .github/workflows/auto-favorite.yml).
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import traceback
import types

APP_NAME = "Auto-Favorite"
APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))

# A windowed exe has no console: libraries that write to stdout/stderr must not crash.
if sys.stdout is None or sys.stderr is None:
    _sink = open(os.path.join(APP_DIR, "auto-favorite-debug.log"), "a", encoding="utf-8")
    sys.stdout = sys.stdout or _sink
    sys.stderr = sys.stderr or _sink

import tkinter as tk  # noqa: E402
from tkinter import messagebox, ttk  # noqa: E402
from tkinter.scrolledtext import ScrolledText  # noqa: E402

import follow_new_shops as engine  # noqa: E402

SETTINGS = os.path.join(APP_DIR, "settings.json")
KEYWORDS = os.path.join(APP_DIR, "keywords.txt")
LOG_CSV = os.path.join(APP_DIR, "shops_log.csv")
PROBE_PNG = os.path.join(APP_DIR, "probe.png")

DEFAULTS = {"window": "ANP-AN00", "mode": "keywords", "per_keyword": 30, "home_max": 30, "dry_run": False,
            "delay_min": 2.0, "delay_max": 5.0, "rest_every": 12, "rest_min": 40.0, "rest_max": 90.0, "scroll": 5}
EXAMPLE_KEYWORDS = "车内用包包储物挂钩\n大号收纳支架\n"


def load_settings() -> dict:
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return {**DEFAULTS, **json.load(f)}
    except Exception:
        return dict(DEFAULTS)


def read_keywords_file() -> str:
    try:
        with open(KEYWORDS, encoding="utf-8-sig") as f:
            return f.read()
    except Exception:
        return EXAMPLE_KEYWORDS


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.msgs: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.bot: engine.Bot | None = None
        self.keywords: list[str] = []
        engine.say = lambda *a, **k: self.msgs.put(" ".join(str(x) for x in a))

        root.title(f"{APP_NAME} · 自动收藏新店")
        root.geometry("560x760")
        root.minsize(480, 600)
        s = load_settings()
        self.v = {
            "window": tk.StringVar(value=s["window"]), "mode": tk.StringVar(value=s["mode"]),
            "per_keyword": tk.IntVar(value=s["per_keyword"]), "home_max": tk.IntVar(value=s["home_max"]),
            "dry_run": tk.BooleanVar(value=s["dry_run"]), "scroll": tk.IntVar(value=s["scroll"]),
            "delay_min": tk.DoubleVar(value=s["delay_min"]), "delay_max": tk.DoubleVar(value=s["delay_max"]),
            "rest_every": tk.IntVar(value=s["rest_every"]),
            "rest_min": tk.DoubleVar(value=s["rest_min"]), "rest_max": tk.DoubleVar(value=s["rest_max"]),
        }
        self._build()
        self.kw_text.insert("1.0", read_keywords_file())
        self.refresh_progress()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(200, self.poll)

    # ------------------------------------------------------------ layout
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        top = ttk.LabelFrame(self.root, text="scrcpy 窗口")
        top.pack(fill="x", **pad)
        ttk.Label(top, text="窗口标题（包含即可）").pack(side="left", padx=6, pady=6)
        ttk.Entry(top, textvariable=self.v["window"], width=18).pack(side="left", pady=6)
        ttk.Button(top, text="检测识别", command=self.probe).pack(side="left", padx=6)

        mode = ttk.LabelFrame(self.root, text="模式")
        mode.pack(fill="x", **pad)
        r1 = ttk.Frame(mode)
        r1.pack(fill="x", padx=6, pady=3)
        ttk.Radiobutton(r1, text="关键词搜索：每个关键词收藏", variable=self.v["mode"], value="keywords").pack(side="left")
        ttk.Spinbox(r1, from_=1, to=999, textvariable=self.v["per_keyword"], width=5).pack(side="left")
        ttk.Label(r1, text="家新店，全部满了自动停止").pack(side="left")
        r2 = ttk.Frame(mode)
        r2.pack(fill="x", padx=6, pady=3)
        ttk.Radiobutton(r2, text="首页推荐：最多检查", variable=self.v["mode"], value="home").pack(side="left")
        ttk.Spinbox(r2, from_=1, to=999, textvariable=self.v["home_max"], width=5).pack(side="left")
        ttk.Label(r2, text="家店").pack(side="left")
        ttk.Checkbutton(mode, text="试运行（完整走流程，但不点「收藏小店」）", variable=self.v["dry_run"]).pack(anchor="w", padx=6, pady=3)

        kw = ttk.LabelFrame(self.root, text="搜索关键词（每行一个，# 开头为注释）")
        kw.pack(fill="x", **pad)
        self.kw_text = tk.Text(kw, height=5, font=("Microsoft YaHei UI", 10))
        self.kw_text.pack(fill="x", padx=6, pady=6)

        adv = ttk.LabelFrame(self.root, text="节奏（防止操作太快）")
        adv.pack(fill="x", **pad)
        a1 = ttk.Frame(adv)
        a1.pack(fill="x", padx=6, pady=3)
        ttk.Label(a1, text="每个商品间隔").pack(side="left")
        ttk.Spinbox(a1, from_=0.5, to=60, increment=0.5, textvariable=self.v["delay_min"], width=5).pack(side="left")
        ttk.Label(a1, text="–").pack(side="left")
        ttk.Spinbox(a1, from_=0.5, to=60, increment=0.5, textvariable=self.v["delay_max"], width=5).pack(side="left")
        ttk.Label(a1, text="秒；滚轮每次").pack(side="left")
        ttk.Spinbox(a1, from_=1, to=20, textvariable=self.v["scroll"], width=4).pack(side="left")
        ttk.Label(a1, text="格").pack(side="left")
        a2 = ttk.Frame(adv)
        a2.pack(fill="x", padx=6, pady=3)
        ttk.Label(a2, text="每").pack(side="left")
        ttk.Spinbox(a2, from_=1, to=200, textvariable=self.v["rest_every"], width=4).pack(side="left")
        ttk.Label(a2, text="个商品休息").pack(side="left")
        ttk.Spinbox(a2, from_=0, to=600, increment=5, textvariable=self.v["rest_min"], width=5).pack(side="left")
        ttk.Label(a2, text="–").pack(side="left")
        ttk.Spinbox(a2, from_=0, to=600, increment=5, textvariable=self.v["rest_max"], width=5).pack(side="left")
        ttk.Label(a2, text="秒").pack(side="left")

        btns = ttk.Frame(self.root)
        btns.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btns, text="▶ 开始", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="■ 停止", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="打开记录文件夹", command=lambda: os.startfile(APP_DIR)).pack(side="right")
        self.status = ttk.Label(btns, text="就绪", foreground="#606266")
        self.status.pack(side="left", padx=10)

        prog = ttk.LabelFrame(self.root, text="进度（已收藏的新店 / 目标）")
        prog.pack(fill="x", **pad)
        self.tree = ttk.Treeview(prog, columns=("kw", "n"), show="headings", height=4)
        self.tree.heading("kw", text="关键词")
        self.tree.heading("n", text="已收藏 / 目标")
        self.tree.column("kw", width=320)
        self.tree.column("n", width=120, anchor="center")
        self.tree.pack(fill="x", padx=6, pady=6)

        logf = ttk.LabelFrame(self.root, text="运行记录")
        logf.pack(fill="both", expand=True, **pad)
        self.log = ScrolledText(logf, height=10, font=("Microsoft YaHei UI", 9), state="disabled")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)
        ttk.Label(self.root, text="提示：运行时会控制鼠标，请勿操作电脑；鼠标甩到屏幕角落可紧急停止。",
                  foreground="#909399").pack(anchor="w", padx=10, pady=(0, 6))

    # ------------------------------------------------------------ helpers
    def write(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def poll(self):
        try:
            while True:
                self.write(self.msgs.get_nowait())
        except queue.Empty:
            pass
        if self.worker and not self.worker.is_alive():
            self.worker = None
            self.set_running(False)
        self.refresh_progress()
        self.root.after(250, self.poll)

    def parse_keywords(self) -> list[str]:
        lines = [ln.strip() for ln in self.kw_text.get("1.0", "end").splitlines()]
        return list(dict.fromkeys(ln for ln in lines if ln and not ln.startswith("#")))

    def refresh_progress(self):
        try:
            target = int(self.v["per_keyword"].get())
        except Exception:
            target = 30
        counts = self.bot.per_keyword if self.bot else self._counts_from_log()
        rows = self.parse_keywords() if self.v["mode"].get() == "keywords" else []
        current = [(k, f"{counts.get(k, 0)} / {target}") for k in rows]
        if [self.tree.item(i, "values") for i in self.tree.get_children()] != [tuple(r) for r in current]:
            self.tree.delete(*self.tree.get_children())
            for r in current:
                self.tree.insert("", "end", values=r)

    _log_cache: tuple[float, dict] = (0.0, {})

    def _counts_from_log(self) -> dict:
        try:
            mtime = os.path.getmtime(LOG_CSV)
        except OSError:
            return {}
        if self._log_cache[0] != mtime:
            import csv
            counts: dict[str, int] = {}
            try:
                with open(LOG_CSV, encoding="utf-8-sig") as f:
                    for r in csv.DictReader(f):
                        if r.get("keyword") and r.get("result") == "新店，已收藏":
                            counts[r["keyword"]] = counts.get(r["keyword"], 0) + 1
            except Exception:
                pass
            self._log_cache = (mtime, counts)
        return self._log_cache[1]

    def save(self):
        data = {}
        for k, var in self.v.items():
            try:
                data[k] = var.get()
            except Exception:
                data[k] = DEFAULTS[k]
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        with open(KEYWORDS, "w", encoding="utf-8") as f:
            f.write(self.kw_text.get("1.0", "end").rstrip() + "\n")
        return data

    def set_running(self, running: bool):
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        self.status.configure(text="运行中…" if running else "就绪", foreground="#67c23a" if running else "#606266")

    def place_beside(self, win: engine.ScrcpyWindow):
        """Keep this window off the scrcpy window, so no click lands on it."""
        try:
            x, y, w, h = win.rect()
            self.root.update_idletasks()
            aw, ah = self.root.winfo_width(), self.root.winfo_height()
            sw = self.root.winfo_screenwidth()
            nx = x + w + 20 if x + w + 20 + aw <= sw else max(x - aw - 20, 0)
            self.root.geometry(f"+{nx}+{max(y - 40, 0)}")
        except Exception:
            pass

    def run_in_thread(self, fn):
        def wrapper():
            try:
                fn()
            except engine.Stopped:
                self.msgs.put("已停止。")
            except engine.Abort as e:
                self.msgs.put(f"⚠ {e}")
                self.root.after(0, lambda: messagebox.showwarning(APP_NAME, str(e)))
            except Exception as e:  # pyautogui fail-safe and anything unexpected
                if type(e).__name__ == "FailSafeException":
                    self.msgs.put("鼠标移到了屏幕角落，已紧急停止。")
                else:
                    self.msgs.put("出错了：" + "".join(traceback.format_exception_only(type(e), e)).strip())
                    sys.stderr.write(traceback.format_exc())
        engine.STOP.clear()
        self.set_running(True)
        self.worker = threading.Thread(target=wrapper, daemon=True)
        self.worker.start()

    # ------------------------------------------------------------ actions
    def probe(self):
        if self.worker:
            return
        title = self.v["window"].get().strip()

        def job():
            self.msgs.put("正在加载识别模型并截图…")
            win = engine.ScrcpyWindow(title)
            self.root.after(0, lambda: self.place_beside(win))
            engine.probe(win, PROBE_PNG)
        self.run_in_thread(job)

    def start(self):
        if self.worker:
            return
        s = self.save()
        keywords = self.parse_keywords() if s["mode"] == "keywords" else []
        if s["mode"] == "keywords" and not keywords:
            messagebox.showwarning(APP_NAME, "请先填写搜索关键词（每行一个）。")
            return
        args = types.SimpleNamespace(
            max=int(s["home_max"]), per_keyword=int(s["per_keyword"]), scroll=int(s["scroll"]),
            delay=(float(s["delay_min"]), max(float(s["delay_max"]), float(s["delay_min"]))),
            rest_every=max(int(s["rest_every"]), 1),
            rest=(float(s["rest_min"]), max(float(s["rest_max"]), float(s["rest_min"]))),
            log=LOG_CSV, dry_run=bool(s["dry_run"]))
        title = s["window"].strip()
        if keywords:
            self.write(f"关键词 {len(keywords)} 个，每个收藏 {args.per_keyword} 家新店：{'、'.join(keywords)}")

        def job():
            win = engine.ScrcpyWindow(title)
            self.root.after(0, lambda: self.place_beside(win))
            engine.sleep(0.8)
            self.msgs.put("正在加载识别模型…（第一次稍慢）")
            self.bot = engine.Bot(win, args)
            self.bot.run(keywords)
        self.run_in_thread(job)

    def stop(self):
        engine.STOP.set()
        self.status.configure(text="正在停止…", foreground="#e6a23c")

    def on_close(self):
        if self.worker and not messagebox.askyesno(APP_NAME, "正在运行，确定停止并退出？"):
            return
        engine.STOP.set()
        try:
            self.save()
        except Exception:
            pass
        self.root.destroy()


def smoke() -> int:
    """Build check (CI): open the window, run the bundled OCR once, write smoke.txt, exit."""
    out = os.path.join(APP_DIR, "smoke.txt")
    try:
        root = tk.Tk()
        App(root)
        root.update()
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (400, 120), "white")
        ImageDraw.Draw(img).text((20, 40), "Auto Favorite 2026", fill="black")
        boxes = engine.ocr(img.resize((1200, 360)))
        root.destroy()
        with open(out, "w", encoding="utf-8") as f:
            f.write("ok: window + OCR (%d text boxes: %s)\n" % (len(boxes), [b.text for b in boxes]))
        return 0
    except Exception:
        with open(out, "w", encoding="utf-8") as f:
            f.write("FAILED\n" + traceback.format_exc())
        return 1


def main():
    engine.set_dpi_aware()
    if "--smoke" in sys.argv:
        sys.exit(smoke())
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
