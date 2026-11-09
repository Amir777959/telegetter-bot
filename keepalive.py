# -*- coding: utf-8 -*-
"""
سرور کوچیک پینگ سلامت (فقط با کتابخانه استاندارد پایتون)

سرورهای رایگان مثل Render، Koyeb و Hugging Face Space انتظار دارن
پروسه‌ات روی یه پورت HTTP گوش بده؛ این سرور اون کار رو انجام میده و
همزمان با سرویس‌هایی مثل UptimeRobot (پینگ هر ۵ دقیقه) باعث میشه
سرویس رایگان خوابیده نشه.
"""

import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("keepalive")

_server = None


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            body = "✅ ربات بازنشر فعال است"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
        except Exception:
            pass

    def log_message(self, *args, **kwargs):
        pass  # پینگ‌ها رو لاگ نکن


def start_keepalive(port):
    """سرور HTTP را در یک ترد پس‌زمینه روشن می‌کند"""
    global _server
    if _server is not None:
        return _server
    try:
        _server = ThreadingHTTPServer(("0.0.0.0", int(port)), _Handler)
    except OSError as e:
        log.warning("Keepalive server could not start on port %s: %s", port, e)
        return None
    t = _server.serve_forever
    import threading
    threading.Thread(target=t, daemon=True, name="keepalive").start()
    log.info("Keepalive server started on port %s", port)
    return _server
