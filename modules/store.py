"""공용 저장소(Supabase) — 폰 클라우드 앱 ↔ 맥 포스터 사이의 데이터 다리.

Supabase REST(PostgREST) + Storage 를 httpx 로 직접 호출(전용 클라이언트 불필요).
드래프트 메타/본문은 `drafts` 테이블, 사진·썸네일은 Storage `media` 버킷에 저장한다.

필요 env: SUPABASE_URL, SUPABASE_KEY (service role 권장). 버킷: SUPABASE_BUCKET(기본 media).
테이블 스키마는 SUPABASE_SETUP.md 참고.
"""

from __future__ import annotations

import time
import uuid

import httpx

import config

_TIMEOUT = 30


def enabled() -> bool:
    return bool(config.SUPABASE_URL and config.SUPABASE_KEY)


def _rest_headers(extra: dict | None = None) -> dict:
    h = {
        "apikey": config.SUPABASE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _require():
    if not enabled():
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_KEY 가 설정되지 않았습니다. .env(또는 클라우드 secrets)를 확인하세요."
        )


# --- Storage --------------------------------------------------------------
def _upload(path: str, data: bytes, content_type: str) -> str:
    """media 버킷에 업로드(덮어쓰기). 저장 경로(path) 반환."""
    url = f"{config.SUPABASE_URL}/storage/v1/object/{config.SUPABASE_BUCKET}/{path}"
    headers = {
        "apikey": config.SUPABASE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_KEY}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    r = httpx.post(url, content=data, headers=headers, timeout=_TIMEOUT)
    r.raise_for_status()
    return path


def download(path: str) -> bytes:
    """media 버킷에서 객체 내려받기. 간헐 SSL/연결 오류는 재시도."""
    _require()
    url = f"{config.SUPABASE_URL}/storage/v1/object/{config.SUPABASE_BUCKET}/{path}"
    headers = {
        "apikey": config.SUPABASE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_KEY}",
    }
    last = None
    for attempt in range(4):
        try:
            r = httpx.get(url, headers=headers, timeout=_TIMEOUT)
            r.raise_for_status()
            return r.content
        except Exception as e:
            last = e
            time.sleep(0.5 * (attempt + 1))
    raise last


# --- Drafts (DB) ----------------------------------------------------------
def save_draft(
    *,
    title: str,
    body: str,
    data: dict,
    images: list[bytes],
    thumbnail: bytes | None = None,
    status: str = "ready",
) -> str:
    """드래프트 1건 저장. 사진/썸네일은 Storage 에, 메타는 테이블에. id 반환."""
    _require()
    draft_id = str(uuid.uuid4())

    image_paths = []
    for i, img in enumerate(images, start=1):
        p = _upload(f"{draft_id}/{i}.jpg", img, "image/jpeg")
        image_paths.append(p)

    thumb_path = None
    if thumbnail:
        thumb_path = _upload(f"{draft_id}/thumb.png", thumbnail, "image/png")

    record = {
        "id": draft_id,
        "status": status,
        "title": title,
        "body": body,
        "data": data,
        "images": image_paths,
        "thumbnail": thumb_path,
    }
    url = f"{config.SUPABASE_URL}/rest/v1/drafts"
    r = httpx.post(url, json=record, headers=_rest_headers({"Prefer": "return=minimal"}),
                   timeout=_TIMEOUT)
    r.raise_for_status()
    return draft_id


def list_drafts(status: str | None = None) -> list[dict]:
    """드래프트 목록(최신순). status 지정 시 필터."""
    _require()
    url = f"{config.SUPABASE_URL}/rest/v1/drafts?select=*&order=created_at.desc"
    if status:
        url += f"&status=eq.{status}"
    r = httpx.get(url, headers=_rest_headers(), timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_draft(draft_id: str) -> dict | None:
    _require()
    url = f"{config.SUPABASE_URL}/rest/v1/drafts?id=eq.{draft_id}&select=*"
    r = httpx.get(url, headers=_rest_headers(), timeout=_TIMEOUT)
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def update_draft(draft_id: str, fields: dict) -> None:
    _require()
    url = f"{config.SUPABASE_URL}/rest/v1/drafts?id=eq.{draft_id}"
    r = httpx.patch(url, json=fields, headers=_rest_headers({"Prefer": "return=minimal"}),
                    timeout=_TIMEOUT)
    r.raise_for_status()


def delete_draft(draft_id: str) -> None:
    _require()
    url = f"{config.SUPABASE_URL}/rest/v1/drafts?id=eq.{draft_id}"
    r = httpx.delete(url, headers=_rest_headers(), timeout=_TIMEOUT)
    r.raise_for_status()
