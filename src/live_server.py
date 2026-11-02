#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qoder CN Token / Credits 实时面板。

单进程：增量 tail 转录 -> 本机 HTTP 服务 -> 浏览器实时页 + 置顶小窗。
只监听 127.0.0.1，不上传任何数据；关闭置顶小窗即停止服务。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from cn_usage_core import ROOT, TZ, WINDOW, build_range, parse_line, summarize

SRC = Path(__file__).resolve().parent
PROJECT = SRC.parent
DIST = PROJECT / "dist"
PAGE = SRC / "live_dashboard.html"
HISTORY = DIST / "Qoder_CN_Token用量统计.html"
STATE = DIST / ".live_server.json"
LOG = DIST / "live_server.log"
APP_TITLE = "Qoder Token 实时消耗"
APP_VERSION = "1.0.0"
DEFAULT_PORT = 8765
TICK_SECONDS = 1.0
DISCOVERY_SECONDS = 2.0

logger = logging.getLogger("token-live")


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):
            pass


def setup_logging() -> None:
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    try:
        DIST.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(LOG, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)


class FileState:
    """单个转录文件的增量读取状态。"""

    def __init__(self, path: Path):
        self.path = path
        self.is_sub = "subagents" in path.parts
        self.session = path.parent.parent.name if self.is_sub else path.stem
        self.offset = 0
        self.pending = b""
        self.records = []

    def refresh(self) -> bool:
        try:
            size = self.path.stat().st_size
        except OSError:
            return False
        reset = False
        if size < self.offset:
            self.offset = 0
            self.pending = b""
            self.records = []
            reset = True
        if size == self.offset and not reset:
            return False
        try:
            with self.path.open("rb") as fh:
                fh.seek(self.offset)
                chunk = fh.read()
        except OSError:
            return False
        if not chunk:
            return reset
        self.offset += len(chunk)
        data = self.pending + chunk
        self.pending = b""
        parts = data.split(b"\n")
        self.pending = parts.pop()
        added = 0
        for raw in parts:
            rec = parse_line_safe(raw, self.session, self.is_sub)
            if rec:
                self.records.append(rec)
                added += 1
        if self.pending:
            # 文件可能已经写完但最后一行没有换行；JSON 解析成功才消费，
            # 半行/半写入的 JSON 解析失败会继续留在 pending 里等待下一次。
            try:
                json.loads(self.pending.decode("utf-8", "ignore"))
            except Exception:
                pass
            else:
                rec = parse_line_safe(self.pending, self.session, self.is_sub)
                if rec:
                    self.records.append(rec)
                    added += 1
                self.pending = b""
        return reset or added > 0


def parse_line_safe(raw, session, is_sub):
    try:
        return parse_line(raw, session, is_sub)
    except Exception:
        logger.debug("解析转录行失败：%s", traceback.format_exc())
        return None


class LiveStore:
    def __init__(self, root: Path = ROOT):
        self.root = Path(root).expanduser()
        if not self.root.is_dir():
            logger.warning(
                "转录目录不存在：%s（可用 --root 或 QODER_CN_PROJECTS 指定）",
                self.root,
            )
        self.states = {}
        self.records = []
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.last_change = None
        self._last_discovery = 0.0
        self._snap_cache = {}
        self._initial_scan()
        self._thread = threading.Thread(target=self._poll_loop, name="live-poll", daemon=True)

    def _initial_scan(self) -> None:
        t0 = time.time()
        paths = sorted(self.root.rglob("*.jsonl"))
        for path in paths:
            st = FileState(path)
            st.refresh()
            self.states[path] = st
        self._rebuild()
        logger.info(
            "初始扫描完成：%d 个文件 / %d 条记录 / %.1fs",
            len(self.states), len(self.records), time.time() - t0,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _rebuild(self) -> None:
        records = []
        for st in self.states.values():
            records.extend(st.records)
        records.sort(key=lambda r: r["ts"])
        with self.lock:
            self.records = records
            self.last_change = datetime.now(TZ)
            self._snap_cache = {}

    def _poll_loop(self) -> None:
        while not self.stop_event.wait(TICK_SECONDS):
            try:
                self.poll_once()
            except Exception:
                logger.warning("增量扫描失败：%s", traceback.format_exc())

    def poll_once(self) -> None:
        now = time.monotonic()
        changed = False
        if now - self._last_discovery >= DISCOVERY_SECONDS:
            self._last_discovery = now
            try:
                paths = set(self.root.rglob("*.jsonl"))
            except OSError:
                paths = set(self.states)
            for path in paths - set(self.states):
                st = FileState(path)
                self.states[path] = st
                if st.refresh():
                    changed = True
            for path in set(self.states) - paths:
                self.states.pop(path, None)
                changed = True
        for st in list(self.states.values()):
            if st.refresh():
                changed = True
        if changed:
            self._rebuild()

    def _rates(self, records, now, seconds):
        since = now - timedelta(seconds=seconds)
        total_tokens = 0.0
        credits = 0.0
        trips = 0
        for r in records:
            if r["ts"] >= since:
                total_tokens += r["ratio"] * WINDOW
                credits += r["credits"]
                trips += 1
        minutes = seconds / 60
        return {
            "tokens": round(total_tokens),
            "credits": round(credits, 3),
            "trips": trips,
            "tokensPerMin": round(total_tokens / minutes),
            "creditsPerMin": round(credits / minutes, 3),
        }

    def _spark(self, records, now, minutes=30):
        end = now.replace(second=0, microsecond=0)
        start = end - timedelta(minutes=minutes - 1)
        buckets = []
        index = {}
        for i in range(minutes):
            t = start + timedelta(minutes=i)
            key = t.strftime("%Y-%m-%d %H:%M")
            index[key] = i
            buckets.append({"t": t.strftime("%H:%M"), "tokens": 0.0, "credits": 0.0, "trips": 0})
        for r in records:
            if r["ts"] < start:
                continue
            i = index.get(r["ts"].strftime("%Y-%m-%d %H:%M"))
            if i is None:
                continue
            buckets[i]["tokens"] += r["ratio"] * WINDOW
            buckets[i]["credits"] += r["credits"]
            buckets[i]["trips"] += 1
        for b in buckets:
            b["tokens"] = round(b["tokens"])
            b["credits"] = round(b["credits"], 3)
        return buckets

    def snapshot(self, pinned: str = "", now: datetime | None = None) -> dict:
        key = (pinned or "").strip()
        stamp = time.monotonic()
        cached = self._snap_cache.get(key)
        if cached and stamp - cached[0] < 0.7:
            return cached[1]
        snap = self._snapshot(key, now)
        with self.lock:
            self._snap_cache = {key: (stamp, snap)}
        return snap

    def _snapshot(self, pinned: str, now: datetime | None = None) -> dict:
        now = now or datetime.now(TZ)
        with self.lock:
            records = self.records
            files = len(self.states)
        base = {
            "generatedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
            "generatedEpoch": int(now.timestamp()),
            "window": WINDOW,
            "source": str(self.root),
            "sourceExists": self.root.is_dir(),
            "files": files,
            "records": len(records),
            "autoSession": None,
            "selectedSession": None,
            "followed": True,
            "session": None,
            "today": None,
            "all": None,
            "spark": [],
            "sessions": [],
        }
        if not records:
            return base

        today_str = now.strftime("%Y-%m-%d")
        auto = records[-1]["session"]
        all_sessions = {r["session"] for r in records}
        selected = pinned if pinned in all_sessions else auto

        sess_records = [r for r in records if r["session"] == selected]
        today_records = [r for r in records if r["date"] == today_str]
        latest = sess_records[-1]
        main_records = [r for r in sess_records if not r["sub"]] or sess_records
        latest_main = main_records[-1]
        context_pct = round(latest_main["ratio"] * 100, 1)

        sess_sum = summarize(sess_records)
        today_range = build_range(today_records, "today", "今日")
        today_sum = dict(today_range["summary"])
        today_sum["topModels"] = today_range["model"][:6]
        today_sum["topProjects"] = today_range["project"][:6]

        agg = {}
        for r in records:
            b = agg.get(r["session"])
            if b is None:
                b = agg[r["session"]] = {
                    "id": r["session"], "ratio": 0.0, "credits": 0.0, "trips": 0,
                    "todayRatio": 0.0, "todayCredits": 0.0,
                    "last": r["ts"], "label": r["label"], "project": r["project"],
                }
            b["ratio"] += r["ratio"]
            b["credits"] += r["credits"]
            b["trips"] += 1
            if r["date"] == today_str:
                b["todayRatio"] += r["ratio"]
                b["todayCredits"] += r["credits"]
            if r["ts"] >= b["last"]:
                b["last"] = r["ts"]
                b["label"] = r["label"]
                b["project"] = r["project"]
        session_rows = []
        for b in agg.values():
            session_rows.append({
                "id": b["id"],
                "short": b["id"][:8],
                "label": b["label"],
                "lastTs": b["last"].strftime("%m-%d %H:%M:%S"),
                "lastAgo": max(0, int((now - b["last"]).total_seconds())),
                "tokens": round(b["ratio"] * WINDOW),
                "credits": round(b["credits"], 3),
                "trips": b["trips"],
                "todayTokens": round(b["todayRatio"] * WINDOW),
                "todayCredits": round(b["todayCredits"], 3),
                "selected": b["id"] == selected,
            })
        session_rows.sort(key=lambda x: x["lastAgo"])
        shown = session_rows[:12]
        if selected not in {x["id"] for x in shown}:
            shown.append(next(x for x in session_rows if x["id"] == selected))
        # 上次活动时间以“距今秒数”升序，等价于最近优先
        shown.sort(key=lambda x: x["lastAgo"])

        base.update({
            "autoSession": auto,
            "selectedSession": selected,
            "followed": selected == auto,
            "session": {
                "id": selected,
                "short": selected[:8],
                "label": latest_main["label"],
                "project": latest_main["project"],
                "lastTs": latest["ts"].strftime("%m-%d %H:%M:%S"),
                "lastAgo": max(0, int((now - latest["ts"]).total_seconds())),
                "lastModel": latest_main["model"],
                "contextPct": context_pct,
                **sess_sum,
                "rate1m": self._rates(sess_records, now, 60),
                "rate5m": self._rates(sess_records, now, 300),
            },
            "today": today_sum,
            "all": summarize(records),
            "spark": self._spark(sess_records, now, 30),
            "sessions": shown,
        })
        return base


STORE = None


def history_missing_page() -> bytes:
    return (
        "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>历史仪表盘尚未生成</title>"
        "<style>body{background:#0a0e16;color:#e8edf5;font-family:-apple-system,"
        "\"Microsoft YaHei\",sans-serif;max-width:720px;margin:12vh auto;padding:0 24px;"
        "line-height:1.8}h1{font-size:22px;color:#a78bfa}code,pre{background:#101725;"
        "border:1px solid #1d2738;border-radius:8px;padding:2px 7px;color:#c9d2e2}"
        "pre{padding:14px 16px;overflow:auto}a{color:#8fb0ff}</style>"
        "<h1>历史仪表盘尚未生成</h1>"
        "<p>先运行下面的命令生成一次静态快照：</p>"
        "<pre>python src/build_cn_token_dashboard.py</pre>"
        "<p>生成结果会写入 <code>dist/</code>，不会修改任何转录文件。完成后刷新本页即可。</p>"
        "<p><a href=\"/\">返回实时面板</a></p></body></html>"
    ).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "TokenLive/1.0"

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/live":
                qs = parse_qs(parsed.query)
                sid = (qs.get("session") or [""])[0].strip()[:128]
                self._json(STORE.snapshot(sid))
                return
            if path == "/api/ping":
                self._json({
                    "ok": True,
                    "pid": os.getpid(),
                    "title": APP_TITLE,
                    "version": APP_VERSION,
                })
                return
            if path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                return
            if path == "/history":
                if HISTORY.exists():
                    self._send(200, HISTORY.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._send(404, history_missing_page(), "text/html; charset=utf-8")
                return
            if path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
                return
            self._send(404, "not found".encode("utf-8"), "text/plain; charset=utf-8")
        except Exception:
            logger.warning("HTTP 处理失败：%s", traceback.format_exc())
            try:
                self._json({"error": "internal error"}, 500)
            except Exception:
                pass

    def log_message(self, fmt, *args):
        return


def make_server(port: int):
    last_error = None
    for candidate in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            srv.daemon_threads = True
            return srv
        except OSError as exc:
            last_error = exc
    raise RuntimeError(f"无法绑定本机端口 {port}..{port + 19}：{last_error}")


def live_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def running_instance():
    try:
        info = json.loads(STATE.read_text(encoding="utf-8"))
        port = int(info["port"])
    except Exception:
        return None
    url = live_url(port)
    try:
        with urlopen(url + "api/ping", timeout=0.6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok"):
            return {"port": port, "url": url, "pid": data.get("pid")}
    except Exception:
        pass
    return None


def write_state(port: int) -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({
        "pid": os.getpid(),
        "port": port,
        "url": live_url(port),
        "version": APP_VERSION,
        "startedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_state() -> None:
    try:
        info = json.loads(STATE.read_text(encoding="utf-8"))
        if int(info.get("pid", -1)) == os.getpid():
            STATE.unlink(missing_ok=True)
    except Exception:
        pass


class PetWindow:
    """无边框、始终置顶的迷你实时窗。"""

    W = 360
    H = 196
    BG = "#0b111c"
    BORDER = "#273248"
    TEXT = "#e8edf5"
    MUTED = "#7e8aa0"
    MUTED2 = "#5b6577"
    ACCENT = "#a78bfa"
    GREEN = "#4ade80"
    AMBER = "#f59e0b"
    RED = "#f87171"

    def __init__(self, store: LiveStore, url: str, on_close):
        import tkinter as tk
        self.tk = tk
        self.store = store
        self.url = url
        self.on_close = on_close
        self._closed = False
        self._drag = (0, 0)
        self.root = tk.Tk()
        self.font_family = self._pick_font()
        self.root.title(APP_TITLE)
        self.root.overrideredirect(True)
        try:
            self.root.attributes("-topmost", True)
            self.root.attributes("-alpha", 0.97)
        except tk.TclError:
            pass
        self.root.configure(bg=self.BORDER)
        self._build()
        self._place()
        self.root.after(150, self._tick)

    def _pick_font(self):
        try:
            import tkinter.font as tkfont
            available = set(tkfont.families(self.root))
            for name in (
                "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                "Noto Sans CJK SC", "Source Han Sans SC", "Segoe UI", "Helvetica",
            ):
                if name in available:
                    return name
            return tkfont.nametofont("TkDefaultFont").actual("family")
        except Exception:
            return "TkDefaultFont"

    def _label(self, parent, text="", size=9, bold=False, fg=None, bg=None):
        return self.tk.Label(
            parent, text=text, bg=bg or self.BG, fg=fg or self.TEXT,
            font=(self.font_family, size, "bold" if bold else "normal"),
            anchor="w", justify="left", bd=0, padx=0, pady=0,
        )

    def _build(self):
        tk = self.tk
        body = tk.Frame(self.root, bg=self.BG)
        body.pack(fill="both", expand=True, padx=1, pady=1)

        self.dot = self._label(body, "●", 10, fg=self.GREEN)
        self.dot.place(x=12, y=9)
        self.title = self._label(body, "正在读取…", 10, bold=True)
        self.title.place(x=28, y=8)
        self.sub = self._label(body, "", 8, fg=self.MUTED)
        self.sub.place(x=28, y=26)

        self.close_btn = self._label(body, "×", 13, fg=self.MUTED, bg=self.BG)
        self.close_btn.place(x=336, y=4)
        self.close_btn.bind("<Button-1>", lambda e: self.close())
        self.open_btn = self._label(body, "↗", 11, fg=self.MUTED, bg=self.BG)
        self.open_btn.place(x=314, y=7)
        self.open_btn.bind("<Button-1>", lambda e: webbrowser.open(self.url))
        for widget in (self.close_btn, self.open_btn):
            widget.bind("<Enter>", lambda e, w=widget: w.configure(fg=self.TEXT))
            widget.bind("<Leave>", lambda e, w=widget: w.configure(fg=self.MUTED))

        self.tokens = self._label(body, "—", 22, bold=True, fg=self.ACCENT)
        self.tokens.place(x=12, y=44)
        self.tokens_cap = self._label(body, "本会话 Tokens（推导）", 8, fg=self.MUTED2)
        self.tokens_cap.place(x=13, y=79)

        self.credits = self._label(body, "—", 22, bold=True, fg=self.GREEN)
        self.credits.place(x=196, y=44)
        self.credits_cap = self._label(body, "本会话 Credits（实测）", 8, fg=self.MUTED2)
        self.credits_cap.place(x=197, y=79)

        self.today = self._label(body, "今日 —", 9, fg="#c9d2e2")
        self.today.place(x=12, y=96)
        self.context = self._label(body, "上下文 —", 9, fg="#c9d2e2")
        self.context.place(x=12, y=115)
        self.free = self._label(body, "零扣费往返 0", 8, fg=self.MUTED)
        self.free.place(x=12, y=134)

        self.spark = tk.Canvas(body, width=self.W - 24, height=36, bg=self.BG, highlightthickness=0, bd=0)
        self.spark.place(x=12, y=152)

        for w in (body, self.title, self.sub, self.tokens, self.tokens_cap, self.credits,
                  self.credits_cap, self.today, self.context, self.free, self.spark, self.dot):
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)

    def _place(self):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        x = max(12, sw - self.W - 26)
        self.root.geometry(f"{self.W}x{self.H}+{x}+24")

    def _start_drag(self, event):
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _on_drag(self, event):
        dx, dy = self._drag
        self.root.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _fmt_tokens(self, n):
        n = n or 0
        if n >= 1e8:
            return f"{n / 1e8:.2f}".rstrip("0").rstrip(".") + " 亿"
        if n >= 1e4:
            return f"{n / 1e4:.0f} 万" if n >= 1e7 else f"{n / 1e4:.1f}".rstrip("0").rstrip(".") + " 万"
        return f"{round(n):,}"

    def _draw_spark(self, points):
        c = self.spark
        c.delete("all")
        w = self.W - 24
        h = 36
        if not points:
            return
        vals = [p["tokens"] for p in points]
        peak = max(vals) or 1
        n = len(vals)
        step = w / max(1, n - 1)
        coords = []
        for i, v in enumerate(vals):
            x = i * step
            y = h - 3 - (v / peak) * (h - 8)
            coords.extend([x, y])
        if n > 1:
            c.create_line(*coords, fill=self.ACCENT, width=1.6, smooth=True)
            area = [0, h, *coords, w, h]
            c.create_polygon(*area, fill="#2a2148", outline="")
        c.create_line(0, h, w, h, fill="#1d2738")

    def _clear_metrics(self):
        self.tokens.configure(text="—")
        self.credits.configure(text="—")
        self.today.configure(text="今日 —")
        self.context.configure(text="上下文 —")
        self.free.configure(text="免费/零扣费 —", fg=self.MUTED)

    def _tick(self):
        if self._closed:
            return
        try:
            snap = self.store.snapshot("")
            sess = snap.get("session")
            if not sess:
                self._clear_metrics()
                self._draw_spark([])
                if not snap.get("sourceExists", True):
                    self.dot.configure(fg=self.RED)
                    self.title.configure(text="未找到 Qoder 转录目录", fg=self.AMBER)
                    self.sub.configure(text="用 --root 指定数据目录")
                else:
                    self.dot.configure(fg=self.AMBER)
                    self.title.configure(text="等待第一条对话…", fg=self.TEXT)
                    self.sub.configure(text=f"已扫描 {snap.get('files', 0)} 个转录文件")
            else:
                title = sess["label"]
                if len(title) > 18:
                    title = title[:17] + "…"
                self.dot.configure(fg=self.GREEN if sess["lastAgo"] <= 120 else self.AMBER)
                self.title.configure(text=title, fg=self.TEXT)
                self.sub.configure(text=f"{sess['short']} · {sess['lastModel']} · {sess['lastAgo']} 秒前")
                self.tokens.configure(text=self._fmt_tokens(sess["tokens"]))
                self.credits.configure(text=f"{sess['credits']:,.1f}")
                today = snap["today"]
                self.today.configure(
                    text=f"今日 {self._fmt_tokens(today['tokens'])} · {today['credits']:,.1f} Credits · "
                         f"{today['trips']:,} 次往返"
                )
                rate = sess["rate5m"]["tokensPerMin"]
                self.context.configure(
                    text=f"上下文 {sess['contextPct']:.1f}% · 5 分钟 {self._fmt_tokens(rate)} tok/min"
                )
                if today["freeTrips"] or today["freeTokens"]:
                    self.free.configure(
                        text=f"免费/零扣费 {self._fmt_tokens(today['freeTokens'])}（{today['freeShare'] * 100:.1f}%）",
                        fg=self.AMBER,
                    )
                else:
                    self.free.configure(text="免费/零扣费 0 · 全部为计费往返", fg=self.MUTED)
                self._draw_spark(snap.get("spark") or [])
        except Exception:
            logger.warning("小窗刷新失败：%s", traceback.format_exc())
            self.dot.configure(fg=self.RED)
            self.sub.configure(text="刷新失败，查看 live_server.log")
        self.root.after(1000, self._tick)

    def run(self):
        self.root.mainloop()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.root.destroy()
        except Exception:
            pass
        self.on_close()


def main(argv=None):
    global STORE
    configure_stdio()
    parser = argparse.ArgumentParser(description="Qoder CN Token / Credits 实时面板")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="转录根目录（默认 %(default)s；也可用 QODER_CN_PROJECTS 指定）",
    )
    parser.add_argument("--no-pet", action="store_true", help="只运行网页服务，不显示置顶小窗")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--snapshot", action="store_true", help="打印一次快照后退出（用于核对）")
    parser.add_argument("--force", action="store_true", help="忽略已在运行的实例，强制新开")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    args = parser.parse_args(argv)

    if args.snapshot:
        store = LiveStore(Path(args.root))
        print(json.dumps(store.snapshot(""), ensure_ascii=False, indent=2))
        return 0

    setup_logging()

    if os.environ.get("TOKEN_LIVE_SKIP_BROWSER") == "1":
        args.no_browser = True

    if not args.force:
        existing = running_instance()
        if existing:
            logger.info("已有实例在运行：%s", existing["url"])
            if not args.no_browser:
                try:
                    webbrowser.open(existing["url"])
                except Exception:
                    logger.warning("无法打开浏览器，请手动访问 %s", existing["url"])
            return 0

    if not args.no_pet:
        try:
            import tkinter  # noqa: F401
        except Exception:
            logger.warning("tkinter 不可用，改为只启动网页模式")
            args.no_pet = True

    STORE = LiveStore(Path(args.root))
    STORE.start()
    server = make_server(args.port)
    port = server.server_address[1]
    url = live_url(port)
    write_state(port)
    logger.info("实时服务已启动：%s（v%s）", url, APP_VERSION)
    if not args.no_browser:
        def open_browser():
            try:
                webbrowser.open(url)
            except Exception:
                logger.warning("无法打开浏览器，请手动访问 %s", url)

        threading.Timer(0.3, open_browser).start()

    thread = threading.Thread(target=server.serve_forever, name="live-http", daemon=True)
    thread.start()

    closed = threading.Event()

    def stop_all():
        closed.set()

    exit_code = 0
    try:
        if args.no_pet:
            print(url, flush=True)
            while not closed.wait(3600):
                pass
        else:
            pet = PetWindow(STORE, url, stop_all)
            pet.run()
            STORE.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.error("运行失败：%s", traceback.format_exc())
        exit_code = 1
    finally:
        STORE.stop()
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
        clear_state()
        logger.info("实时服务已停止")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
