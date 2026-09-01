"""容器 HEALTHCHECK：探测本进程公开站点信息接口（无需鉴权、依赖仅 SQLite）。"""

import os
import sys
import urllib.request

port = os.environ.get("PORT", "8000")
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/system/site", timeout=4) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
