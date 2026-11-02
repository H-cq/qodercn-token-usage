#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 Qoder CN 桌面端会话转录生成 token 用量仪表盘。

CN 版桌面端没有 token-usage 插件所需的 local.db，转录里只有
context_usage_ratio 与 credits，没有 input/output/cached tokens。
因此 tokens 按 ratio x 200000 推导，credits 为实测值。
"""
from __future__ import annotations

import argparse
import json
import time as time_mod
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

from cn_usage_core import ROOT, RANGES, TZ, WINDOW, build_range, scan_all

SRC = Path(__file__).resolve().parent
PROJECT = SRC.parent
DIST = PROJECT / "dist"


def scan(root=ROOT):
    return scan_all(Path(root).expanduser())


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成 Qoder CN Token 用量静态仪表盘")
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="转录根目录（默认 %(default)s；也可用 QODER_CN_PROJECTS 指定）",
    )
    args = parser.parse_args(argv)

    t0 = time_mod.time()
    root = Path(args.root).expanduser()
    files, records = scan(root)
    if not records:
        raise SystemExit(f"没有在 {root} 扫描到 usage 记录")
    now = datetime.now(TZ)

    ranges = []
    for rid, label, days in RANGES:
        if days:
            since = datetime.combine(now.date() - timedelta(days=days - 1), dtime.min, TZ)
            subset = [r for r in records if r["ts"] >= since]
        else:
            subset = records
        ranges.append(build_range(subset, rid, label))

    for rng in ranges:
        s = rng["summary"]
        assert s["trips"] == sum(d["trips"] for d in rng["day"]), rng["id"]
        assert abs(sum(d["tokens"] for d in rng["day"]) - s["tokens"]) <= len(rng["day"]), rng["id"]
        assert s["subTokens"] <= s["tokens"], rng["id"]

    all_sum = ranges[-1]["summary"]
    all_trips = sum(r["trips"] for r in ranges[-1]["day"])
    meta = {
        "files": files,
        "records": len(records),
        "sessions": all_sum["sessions"],
        "projects": all_sum["projects"],
        "since": min(r["date"] for r in records),
        "until": max(r["date"] for r in records),
        "window": WINDOW,
        "subTrips": all_sum["subTrips"],
        "subTokens": all_sum["subTokens"],
        "source": str(root),
    }
    footnotes = [
        f"数据源：{root} 下 {files} 个 .jsonl 会话转录（含 subagents），只读扫描，不上传任何数据。",
        f"Tokens 为推导值：每次模型往返取 usage.context_usage_ratio × {WINDOW:,}；模型实际窗口若不同，绝对值会偏差，占比相对稳定。",
        "Credits 为转录中 usage.credits 的实测值；原始 Credits 为折扣前数值，官方 UI 以 Credits 计费口径为准。",
        "CN 桌面端转录不记录 output_tokens / cached_tokens，故输出与缓存命中无法统计，也不显示为 0。",
        "子代理往返来自 <session>/subagents/*.jsonl，已计入总量，并在每日图中单独着色。",
        "对账建议：同一时刻将本页的往返次数、Tokens、Credits 与官方 UI 或 Credits Inspector 对照；差异通常来自统计时点、折扣和免费记录口径。",
        "数据为生成时快照；新增对话后重新运行 src/build_cn_token_dashboard.py 刷新。",
    ]
    payload = {
        "generatedAt": now.strftime("%Y-%m-%d %H:%M"),
        "meta": meta,
        "ranges": ranges,
        "footnotes": footnotes,
    }

    DIST.mkdir(parents=True, exist_ok=True)
    tpl = (SRC / "cn_token_template.html").read_text(encoding="utf-8")
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = tpl.replace("__DATA__", data_json)
    history_out = DIST / "Qoder_CN_Token用量统计.html"
    history_out.write_text(html, encoding="utf-8")
    (DIST / "cn_token_dashboard.html").write_text(html, encoding="utf-8")
    (DIST / "cn_token_dashboard.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    canvas_ranges = []
    for rng in payload["ranges"]:
        canvas_ranges.append({
            "id": rng["id"],
            "label": rng["label"],
            "summary": rng["summary"],
            "days": [{
                "label": d["key"][5:],
                "main": d["tokens"] - d["subTokens"],
                "sub": d["subTokens"],
                "trips": d["trips"],
                "sessions": d["sessions"],
                "tokens": d["tokens"],
                "credits": d["credits"],
                "share": d["share"],
            } for d in rng["day"]],
            "models": [{
                "name": x["label"], "trips": x["trips"], "sessions": x["sessions"],
                "tokens": x["tokens"], "credits": x["credits"], "share": x["share"],
            } for x in rng["model"]],
            "projects": [{
                "name": x["label"], "trips": x["trips"], "sessions": x["sessions"],
                "tokens": x["tokens"], "credits": x["credits"], "share": x["share"],
            } for x in rng["project"]],
        })
    canvas_data = {
        "generatedAt": payload["generatedAt"],
        "meta": payload["meta"],
        "ranges": canvas_ranges,
        "footnotes": payload["footnotes"],
    }
    canvas_tpl = (SRC / "cn_canvas_template.tsx").read_text(encoding="utf-8")
    canvas_json = json.dumps(canvas_data, ensure_ascii=False).replace("</", "<\\/")
    canvas_tsx = canvas_tpl.replace("__DATA__", canvas_json)
    canvas_dir = PROJECT / ".qoder-token-usage"
    canvas_dir.mkdir(exist_ok=True)
    canvas_out = canvas_dir / "cn-token-dashboard.canvas.tsx"
    canvas_out.write_text(canvas_tsx, encoding="utf-8")
    (DIST / "Qoder_CN_Token用量统计.canvas.tsx").write_text(canvas_tsx, encoding="utf-8")

    latest_sid = records[-1]["session"]
    cur = [r for r in records if r["session"] == latest_sid]
    cur_credits = sum(r["credits"] for r in cur)
    cur_tokens = round(sum(r["ratio"] for r in cur) * WINDOW)
    print(f"[scan] files={files} records={len(records)} range={meta['since']}..{meta['until']} ({time_mod.time()-t0:.1f}s)")
    print(f"[all] tokens={all_sum['tokens']:,} credits={all_sum['credits']:,.1f} "
          f"sessions={all_sum['sessions']} projects={all_sum['projects']} days={all_sum['days']}")
    print(f"[7d]  tokens={ranges[0]['summary']['tokens']:,} credits={ranges[0]['summary']['credits']:,.1f} "
          f"trips={ranges[0]['summary']['trips']:,}")
    print(f"[check] latest session {latest_sid[:8]} tokens={cur_tokens:,} credits={cur_credits:,.3f} trips={len(cur)}")
    print(f"HTML  {history_out}")
    print(f"JSON  {DIST / 'cn_token_dashboard.json'}")
    print(f"CANVAS {canvas_out}")
    print(f"CANVAS {DIST / 'Qoder_CN_Token用量统计.canvas.tsx'}")


if __name__ == "__main__":
    main()
