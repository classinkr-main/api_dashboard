#!/usr/bin/env python3
"""Replay sample (or captured) webhook payloads against a running dashboard.

Usage:
    python samples/replay.py http://127.0.0.1:8100 samples/*.json
"""

import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    base = sys.argv[1].rstrip("/")
    for path in sys.argv[2:]:
        body = Path(path).read_bytes()
        json.loads(body)  # validate before sending
        req = Request(
            f"{base}/webhook/classin", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urlopen(req) as resp:
            print(f"{path}: {resp.status} {resp.read().decode()[:120]}")


if __name__ == "__main__":
    main()
