#!/usr/bin/env python3
"""Supabase media 버킷 정리 CLI — 로직은 modules/media_gc.py 에 있다.

    python3 -m scripts.purge_old_media              # 뭐가 지워질지 보기만(기본 7일)
    python3 -m scripts.purge_old_media --apply      # 실제 삭제
    python3 -m scripts.purge_old_media --days 30 --apply

맥 워커(mac_worker.py)가 6시간마다 같은 정리를 자동으로 돌리므로 평소엔 쓸 일이 없다.
용량이 갑자기 늘었을 때 무엇이 먹고 있는지 크기순으로 보려고 남겨둔 도구다.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from modules import media_gc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Supabase media 버킷 정리")
    ap.add_argument("--days", type=int, default=None,
                    help="모든 상태에 이 일수를 적용(수동 일괄 정리용). "
                         f"안 주면 상태별 기본값: {media_gc.PURGE_AFTER}")
    ap.add_argument("--apply", action="store_true", help="실제로 삭제 (기본은 미리보기)")
    args = ap.parse_args()

    try:
        result = media_gc.plan(args.days)
    except media_gc.Restricted as e:
        print(e)
        return 2

    purge, keep = result["purge"], result["keep"]
    freed = sum(r[3] for r in purge)

    print(f"버킷 총 사용량 {media_gc.mb(result['total'])}  ·  폴더 {len(purge) + len(keep)}개\n")
    print(f"삭제 대상 {len(purge)}개 / {media_gc.mb(freed)}")
    for did, why, count, size in purge:
        print(f"  - {did}  {media_gc.mb(size):>9}  파일 {count}개  ({why})")
    print(f"\n유지 {len(keep)}개 / {media_gc.mb(sum(r[2] for r in keep))}")
    for did, why, size in sorted(keep, key=lambda r: r[2], reverse=True)[:10]:
        print(f"  · {did}  {media_gc.mb(size):>9}  ({why})")
    if len(keep) > 10:
        print(f"  … 외 {len(keep) - 10}개")

    if not purge:
        print("\n지울 게 없습니다.")
        return 0
    if not args.apply:
        print("\n미리보기입니다. 실제로 지우려면 --apply 를 붙이세요.")
        return 0

    print()
    actual = media_gc.delete([r[0] for r in purge])
    print(f"{len(purge)}개 폴더 / {media_gc.mb(actual)} 정리했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
