"""OpenAI API 키 보관.

Windows에서는 keyring → Windows 자격 증명 관리자(Credential Manager)에 저장된다.
보안 저장소를 쓸 수 없는 환경에서는 디스크에 쓰지 않고 '이번 실행 동안만' 메모리에 보관한다.
키는 settings.json / SQLite / 로그 / 프로젝트 파일 어디에도 기록하지 않는다.
"""
from __future__ import annotations

import logging

SERVICE = "V-Concept Studio"
LEGACY_SERVICE = "CharacterReferenceStudio"   # v0.1~0.2 에서 쓰던 이름 → 처음 읽을 때 자동 이전
ACCOUNT = "openai_api_key"

log = logging.getLogger(__name__)
_session_key: str | None = None


def _secure_backend():
    try:
        import keyring
        from keyring.backends import fail
        kr = keyring.get_keyring()
        if isinstance(kr, fail.Keyring) or getattr(kr, "priority", 1) <= 0:
            return None
        return keyring
    except Exception:  # keyring 미설치/백엔드 오류
        return None


def backend_description() -> str:
    kr = _secure_backend()
    if kr is None:
        return "보안 저장소를 사용할 수 없음 — 이번 실행 동안만 메모리에 보관 (디스크에 저장 안 함)"
    name = type(kr.get_keyring()).__name__
    if "WinVault" in name:
        return "Windows 자격 증명 관리자 (Credential Manager)"
    return f"OS 보안 저장소 ({name})"


def is_persistent() -> bool:
    return _secure_backend() is not None


def get_key() -> str | None:
    kr = _secure_backend()
    if kr is not None:
        try:
            value = kr.get_password(SERVICE, ACCOUNT)
            if value:
                return value
            legacy = kr.get_password(LEGACY_SERVICE, ACCOUNT)
            if legacy:
                kr.set_password(SERVICE, ACCOUNT, legacy)
                try:
                    kr.delete_password(LEGACY_SERVICE, ACCOUNT)
                except Exception:
                    pass
                return legacy
        except Exception as e:
            log.warning("보안 저장소에서 키를 읽지 못했습니다: %s", type(e).__name__)
    return _session_key


def set_key(value: str) -> bool:
    """키 저장. 영구 저장되면 True, 세션 메모리에만 보관되면 False."""
    global _session_key
    value = (value or "").strip()
    if not value:
        raise ValueError("빈 키")
    kr = _secure_backend()
    if kr is not None:
        try:
            kr.set_password(SERVICE, ACCOUNT, value)
            _session_key = None
            return True
        except Exception as e:
            log.warning("보안 저장소에 키를 저장하지 못했습니다: %s", type(e).__name__)
    _session_key = value
    return False


def delete_key():
    global _session_key
    _session_key = None
    kr = _secure_backend()
    if kr is not None:
        try:
            kr.delete_password(SERVICE, ACCOUNT)
        except Exception:
            pass


def mask(value: str | None) -> str:
    if not value:
        return "(없음)"
    return f"{value[:3]}…{value[-4:]}" if len(value) > 12 else "…"


# ---- 구글 드라이브 연결 정보 (클라이언트 보안 비밀번호, 갱신 토큰) -------------------------------
_session_extra: dict[str, str | None] = {}


def get_secret(name: str) -> str | None:
    kr = _secure_backend()
    if kr is not None:
        try:
            v = kr.get_password(SERVICE, name)
            if v:
                return v
        except Exception:
            pass
    return _session_extra.get(name)


def set_secret(name: str, value: str | None) -> bool:
    kr = _secure_backend()
    if value is None:
        _session_extra.pop(name, None)
        if kr is not None:
            try:
                kr.delete_password(SERVICE, name)
            except Exception:
                pass
        return True
    if kr is not None:
        try:
            kr.set_password(SERVICE, name, value)
            _session_extra.pop(name, None)
            return True
        except Exception:
            pass
    _session_extra[name] = value
    return False
