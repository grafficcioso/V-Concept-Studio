"""핀터레스트 공개 보드 가져오기 (v0.6.0).

- 공개 보드의 RSS(`https://www.pinterest.com/<사용자>/<보드>.rss`)를 읽는다. 로그인·API 키·비용 없음.
  RSS 는 최근 핀 약 25개만 주고, 핀터레스트가 언제든 바꾸거나 막을 수 있다 (비공식 기능).
- 핀 날짜는 RSS 의 pubDate (핀이 만들어진 날짜로 보임. '보드에 추가한 날짜'와 다를 수 있음 — 확인 필요).
- 이미지는 미리보기 주소(/236x/)를 원본 크기(/originals/)로 바꿔 받고, 안 되면 /736x/ 로 받는다.
- 가져온 이미지는 '외부 출처' 태그를 달고 메모에 출처를 남긴다: 공부·참고용, 판매·커미션·게시 금지.
"""
from __future__ import annotations

import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) V-Concept-Studio"
EXTERNAL_TAG = "외부 출처"
EXTERNAL_NOTE = "핀터레스트에서 가져옴 — 공부·참고용. 판매·커미션·게시 금지. 출처: {link}"
TIMEOUT = 20
MAX_BYTES = 40 * 1024 * 1024


class PinterestError(Exception):
    pass


@dataclass
class Pin:
    pin_id: str
    title: str
    link: str
    thumb_url: str
    date: datetime | None

    @property
    def date_text(self) -> str:
        return self.date.astimezone().strftime("%Y-%m-%d") if self.date else "날짜 없음"


def board_rss_url(text: str) -> str:
    """보드 주소(여러 형태)를 RSS 주소로. 예: 'https://kr.pinterest.com/user/board/' / 'user/board' → .../user/board.rss"""
    t = (text or "").strip()
    if not t:
        raise PinterestError("보드 주소를 넣어 주세요.")
    if "pin.it/" in t:
        raise PinterestError("pin.it 짧은 주소는 쓸 수 없습니다. 브라우저에서 보드를 연 뒤 주소창의 전체 주소를 넣어 주세요.")
    if "://" not in t:
        t = "https://www.pinterest.com/" + t.lstrip("/")
    u = urllib.parse.urlparse(t)
    host = u.netloc.lower()
    if not (host == "pinterest.com" or host.endswith(".pinterest.com") or re.fullmatch(r"([a-z]+\.)?pinterest\.[a-z.]+", host)):
        raise PinterestError("핀터레스트 보드 주소가 아닙니다.")
    parts = [p for p in u.path.split("/") if p]
    if parts and parts[-1].endswith(".rss"):
        parts[-1] = parts[-1][:-4]
    if not parts or parts[0] in ("pin", "search", "ideas"):
        raise PinterestError("보드 주소를 넣어 주세요 (예: https://www.pinterest.com/사용자이름/보드이름/).")
    if len(parts) == 1:
        return f"https://www.pinterest.com/{parts[0]}/feed.rss"      # 사용자 전체 핀
    return f"https://www.pinterest.com/{parts[0]}/{parts[1]}.rss"


def board_key(text: str) -> str:
    """중복 확인·표시용 '사용자/보드'."""
    u = urllib.parse.urlparse(board_rss_url(text))
    return u.path.strip("/").removesuffix(".rss").removesuffix("/feed")


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def http_get(url: str, limit: int = MAX_BYTES) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise PinterestError("파일이 너무 큽니다.")
    return data


def parse_rss(xml_bytes: bytes) -> list[Pin]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise PinterestError("보드 목록을 읽지 못했습니다 (RSS 형식이 아님). 비공개 보드이거나 주소가 틀렸을 수 있습니다.") from e
    pins = []
    for item in root.iter("item"):
        link = (item.findtext("link") or item.findtext("guid") or "").strip()
        m = re.search(r"/pin/([^/?#]+)", link)
        desc = item.findtext("description") or ""
        im = re.search(r'<img[^>]+src="([^"]+)"', desc)
        if not m or not im:
            continue
        date = None
        raw = (item.findtext("pubDate") or "").strip()
        if raw:
            try:
                date = parsedate_to_datetime(raw)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                date = None
        title = " ".join((item.findtext("title") or "").split())
        pins.append(Pin(m.group(1), title[:200], link, im.group(1), date))
    return pins


def fetch_board(text: str, get=http_get) -> list[Pin]:
    url = board_rss_url(text)
    try:
        data = get(url)
    except PinterestError:
        raise
    except Exception as e:
        code = getattr(e, "code", None)
        if code == 404:
            raise PinterestError("보드를 찾지 못했습니다. 공개 보드인지, 주소가 맞는지 확인해 주세요.") from e
        raise PinterestError(f"핀터레스트에 연결하지 못했습니다: {e}") from e
    return parse_rss(data)


def image_urls(thumb_url: str) -> list[str]:
    """원본 크기 → 736px → 미리보기 순서로 시도할 주소."""
    out = []
    for size in ("originals", "736x"):
        u = re.sub(r"/(\d+x(\d+)?|originals)/", f"/{size}/", thumb_url, count=1)
        if u not in out:
            out.append(u)
    if thumb_url not in out:
        out.append(thumb_url)
    return out


def download_image(pin: Pin, get=http_get) -> tuple[bytes, str]:
    """(이미지 바이트, 받은 주소). 전부 실패하면 PinterestError."""
    last = None
    for u in image_urls(pin.thumb_url):
        try:
            data = get(u)
            if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[8:12] == b"WEBP":
                return data, u
            last = PinterestError("이미지가 아닌 응답")
        except Exception as e:   # 다음 크기로
            last = e
    raise PinterestError(f"핀 {pin.pin_id} 이미지를 받지 못했습니다: {last}")


def filter_since(pins: list[Pin], since: datetime | None) -> list[Pin]:
    if since is None:
        return list(pins)
    if since.tzinfo is None:
        since = since.astimezone()
    return [p for p in pins if p.date and p.date >= since]


def parse_since(text: str | None) -> datetime | None:
    """'2026-11-01' 같은 날짜(그날 0시, 이 PC 시간대)."""
    if not text:
        return None
    try:
        return datetime.strptime(text.strip()[:10], "%Y-%m-%d").astimezone()
    except ValueError as e:
        raise PinterestError(f"날짜 형식은 2026-11-01 처럼 넣어 주세요 (받은 값: {text})") from e
