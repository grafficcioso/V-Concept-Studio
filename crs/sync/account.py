"""구글 계정 연결 정보 (창 화면과 MCP 모드가 같이 쓴다). 보안 비밀번호·토큰은 키 저장소에만."""
from __future__ import annotations

from .. import keystore

SECRET_NAME = "google_client_secret"
REFRESH_NAME = "google_refresh_token"


def make_auth(ws):
    from .drive import GoogleAuth
    return GoogleAuth(ws.settings.get("google_client_id") or "", keystore.get_secret(SECRET_NAME) or "",
                      lambda: keystore.get_secret(REFRESH_NAME), lambda v: keystore.set_secret(REFRESH_NAME, v))


def is_connected() -> bool:
    return bool(keystore.get_secret(REFRESH_NAME))


def make_drive(ws):
    from .drive import DriveClient
    return DriveClient(make_auth(ws).access_token)
