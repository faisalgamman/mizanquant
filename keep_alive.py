import threading, requests, time, os

def ping():
    url = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    if not url: return
    while True:
        try:
            requests.get(f"https://{url}/health", timeout=10)
        except: pass
        time.sleep(240)

threading.Thread(target=ping, daemon=True).start()