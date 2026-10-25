"""Dogfood SSE test: hit /api/conversations/{id}/ask and /api/projects/{id}/handoffs/stream-generate."""
import base64
import json
import os
import sys
import time
import urllib.request
import urllib.error
import http.client

BASE = "http://127.0.0.1:8000"
# P0 修复（DRL R1b class-level）：移除硬编码 admin:admin123，改环境变量注入 + fail-fast
_ADMIN_PASS = os.getenv("BRIDGE_ADMIN_PASS")
if not _ADMIN_PASS:
    sys.stderr.write(
        "[FATAL] BRIDGE_ADMIN_PASS 环境变量未设置。\n"
        "        用法: BRIDGE_ADMIN_PASS=<your-pass> python dogfood_sse_test.py\n"
    )
    sys.exit(2)
AUTH = "Basic " + base64.b64encode(f"admin:{_ADMIN_PASS}".encode()).decode()


def get_csrf_and_cookie():
    """Hit a GET to obtain CSRF cookie + token."""
    req = urllib.request.Request(f"{BASE}/demo", headers={"Authorization": AUTH})
    with urllib.request.urlopen(req, timeout=5) as resp:
        cookie = resp.headers.get("Set-Cookie", "")
        # extract bridge_csrf=VALUE
        token = ""
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("bridge_csrf="):
                token = part[len("bridge_csrf="):]
                break
        return token, cookie


def post_sse(path, body, csrf_token, cookie):
    """POST to an SSE endpoint; read stream; return list of events."""
    conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=60)
    headers = {
        "Authorization": AUTH,
        "X-Requested-With": "BridgeAdmin",
        "X-CSRF-Token": csrf_token,
        "Content-Type": "application/json",
        "Cookie": f"bridge_csrf={csrf_token}",
        "Accept": "text/event-stream",
    }
    conn.request("POST", path, body=body, headers=headers)
    resp = conn.getresponse()
    print(f"  status: {resp.status}")
    print(f"  content-type: {resp.headers.get('content-type')}")
    print(f"  cache-control: {resp.headers.get('cache-control')}")

    if resp.status != 200:
        body = resp.read().decode("utf-8", errors="replace")
        print(f"  ERROR body: {body[:500]}")
        return [], resp.status

    events = []
    buf = ""
    start = time.time()
    while True:
        chunk = resp.read(4096)
        if not chunk:
            break
        buf += chunk.decode("utf-8", errors="replace")
        while "\n\n" in buf:
            event_str, buf = buf.split("\n\n", 1)
            ev = {"raw": event_str}
            for line in event_str.split("\n"):
                if line.startswith("event:"):
                    ev["event"] = line[6:].strip()
                elif line.startswith("data:"):
                    ev["data"] = line[5:].strip()
            events.append(ev)
            # safety cap
            if time.time() - start > 60:
                print("  [timeout 60s]")
                break
        if time.time() - start > 60:
            break
    conn.close()
    return events, 200


def main():
    csrf, _ = get_csrf_and_cookie()
    print(f"CSRF token: {csrf[:20]}...")

    # Test 1: ask SSE
    print("\n=== Test 1: /api/conversations/1/ask (SSE) ===")
    body = json.dumps({"question": "项目当前状态如何？"})
    events, status = post_sse("/api/conversations/1/ask", body, csrf, "")
    print(f"  total events: {len(events)}")
    delta_count = sum(1 for e in events if e.get("event") == "delta")
    done_count = sum(1 for e in events if e.get("event") == "done")
    err_count = sum(1 for e in events if e.get("event") == "error")
    print(f"  delta events: {delta_count}")
    print(f"  done events: {done_count}")
    print(f"  error events: {err_count}")
    # show first 3 delta + last 2 events
    deltas_shown = 0
    for e in events:
        if e.get("event") == "delta" and deltas_shown < 3:
            data = e.get("data", "")
            try:
                d = json.loads(data)
                snippet = d.get("delta", "")[:120]
                print(f"  [delta] (fallback={d.get('fallback', False)}): {snippet}")
            except Exception:
                print(f"  [delta] raw: {data[:120]}")
            deltas_shown += 1
    for e in events[-2:]:
        print(f"  [last] event={e.get('event')} data={e.get('data', '')[:200]}")

    # Test 2: handoff stream-generate SSE
    print("\n=== Test 2: /api/projects/1/handoffs/stream-generate (SSE) ===")
    body2 = json.dumps({})
    events2, status2 = post_sse("/api/projects/1/handoffs/stream-generate", body2, csrf, "")
    print(f"  total events: {len(events2)}")
    delta_count2 = sum(1 for e in events2 if e.get("event") == "delta")
    done_count2 = sum(1 for e in events2 if e.get("event") == "done")
    err_count2 = sum(1 for e in events2 if e.get("event") == "error")
    print(f"  delta events: {delta_count2}")
    print(f"  done events: {done_count2}")
    print(f"  error events: {err_count2}")
    if events2:
        # show first delta (may be long, fallback)
        for e in events2[:1]:
            data = e.get("data", "")
            try:
                d = json.loads(data)
                snippet = d.get("delta", "")[:400]
                print(f"  [first delta] (fallback={d.get('fallback', False)}): {snippet}")
            except Exception:
                print(f"  [first delta] raw: {data[:300]}")
        for e in events2[-2:]:
            print(f"  [last] event={e.get('event')} data={e.get('data', '')[:200]}")


if __name__ == "__main__":
    main()
