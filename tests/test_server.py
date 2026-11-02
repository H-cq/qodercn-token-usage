import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import live_server  # noqa: E402
from cn_usage_core import TZ  # noqa: E402
from live_server import Handler, LiveStore  # noqa: E402

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=TZ)


def make_line():
    payload = {
        "type": "assistant",
        "uuid": "00000000-0000-0000-0000-000000000000",
        "timestamp": NOW.isoformat(),
        "message": {
            "role": "assistant",
            "model": "test-model",
            "usage": {"context_usage_ratio": 0.1, "credits": 1.0, "original_credits": 1.0},
        },
        "cwd": "/work/demo",
    }
    return json.dumps(payload, ensure_ascii=False) + "\n"


class ServerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "projects"
        session = root / "demo" / "sess-1.jsonl"
        session.parent.mkdir(parents=True)
        session.write_text(make_line(), encoding="utf-8")

        self.store = LiveStore(root)
        self.old_store = live_server.STORE
        self.old_history = live_server.HISTORY
        live_server.STORE = self.store
        live_server.HISTORY = Path(self._tmp.name) / "missing-history.html"

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        live_server.STORE = self.old_store
        live_server.HISTORY = self.old_history
        self._tmp.cleanup()

    def get(self, path):
        return urllib.request.urlopen(self.base + path, timeout=3)

    def test_ping(self):
        with self.get("/api/ping") as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["version"], live_server.APP_VERSION)
        self.assertEqual(data["title"], live_server.APP_TITLE)

    def test_live_api(self):
        with self.get("/api/live") as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(data["files"], 1)
        self.assertEqual(data["records"], 1)
        self.assertTrue(data["sourceExists"])
        self.assertEqual(data["session"]["trips"], 1)

    def test_live_page(self):
        with self.get("/") as resp:
            body = resp.read().decode("utf-8")
        self.assertIn("Qoder Token 实时消耗", body)

    def test_missing_history_page(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/history")
        self.assertEqual(ctx.exception.code, 404)
        with ctx.exception as exc:
            body = exc.read().decode("utf-8")
        self.assertIn("历史仪表盘尚未生成", body)
        self.assertIn("build_cn_token_dashboard.py", body)

    def test_favicon(self):
        with self.get("/favicon.ico") as resp:
            self.assertEqual(resp.status, 204)


if __name__ == "__main__":
    unittest.main()
