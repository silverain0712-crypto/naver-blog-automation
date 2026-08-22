"""Supabase media 버킷 청소 — 발행이 끝난 초안의 사진을 회수한다.

무료 플랜은 스토리지 1GB 다. 넘기면 프로젝트가 restricted 되어 Storage 뿐 아니라
DB(`/rest/v1/*`)까지 전부 HTTP 402 가 되고 폰 앱이 통째로 멈춘다(2026-08-22 실제 발생).

버킷은 초안 1건당 폴더 1개(`{draft_id}/1.jpg`, `thumb.png`, `guideline_*` …) 구조다.

지우는 대상
  · 고아 폴더    — drafts 테이블에 행이 없는 폴더(이미 지운 초안의 잔해)
  · 발행 끝난 것 — status=posted 이면서 DEFAULT_DAYS 보다 오래된 초안.
                   사진은 이미 네이버에 올라가 있으므로 버킷에 둘 이유가 없다.
  · 실패한 것    — status=error 이면서 DEFAULT_DAYS 보다 오래된 초안.
                   이만큼 지나도록 안 고쳤으면 재시도하지 않는다고 본다.
                   사진이 사라지므로 그 초안은 더 이상 재시도할 수 없다.

건드리지 않는 것
  · photo_stash — 나중에 글로 쓰려고 일부러 보관 중인 사진
  · 작업 중 상태(uploading·generating·draft_ready·queued·posting)

DB 행은 남긴다 — 글 목록/본문 기록은 그대로 두고 용량만 회수한다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx

from modules import store

# 네이버 임시저장(posted) 후 이만큼 지나면 사진을 지운다.
DEFAULT_DAYS = 7

KEEP_STATUSES = {"photo_stash"}

# 상태별 보관 기간. 실패한 글은 폰에서 '다시 시도'를 누를 수 있어야 하고, 사진이
# 사라지면 재시도해도 글만 저장된다. 그래서 발행 완료분보다 길게 둔다.
PURGE_AFTER = {"posted": DEFAULT_DAYS, "error": 30}
PURGEABLE_STATUSES = set(PURGE_AFTER)


class Restricted(RuntimeError):
    """용량 초과로 Supabase 프로젝트가 잠긴 상태(HTTP 402)."""


def mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f}MB"


# Postgres 는 소수점 이하 자릿수를 있는 대로 준다(`.46993` 처럼 5자리도 나온다).
# Python 3.9 의 fromisoformat 은 3자리/6자리만 받으므로 6자리로 맞춰준다.
_TS_RE = re.compile(
    r"^(?P<head>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d+))?(?P<tz>.*)$"
)


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    m = _TS_RE.match(text)
    if m:
        frac = (m.group("frac") or "")[:6].ljust(6, "0")
        text = f"{m.group('head')}.{frac}{m.group('tz')}"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _files(prefix: str) -> list[dict]:
    """폴더 안의 파일 목록(하위 폴더 제외)."""
    return [it for it in store.list_prefix(prefix)
            if (it.get("metadata") or {}).get("size") is not None]


def plan(days: int | None = None) -> dict:
    """무엇을 지우고 무엇을 남길지 계산만 한다(삭제 없음).

    days 를 주면 모든 상태에 그 기준을 쓴다(수동 일괄 정리용). 안 주면 PURGE_AFTER 의
    상태별 기간을 따른다.

    반환: {"total": 바이트, "purge": [(id, 사유, 파일수, 바이트)], "keep": [(id, 사유, 바이트)]}
    """
    try:
        drafts = store.list_drafts()
        roots = store.list_prefix("")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 402:
            raise Restricted(
                "Supabase 프로젝트가 용량 초과로 잠겨 있습니다(402). "
                "대시보드 Storage 에서 파일을 먼저 지워 1GB 아래로 내린 뒤 다시 실행하세요."
            ) from e
        raise

    by_id = {d["id"]: d for d in drafts}
    now = datetime.now(timezone.utc)

    def cutoff_for(status: str) -> datetime:
        span = days if days is not None else PURGE_AFTER.get(status, DEFAULT_DAYS)
        return now - timedelta(days=span)
    purge: list[tuple[str, str, int, int]] = []
    keep: list[tuple[str, str, int]] = []
    total = 0

    for item in roots:
        # 폴더는 metadata 가 없다. 루트에 흘러든 파일은 건드리지 않는다.
        if (item.get("metadata") or {}).get("size") is not None:
            continue
        name = item["name"]
        files = _files(name)
        if not files:
            continue
        size = sum(f["metadata"]["size"] for f in files)
        total += size

        draft = by_id.get(name)
        if draft is None:
            purge.append((name, "고아(DB 행 없음)", len(files), size))
            continue
        status = draft.get("status") or "?"
        if status in KEEP_STATUSES:
            keep.append((name, f"보관 사진({status})", size))
            continue
        created = _parse_ts(draft.get("created_at") or "")
        if status in PURGEABLE_STATUSES and created and created < cutoff_for(status):
            age = (now - created).days
            purge.append((name, f"{status} {age}일 지남", len(files), size))
        else:
            keep.append((name, status, size))

    purge.sort(key=lambda r: r[3], reverse=True)
    return {"total": total, "purge": purge, "keep": keep}


def delete(folders: list[str]) -> int:
    """폴더들의 파일을 실제로 지우고 회수한 바이트를 반환."""
    freed = 0
    for name in folders:
        files = _files(name)
        if not files:
            continue
        store.remove_paths([f"{name}/{f['name']}" for f in files])
        freed += sum(f["metadata"]["size"] for f in files)
    return freed


def run(days: int | None = None, log=None) -> int:
    """계산 → 삭제까지 한 번에. 회수한 바이트 반환. 워커가 주기적으로 부른다."""
    result = plan(days)
    targets = [r[0] for r in result["purge"]]
    if not targets:
        return 0
    freed = delete(targets)
    if log:
        log(f"사진 정리: 폴더 {len(targets)}개 / {mb(freed)} 회수 "
            f"(남은 사용량 {mb(result['total'] - freed)})")
    return freed
