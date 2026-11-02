#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qoder CN 桌面端转录的扫描与聚合核心，静态快照与实时面板共用。

CN 版转录里只有 context_usage_ratio 与 credits，没有 input/output/cached
tokens，因此 tokens 按 ratio x 200000 推导，credits 为实测值。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".qoder-cn" / "projects"
ROOT = Path(os.environ.get("QODER_CN_PROJECTS") or DEFAULT_ROOT).expanduser()

TZ = timezone(timedelta(hours=8))

# 上下文窗口是推导 Tokens 的固定假设；不同模型窗口不同，可用环境变量覆盖。
try:
    WINDOW = int(os.environ.get("QODER_CN_WINDOW") or 200_000)
except ValueError:
    WINDOW = 200_000
if WINDOW <= 0:
    WINDOW = 200_000

RANGES = [("7", "近 7 天", 7), ("30", "近 30 天", 30), ("90", "近 90 天", 90), ("all", "全部历史", 0)]


def project_label(cwd: str) -> str:
    raw = (cwd or "").strip()
    normalized = raw.replace("\\", "/").rstrip("/")
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return "(未知工作区)"
    for i in range(len(parts) - 1):
        if parts[i] == "Documents" and parts[i + 1] == "Qoder":
            tail = parts[i + 2:]
            if tail:
                return f"Qoder 临时会话 {tail[0]}"
            return "Qoder 临时会话"
    return parts[-1]


def is_free(record) -> bool:
    return float(record.get("credits") or 0) <= 0


def parse_line(line, session: str, is_sub: bool):
    if isinstance(line, (bytes, bytearray)):
        line = line.decode("utf-8", "ignore")
    if '"context_usage_ratio"' not in line:
        return None
    try:
        d = json.loads(line)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    m = d.get("message")
    if not isinstance(m, dict):
        return None
    u = m.get("usage")
    if not isinstance(u, dict):
        return None
    try:
        ratio = float(u.get("context_usage_ratio") or 0)
        credits = float(u.get("credits") or 0)
        orig_credits = float(u.get("original_credits") or 0)
    except (TypeError, ValueError):
        return None
    if ratio <= 0:
        return None
    try:
        t = datetime.fromisoformat(str(d.get("timestamp")).replace("Z", "+00:00")).astimezone(TZ)
    except Exception:
        return None
    cwd = str(d.get("cwd") or "(未知工作区)")
    return {
        "ts": t,
        "date": t.strftime("%Y-%m-%d"),
        "session": session,
        "model": str(m.get("model") or "(未记录)"),
        "credits": credits,
        "origCredits": orig_credits,
        "ratio": ratio,
        "project": cwd,
        "label": project_label(cwd),
        "sub": is_sub,
        "free": credits <= 0,
    }


def scan_all(root: Path = ROOT):
    records = []
    files = 0
    for f in root.rglob("*.jsonl"):
        if not f.is_file():
            continue
        files += 1
        is_sub = "subagents" in f.parts
        session = f.parent.parent.name if is_sub else f.stem
        try:
            with f.open(encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    rec = parse_line(line, session, is_sub)
                    if rec:
                        records.append(rec)
        except OSError:
            continue
    records.sort(key=lambda r: r["ts"])
    return files, records


def summarize(records) -> dict:
    tokens = round(sum(r["ratio"] for r in records) * WINDOW)
    sub_tokens = round(sum(r["ratio"] for r in records if r["sub"]) * WINDOW)
    free_tokens = round(sum(r["ratio"] for r in records if r["free"]) * WINDOW)
    days = len({r["date"] for r in records})
    return {
        "trips": len(records),
        "subTrips": sum(1 for r in records if r["sub"]),
        "freeTrips": sum(1 for r in records if r["free"]),
        "sessions": len({r["session"] for r in records}),
        "projects": len({r["project"] for r in records}),
        "days": days,
        "tokens": tokens,
        "subTokens": sub_tokens,
        "subShare": sub_tokens / tokens if tokens else 0,
        "freeTokens": free_tokens,
        "freeShare": free_tokens / tokens if tokens else 0,
        "credits": round(sum(r["credits"] for r in records), 3),
        "origCredits": round(sum(r["origCredits"] for r in records), 3),
        "avgTrip": tokens / len(records) if records else 0,
        "avgDay": tokens / days if days else 0,
    }


def rows_for(records, key_fn, label_fn):
    buckets = {}
    for r in records:
        k = key_fn(r)
        b = buckets.get(k)
        if b is None:
            b = buckets[k] = {
                "key": k, "label": label_fn(r), "trips": 0, "subTrips": 0,
                "freeTrips": 0, "sessions": set(), "ratio": 0.0, "subRatio": 0.0,
                "freeRatio": 0.0, "credits": 0.0,
            }
        b["trips"] += 1
        b["sessions"].add(r["session"])
        b["ratio"] += r["ratio"]
        b["credits"] += r["credits"]
        if r["sub"]:
            b["subTrips"] += 1
            b["subRatio"] += r["ratio"]
        if r["free"]:
            b["freeTrips"] += 1
            b["freeRatio"] += r["ratio"]
    out = []
    for b in buckets.values():
        tokens = round(b["ratio"] * WINDOW)
        out.append({
            "key": b["key"],
            "label": b["label"],
            "trips": b["trips"],
            "subTrips": b["subTrips"],
            "freeTrips": b["freeTrips"],
            "sessions": len(b["sessions"]),
            "tokens": tokens,
            "subTokens": round(b["subRatio"] * WINDOW),
            "freeTokens": round(b["freeRatio"] * WINDOW),
            "credits": round(b["credits"], 3),
        })
    return out


def build_range(records, rid, label):
    total_ratio = sum(r["ratio"] for r in records)
    tokens = round(total_ratio * WINDOW)
    day = rows_for(records, lambda r: r["date"], lambda r: r["date"])
    day.sort(key=lambda x: x["key"])
    model = rows_for(records, lambda r: r["model"], lambda r: r["model"])
    model.sort(key=lambda x: -x["tokens"])
    project = rows_for(records, lambda r: r["project"], lambda r: r["label"])
    project.sort(key=lambda x: -x["tokens"])
    for rows in (day, model, project):
        for row in rows:
            row["share"] = row["tokens"] / tokens if tokens else 0
    return {
        "id": rid,
        "label": label,
        "summary": summarize(records),
        "day": day,
        "model": model,
        "project": project,
    }


def range_records(records, days, now=None):
    now = now or datetime.now(TZ)
    if not days:
        return records
    since = datetime.combine(now.date() - timedelta(days=days - 1), dtime.min, TZ)
    return [r for r in records if r["ts"] >= since]


def compact_tokens(n) -> str:
    n = n or 0
    if n >= 1e8:
        return f"{n / 1e8:.2f}".rstrip("0").rstrip(".") + " 亿"
    if n >= 1e4:
        return f"{n / 1e4:.1f}".rstrip("0").rstrip(".") + " 万"
    return f"{round(n):,}"
