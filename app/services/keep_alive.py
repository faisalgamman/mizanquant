"""Keep-alive ping for Railway's health checker.

Railway's built-in health checker already monitors the /health endpoint,
so this module is a no-op. It exists only for backward compatibility
with imports in older code.
"""

import os

# Only start the keep-alive thread if explicitly enabled via env var.
# Railway's own health checker makes this unnecessary.
if os.environ.get("ENABLE_KEEPALIVE_PING", "").lower() in ("1", "true", "yes"):
    import threading
    import requests
    import time

    def _ping():
        url = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
        if not url:
            return
        while True:
            try:
                requests.get(f"https://{url}/health", timeout=10)
            except Exception:
                pass
            time.sleep(240)

    threading.Thread(target=_ping, daemon=True).start()
