import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cn_usage_core import TZ  # noqa: E402
from live_server import LiveStore  # noqa: E402

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=TZ)


def make_line(ts, ratio=0.1, credits=1.0, model="test-model", cwd="/work/demo"):
    payload = {
        "type": "assistant",
        "uuid": "00000000-0000-0000-0000-000000000000",
        "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
        "message": {
            "role": "assistant",
            "model": model,
            "usage": {
                "context_usage_ratio": ratio,
                "credits": credits,
                "original_credits": credits,
            },
        },
        "cwd": cwd,
    }
    return json.dumps(payload, ensure_ascii=False)


def write_jsonl(path, lines, newline=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    if newline:
        text += "\n"
    path.write_text(text, encoding="utf-8")


class TempRootCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "projects"
        self.project = self.root / "demo-project"
        self.session = self.project / "sess-main.jsonl"
        self.project.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def store(self):
        return LiveStore(self.root)


class InitialScanTests(TempRootCase):
    def test_initial_scan_and_append(self):
        write_jsonl(self.session, [make_line(NOW - timedelta(minutes=2))])
        store = self.store()
        self.assertEqual(store.snapshot("", now=NOW)["files"], 1)
        self.assertEqual(len(store.records), 1)

        with self.session.open("a", encoding="utf-8") as fh:
            fh.write(make_line(NOW - timedelta(minutes=1), ratio=0.2) + "\n")
        store.poll_once()
        self.assertEqual(len(store.records), 2)
        self.assertEqual(store.records[-1]["ratio"], 0.2)

    def test_final_line_without_newline_is_counted(self):
        write_jsonl(self.session, [make_line(NOW)], newline=False)
        store = self.store()
        self.assertEqual(len(store.records), 1)

    def test_half_written_line_waits_for_completion(self):
        write_jsonl(self.session, [make_line(NOW)], newline=False)
        store = self.store()
        with self.session.open("a", encoding="utf-8") as fh:
            fh.write(make_line(NOW, ratio=0.2)[:40])
        store.poll_once()
        self.assertEqual(len(store.records), 1)

        with self.session.open("a", encoding="utf-8") as fh:
            fh.write(make_line(NOW, ratio=0.2)[40:] + "\n")
        store.poll_once()
        self.assertEqual(len(store.records), 2)

    def test_truncated_file_is_rebuilt(self):
        write_jsonl(self.session, [make_line(NOW - timedelta(minutes=2)), make_line(NOW - timedelta(minutes=1))])
        store = self.store()
        self.assertEqual(len(store.records), 2)

        write_jsonl(self.session, [make_line(NOW)])
        store.poll_once()
        self.assertEqual(len(store.records), 1)

    def test_new_subagent_file_is_discovered(self):
        write_jsonl(self.session, [make_line(NOW - timedelta(minutes=1))])
        store = self.store()
        sub = self.project / "sess-main" / "subagents" / "agent-1.jsonl"
        write_jsonl(sub, [make_line(NOW, ratio=0.3, credits=0.5)])
        store._last_discovery = -1e9
        store.poll_once()
        subs = [r for r in store.records if r["sub"]]
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["session"], "sess-main")
        self.assertEqual(subs[0]["ratio"], 0.3)


class SnapshotTests(TempRootCase):
    def test_snapshot_follow_and_pin(self):
        old_session = self.project / "sess-old.jsonl"
        write_jsonl(old_session, [make_line(NOW - timedelta(days=1), ratio=0.1, credits=1.0)])
        write_jsonl(
            self.session,
            [
                make_line(NOW - timedelta(seconds=100), ratio=0.2, credits=2.0),
                make_line(NOW, ratio=0.4, credits=4.0),
            ],
        )
        store = self.store()
        snap = store.snapshot("", now=NOW)

        self.assertTrue(snap["sourceExists"])
        self.assertEqual(snap["records"], 3)
        self.assertEqual(snap["selectedSession"], "sess-main")
        self.assertTrue(snap["followed"])
        self.assertEqual(snap["session"]["trips"], 2)
        self.assertEqual(snap["session"]["tokens"], 120000)
        self.assertEqual(snap["session"]["credits"], 6.0)
        self.assertEqual(snap["today"]["trips"], 2)
        self.assertEqual(snap["spark"][-1]["trips"], 1)
        self.assertEqual(snap["session"]["rate1m"]["trips"], 1)
        self.assertEqual(snap["session"]["rate5m"]["trips"], 2)

        pinned = store.snapshot("sess-old", now=NOW)
        self.assertEqual(pinned["selectedSession"], "sess-old")
        self.assertFalse(pinned["followed"])
        self.assertEqual(pinned["session"]["trips"], 1)
        self.assertEqual(pinned["today"]["trips"], 2)
        self.assertTrue(any(row["selected"] for row in pinned["sessions"]))

    def test_missing_root_still_returns_a_snapshot(self):
        store = LiveStore(self.root / "missing")
        snap = store.snapshot("", now=NOW)
        self.assertFalse(snap["sourceExists"])
        self.assertEqual(snap["records"], 0)
        self.assertIsNone(snap["session"])
        self.assertIsNone(snap["today"])


if __name__ == "__main__":
    unittest.main()
