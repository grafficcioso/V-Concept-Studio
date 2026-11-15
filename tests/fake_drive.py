"""테스트용 가짜 구글 드라이브 서버 (동기화에 쓰는 API만 흉내). PC 테스트와 웹 브라우저 테스트가 함께 쓴다."""
from __future__ import annotations

import http.server
import json
import re
import threading
import urllib.parse
import uuid
from email.parser import BytesParser
from email.policy import default as email_default


class FakeDrive:
    def __init__(self):
        self.files = {}          # id -> {name, mimeType, parents, description, appProperties, trashed, data, modifiedTime}
        self.lock = threading.Lock()
        self.tokens_issued = 0
        self.calls = []
        self.fail_next = None    # (method, path_regex, status)
        self.seq = 0

    def _now(self):
        self.seq += 1
        return f"2026-11-15T12:00:{self.seq:02d}.000Z"

    # q 해석: 동기화에서 쓰는 두 가지 형태만
    def query(self, q):
        out = []
        for fid, f in self.files.items():
            if f["trashed"]:
                continue
            if "vc_root" in q and f["appProperties"].get("vc_root") == "1":
                out.append((fid, f))
            m = re.search(r"'([^']+)' in parents", q)
            if m and m.group(1) in f["parents"] and f["appProperties"].get("vc_item") == "1":
                out.append((fid, f))
        return out

    def view(self, fid, f):
        return {"id": fid, "name": f["name"], "description": f.get("description", ""), "modifiedTime": f["modifiedTime"],
                "appProperties": f["appProperties"], "mimeType": f["mimeType"]}

    def start(self):
        drive = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "authorization,content-type")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,OPTIONS")

            def _send(self, status, obj=None, raw=None, ctype="application/json"):
                body = raw if raw is not None else (json.dumps(obj).encode() if obj is not None else b"")
                self.send_response(status)
                self._cors()
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors()
                self.end_headers()

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _check(self, method):
                u = urllib.parse.urlparse(self.path)
                drive.calls.append((method, u.path))
                if drive.fail_next and drive.fail_next[0] == method and re.search(drive.fail_next[1], u.path):
                    status = drive.fail_next[2]
                    drive.fail_next = None
                    self._send(status, {"error": {"message": "fake failure", "code": status}})
                    return None
                if u.path != "/token" and not (self.headers.get("Authorization") or "").startswith("Bearer "):
                    self._send(401, {"error": {"message": "no auth"}})
                    return None
                return u

            def do_GET(self):
                u = self._check("GET")
                if not u:
                    return
                qs = urllib.parse.parse_qs(u.query)
                with drive.lock:
                    m = re.fullmatch(r"/drive/v3/files/([^/]+)", u.path)
                    if m:
                        f = drive.files.get(m.group(1))
                        if not f or f["trashed"]:
                            return self._send(404, {"error": {"message": "not found"}})
                        if qs.get("alt") == ["media"]:
                            return self._send(200, raw=f["data"], ctype="image/png")
                        return self._send(200, drive.view(m.group(1), f))
                    if u.path == "/drive/v3/files":
                        items = drive.query(qs.get("q", [""])[0])
                        return self._send(200, {"files": [drive.view(i, f) for i, f in items]})
                self._send(404, {})

            def do_POST(self):
                u = self._check("POST")
                if not u:
                    return
                body = self._body()
                with drive.lock:
                    if u.path == "/token":
                        form = urllib.parse.parse_qs(body.decode())
                        drive.tokens_issued += 1
                        out = {"access_token": f"at{drive.tokens_issued}", "expires_in": 3600}
                        if form.get("grant_type") == ["authorization_code"]:
                            out["refresh_token"] = "rt_fake"
                        return self._send(200, out)
                    if u.path == "/drive/v3/files":
                        meta = json.loads(body)
                        fid = "f" + uuid.uuid4().hex[:10]
                        drive.files[fid] = {"name": meta["name"], "mimeType": meta.get("mimeType", ""),
                                            "parents": meta.get("parents", []), "appProperties": meta.get("appProperties", {}),
                                            "description": meta.get("description", ""), "trashed": False, "data": b"",
                                            "modifiedTime": drive._now()}
                        return self._send(200, {"id": fid})
                    if u.path == "/upload/drive/v3/files":
                        raw = b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body
                        msg = BytesParser(policy=email_default).parsebytes(raw)
                        parts = list(msg.iter_parts())
                        meta = json.loads(parts[0].get_payload(decode=True))
                        data = parts[1].get_payload(decode=True)
                        fid = "f" + uuid.uuid4().hex[:10]
                        drive.files[fid] = {"name": meta["name"], "mimeType": meta.get("mimeType", ""),
                                            "parents": meta.get("parents", []), "appProperties": meta.get("appProperties", {}),
                                            "description": meta.get("description", ""), "trashed": False, "data": data,
                                            "modifiedTime": drive._now()}
                        return self._send(200, {"id": fid, "modifiedTime": drive.files[fid]["modifiedTime"]})
                self._send(404, {})

            def do_PATCH(self):
                u = self._check("PATCH")
                if not u:
                    return
                body = json.loads(self._body() or b"{}")
                with drive.lock:
                    m = re.fullmatch(r"/drive/v3/files/([^/]+)", u.path)
                    f = drive.files.get(m.group(1)) if m else None
                    if not f:
                        return self._send(404, {"error": {"message": "not found"}})
                    for k in ("description", "trashed", "name"):
                        if k in body:
                            f[k] = body[k]
                    f["modifiedTime"] = drive._now()
                    return self._send(200, {"id": m.group(1)})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def stop(self):
        self.server.shutdown()

    def items(self):
        return {fid: f for fid, f in self.files.items() if not f["trashed"] and f["appProperties"].get("vc_item") == "1"}
