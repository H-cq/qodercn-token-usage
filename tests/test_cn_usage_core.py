import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cn_usage_core import (  # noqa: E402
    TZ,
    build_range,
    compact_tokens,
    parse_line,
    project_label,
    range_records,
    rows_for,
    summarize,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=TZ)
CWD = "/work/demo"


def make_line(ts, ratio=0.1, credits=1.0, model="test-model", cwd=CWD, orig=None):
    payload = {
        "type": "assistant",
        "uuid": "00000000-0000-0000-0000-000000000000",
        "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
        "message": {
            "type": "message",
            "role": "assistant",
            "model": model,
            "usage": {
                "context_usage_ratio": ratio,
                "credits": credits,
                "original_credits": credits if orig is None else orig,
            },
        },
        "cwd": cwd,
    }
    return json.dumps(payload, ensure_ascii=False) + "\n"


def parse(ts, **kwargs):
    return parse_line(make_line(ts, **kwargs), "sess-1", False)


class ParseLineTests(unittest.TestCase):
    def test_valid_record(self):
        rec = parse(NOW, ratio=0.25, credits=2.5, orig=3.0, model="m1", cwd="/work/示例")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["date"], "2026-11-02")
        self.assertEqual(rec["model"], "m1")
        self.assertEqual(rec["label"], "示例")
        self.assertEqual(rec["ratio"], 0.25)
        self.assertEqual(rec["credits"], 2.5)
        self.assertEqual(rec["origCredits"], 3.0)
        self.assertFalse(rec["free"])

    def test_accepts_bytes(self):
        rec = parse_line(make_line(NOW).encode("utf-8"), "sess-1", True)
        self.assertTrue(rec["sub"])

    def test_rejects_missing_or_bad_usage(self):
        no_usage = json.dumps({"timestamp": NOW.isoformat(), "message": {"model": "m"}})
        self.assertIsNone(parse_line(no_usage, "s", False))
        self.assertIsNone(parse_line(make_line(NOW, ratio=0), "s", False))
        self.assertIsNone(parse_line(make_line(NOW, ratio="bad"), "s", False))
        self.assertIsNone(parse_line("not json", "s", False))
        self.assertIsNone(parse_line("{}", "s", False))

    def test_rejects_bad_timestamp(self):
        self.assertIsNone(parse_line(make_line("not-a-timestamp"), "s", False))

    def test_zulu_timestamp_converts_to_utc8(self):
        rec = parse_line(make_line("2026-11-02T04:00:00Z"), "s", False)
        self.assertEqual(rec["date"], "2026-11-02")
        self.assertEqual(rec["ts"].hour, 12)

    def test_zero_credits_is_free(self):
        rec = parse(NOW, credits=0)
        self.assertTrue(rec["free"])


class ProjectLabelTests(unittest.TestCase):
    def test_qoder_temp_session_uses_first_tail_segment(self):
        self.assertEqual(
            project_label(r"C:\Users\demo\Documents\Qoder\2026-09-29\abc123"),
            "Qoder 临时会话 2026-09-29",
        )

    def test_windows_and_posix_paths(self):
        self.assertEqual(project_label(r"D:\work\示例项目"), "示例项目")
        self.assertEqual(project_label("/home/demo/project"), "project")
        self.assertEqual(project_label(r"C:\Users\demo\Documents\Qoder\direct"), "Qoder 临时会话 direct")

    def test_empty_path(self):
        self.assertEqual(project_label(""), "(未知工作区)")


class SummarizeTests(unittest.TestCase):
    def records(self):
        return [
            parse(NOW, ratio=0.1, credits=1.0),
            parse(NOW, ratio=0.2, credits=0.0, cwd="/work/free"),
            parse(NOW, ratio=0.3, credits=2.0, cwd="/work/sub"),
        ]

    def test_totals_and_shares(self):
        records = self.records()
        records[2]["sub"] = True
        s = summarize(records)
        self.assertEqual(s["trips"], 3)
        self.assertEqual(s["subTrips"], 1)
        self.assertEqual(s["freeTrips"], 1)
        self.assertEqual(s["tokens"], 120000)
        self.assertEqual(s["subTokens"], 60000)
        self.assertEqual(s["freeTokens"], 40000)
        self.assertAlmostEqual(s["subShare"], 0.5)
        self.assertAlmostEqual(s["freeShare"], 1 / 3)
        self.assertEqual(s["credits"], 3.0)
        self.assertEqual(s["sessions"], 1)
        self.assertEqual(s["projects"], 3)

    def test_rows_for_and_build_range(self):
        records = self.records()
        rows = rows_for(records, lambda r: r["date"], lambda r: r["date"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trips"], 3)
        self.assertEqual(rows[0]["tokens"], 120000)
        built = build_range(records, "d", "测试")
        self.assertEqual(built["summary"]["tokens"], 120000)
        self.assertAlmostEqual(built["day"][0]["share"], 1.0)
        self.assertEqual(built["model"][0]["share"], 1.0)


class RangeTests(unittest.TestCase):
    def test_range_records_uses_start_of_day(self):
        records = [
            parse(NOW - timedelta(days=8)),
            parse(NOW - timedelta(days=6)),
            parse(NOW - timedelta(hours=1)),
            parse(NOW),
        ]
        subset = range_records(records, 7, now=NOW)
        self.assertEqual(len(subset), 3)
        self.assertEqual(len(range_records(records, 0, now=NOW)), 4)

    def test_compact_tokens(self):
        self.assertEqual(compact_tokens(123), "123")
        self.assertEqual(compact_tokens(12345), "1.2 万")
        self.assertEqual(compact_tokens(123456789), "1.23 亿")


if __name__ == "__main__":
    unittest.main()
