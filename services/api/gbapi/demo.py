"""`make demo`: start the API with a replay (recorded data if any, otherwise the simulator) and the web app.

- API on http://127.0.0.1:8000 (demo page at /demo, OpenAPI at /docs)
- Next.js app on http://127.0.0.1:3000 if apps/web has node_modules (started with `npm run dev`)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import uvicorn
from gbcore.settings import REPO_ROOT, get_settings


def main() -> int:
    raw = get_settings().lake_dir / "raw" / "VehiclePositions"
    has_recorded = raw.exists() and any(raw.rglob("*.pb.gz"))
    os.environ.setdefault("PIPELINE", "replay" if has_recorded else "sim")
    os.environ.setdefault("REPLAY_SPEED", "10")
    print(f"PIPELINE={os.environ['PIPELINE']} REPLAY_SPEED={os.environ['REPLAY_SPEED']}")
    web = REPO_ROOT / "apps" / "web"
    proc = None
    npm = shutil.which("npm")
    if npm and (web / "node_modules").exists() and "--no-web" not in sys.argv:
        port = os.environ.get("WEB_PORT", "3100")
        built = (web / ".next" / "BUILD_ID").exists()
        cmd = [npm, "run", "start" if built else "dev", "--", "-p", port]
        proc = subprocess.Popen(cmd, cwd=web, env={**os.environ, "NEXT_PUBLIC_API": "http://127.0.0.1:8000"})
        print(f"web app: http://127.0.0.1:{port}")
    print("api: http://127.0.0.1:8000  demo page: http://127.0.0.1:8000/demo")
    try:
        uvicorn.run("gbapi.app:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), access_log=False)
    finally:
        if proc:
            proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
