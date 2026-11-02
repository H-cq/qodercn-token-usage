#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨平台启动入口：python start.py [--root ...] [--no-pet] ..."""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC))

from live_server import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
