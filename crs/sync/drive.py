"""구글 드라이브 REST 클라이언트 + PC용 구글 로그인(OAuth 데스크톱 앱, PKCE, 루프백).

외부 라이브러리 없이 urllib 만 쓴다. 토큰은 Windows 자격 증명 관리자(keyring)에만 저장한다.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser

from . import FOLDER_NAME, SCOPE

log = logging.getLogger(__name__)
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"


class DriveError(Exception):
    def __init__(self, message, status=0, auth=False):
        super().__init__(message)
        self.status = status
        self.auth = auth


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _request(method, url, *, headers=None, data=None, timeout=60):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            msg = json.loads(body).get("error", {})
            msg = msg.get("message") if isinstance(msg, dict) else (msg or body[:200].decode("utf-8", "replace"))
        except Exception:
            msg = body[:200].decode("utf-8", "replace")
        raise DriveError(f"구글 드라이브 오류({e.code}): {msg}", e.code, auth=e.code in (400, 401) and "token" in url
                         or e.code == 401) from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise DriveError("인터넷 연결 또는 구글 접속을 확인하세요.") from e


# ---------------------------------------------------------------- OAuth (PC)
class GoogleAuth:
    """refresh token 은 저장소 함수(get/set)로 주고받는다 → 키 저장소에만 보관."""

    def __init__(self, client_id: str, client_secret: str, load_refresh, save_refresh, token_url=TOKEN_URL):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self._load = load_refresh
        self._save = save_refresh
        self.token_url = token_url
        self._access = None
        self._expires = 0.0

    def is_connected(self) -> bool:
        return bool(self._load())

    def sign_in(self, open_browser=webbrowser.open, timeout=300) -> None:
        """브라우저로 구글 로그인 → 이 PC 의 임시 주소(127.0.0.1)로 돌아온 코드를 토큰으로 바꾼다."""
        if not self.client_id or not self.client_secret:
            raise DriveError("구글 클라이언트 ID 와 보안 비밀번호를 먼저 넣어 주세요.")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        result = {}
        done = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if "code" in q or "error" in q:
                    result.update({k: v[0] for k, v in q.items()})
                    ok = "code" in q and q.get("state", [""])[0] == state
                    msg = ("V-Concept Studio 구글 연결이 끝났습니다. 이 창을 닫고 프로그램으로 돌아가세요." if ok
                           else "연결하지 못했습니다. 프로그램에서 다시 시도해 주세요.")
                    body = f"<html><meta charset='utf-8'><body style='font-family:sans-serif;padding:40px'>{msg}</body></html>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body.encode("utf-8"))
                    done.set()
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        redirect = f"http://127.0.0.1:{srv.server_address[1]}"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = AUTH_URL + "?" + urllib.parse.urlencode({
                "client_id": self.client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
                "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline",
                "prompt": "consent", "state": state})
            open_browser(url)
            if not done.wait(timeout):
                raise DriveError("구글 로그인 시간이 지났습니다. 다시 시도해 주세요.")
        finally:
            srv.shutdown()
            srv.server_close()
        if result.get("state") != state or "code" not in result:
            raise DriveError("구글 연결이 취소되었거나 실패했습니다." + (f" ({result.get('error')})" if result.get("error") else ""))
        tok = self._token({"code": result["code"], "code_verifier": verifier, "redirect_uri": redirect,
                           "grant_type": "authorization_code"})
        if not tok.get("refresh_token"):
            raise DriveError("구글이 갱신 토큰을 주지 않았습니다. 구글 계정의 '타사 연결'에서 V-Concept Studio 를 지운 뒤 다시 연결해 주세요.")
        self._save(tok["refresh_token"])

    def _token(self, fields) -> dict:
        data = urllib.parse.urlencode({"client_id": self.client_id, "client_secret": self.client_secret, **fields}).encode()
        _, body, _ = _request("POST", self.token_url, data=data,
                              headers={"Content-Type": "application/x-www-form-urlencoded"})
        tok = json.loads(body)
        self._access = tok.get("access_token")
        self._expires = time.time() + float(tok.get("expires_in", 3000)) - 60
        return tok

    def access_token(self) -> str:
        if self._access and time.time() < self._expires:
            return self._access
        refresh = self._load()
        if not refresh:
            raise DriveError("구글 드라이브가 연결되어 있지 않습니다.", auth=True)
        try:
            self._token({"refresh_token": refresh, "grant_type": "refresh_token"})
        except DriveError as e:
            if e.status in (400, 401):
                raise DriveError("구글 연결이 만료되었거나 해제되었습니다. [설정 > 구글 드라이브]에서 다시 연결해 주세요.",
                                 e.status, auth=True) from e
            raise
        return self._access

    def sign_out(self):
        refresh = self._load()
        self._save(None)
        self._access = None
        if refresh:
            try:
                _request("POST", "https://oauth2.googleapis.com/revoke?" + urllib.parse.urlencode({"token": refresh}),
                         headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=10)
            except DriveError:
                pass


# ---------------------------------------------------------------- Drive REST
class DriveClient:
    def __init__(self, token_provider, api=API, upload=UPLOAD):
        self.token = token_provider
        self.api = api
        self.upload_url = upload

    def _h(self, extra=None):
        return {"Authorization": f"Bearer {self.token()}", **(extra or {})}

    def _json(self, method, url, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        _, body, _ = _request(method, url, data=data, headers=self._h({"Content-Type": "application/json"} if data else None))
        return json.loads(body) if body else {}

    def find_or_create_folder(self) -> str:
        q = "appProperties has { key='vc_root' and value='1' } and trashed=false and mimeType='application/vnd.google-apps.folder'"
        res = self._json("GET", f"{self.api}/files?" + urllib.parse.urlencode({"q": q, "fields": "files(id,name)", "spaces": "drive"}))
        if res.get("files"):
            return res["files"][0]["id"]
        f = self._json("POST", f"{self.api}/files?fields=id", {
            "name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder", "appProperties": {"vc_root": "1"}})
        return f["id"]

    def list_items(self, folder_id) -> list[dict]:
        q = f"'{folder_id}' in parents and trashed=false and appProperties has {{ key='vc_item' and value='1' }}"
        out, page = [], None
        while True:
            params = {"q": q, "pageSize": "200", "spaces": "drive",
                      "fields": "nextPageToken,files(id,name,description,modifiedTime,appProperties)"}
            if page:
                params["pageToken"] = page
            res = self._json("GET", f"{self.api}/files?" + urllib.parse.urlencode(params))
            out += res.get("files", [])
            page = res.get("nextPageToken")
            if not page:
                return out

    def upload(self, folder_id, name, data: bytes, meta: dict) -> dict:
        boundary = "vc" + uuid.uuid4().hex
        metadata = {"name": name, "parents": [folder_id], "description": json.dumps(meta, ensure_ascii=False),
                    "appProperties": {"vc_item": "1", "vc_uid": meta["uid"]}, "mimeType": "image/png"}
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                f"{json.dumps(metadata, ensure_ascii=False)}\r\n--{boundary}\r\nContent-Type: image/png\r\n\r\n").encode("utf-8")
        body += data + f"\r\n--{boundary}--\r\n".encode()
        _, res, _ = _request("POST", f"{self.upload_url}/files?uploadType=multipart&fields=id,modifiedTime", data=body,
                             headers=self._h({"Content-Type": f"multipart/related; boundary={boundary}"}), timeout=180)
        return json.loads(res)

    def download(self, file_id) -> bytes:
        _, body, _ = _request("GET", f"{self.api}/files/{file_id}?alt=media", headers=self._h(), timeout=180)
        return body

    def update_meta(self, file_id, meta: dict):
        self._json("PATCH", f"{self.api}/files/{file_id}?fields=id", {"description": json.dumps(meta, ensure_ascii=False)})

    def trash(self, file_id):
        try:
            self._json("PATCH", f"{self.api}/files/{file_id}?fields=id", {"trashed": True})
        except DriveError as e:
            if e.status != 404:
                raise
