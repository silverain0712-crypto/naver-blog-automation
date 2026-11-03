"""네이버 클립(세로 숏폼) 생성 오케스트레이션.

블로그 글쓰기 파이프라인(mac_generator.py/mac_worker.py, prompts/*)과는 완전히
별개다 — 이 모듈은 오직 mac_clip_generator.py 에서만 호출된다.

실제 대본/렌더 엔진은 이미 구현돼 있다(modules/shortform.py, modules/video_maker.py,
modules/video_engine.py) — 여기서는 그 둘을 draft 저장소(Supabase)와 잇는 글루만 한다.

두 단계로 나뉜다(2026-09-07, 유저가 참고한 다른 툴처럼 장면별 AI모션 여부를 유저가
직접 고르게 하려고 분리):
  1. plan()     — 대본만 생성(사진 다운로드/렌더 없음, 가벼움). data.clip_script 저장.
  2. generate() — data.clip_script 를 읽어(유저가 고른 motion_selected 반영) 실제 렌더.
shortform.py/video_maker.py 는 이 분리를 위해 수정하지 않는다 — video_maker.render()
는 이미 scenes[].motion_worth/motion_prompt 만 보고 동작하므로, generate() 에서
유저가 고른 값으로 그 두 필드를 덮어써서 넘기기만 하면 된다.
"""

from __future__ import annotations

import copy
import shutil
import tempfile
from pathlib import Path

import config
from modules import shortform, store, video_maker
from modules.llm import call_json, prepare_image_block

# 한 비전 요청에 넣을 최대 사진 수(modules/image_analyzer.py 와 동일 기준 —
# 많이 넣으면 요청이 커져 연결이 끊긴다).
_DESC_BATCH = 8
_DESC_SCHEMA = {
    "type": "object",
    "required": ["photos"],
    "additionalProperties": False,
    "properties": {
        "photos": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["index", "desc"],
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer"},
                    "desc": {
                        "type": "string",
                        "description": "실제로 화면에 보이는 것 한 줄(15~25자). "
                                       "쿠폰/영수증/안내문처럼 글자 위주 이미지면 그 사실을 밝힐 것.",
                    },
                },
            },
        },
    },
}


def _download_photos(images: list[str], tmp: Path, videos: list[dict] | None = None) -> dict[int, Path]:
    """draft.images(Storage 경로 배열)를 임시 디렉토리에 1부터 번호 매겨 내려받는다.

    배열 순서가 곧 [사진N] 마커 번호다(_embed_product_shots 등 기존 파이프라인과
    동일한 가정 — images[i] == [사진(i+1)]).

    videos(data.videos)에 gif_photo_number 가 있는 항목은 그 번호의 사진을 GIF 대신
    원본 동영상으로 덮어쓴다 — video_maker.render() 가 이미 실제 영상 확장자를 보면
    AI 모션 합성 없이 그 원본 움직임을 그대로 쓰기 때문에(2026-09-07 확장, video_maker.py
    _REAL_FOOTAGE_EXTS), 사진 대신 그 진짜 영상을 넘겨주기만 하면 된다.
    """
    photo_paths: dict[int, Path] = {}
    for i, path in enumerate(images, start=1):
        try:
            content = store.download(path)
        except Exception:
            continue
        suffix = Path(path).suffix or ".jpg"
        local = tmp / f"{i}{suffix}"
        local.write_bytes(content)
        photo_paths[i] = local

    for video in videos or []:
        n = video.get("gif_photo_number")
        if not isinstance(n, int):
            continue
        try:
            content = store.download(video["path"])
        except Exception:
            continue
        suffix = Path(video["path"]).suffix or ".mp4"
        local = tmp / f"{n}{suffix}"
        local.write_bytes(content)
        photo_paths[n] = local
    return photo_paths


def _thumbnail_photo_number(images: list[str]) -> int | None:
    """썸네일 빌더(ThumbnailBuilder.tsx)가 images[] 맨 뒤에 붙인 합성 이미지의
    번호(2026-09-09). 항상 `{draftId}/thumb.png` 고정 경로로 올라가고, 제목 문구·
    해시태그 알약이 이미지 위에 그대로 박혀 있어(그 이미지 자체가 "썸네일 컷") —
    클립 씬에 쓰면 자막이 두 번 겹쳐 보이는 등 안 어울린다. 클립 후보에서 뺀다."""
    for i, path in enumerate(images, start=1):
        if path.endswith("/thumb.png"):
            return i
    return None


def _describe_photos(images: list[str], candidates: list[int]) -> dict[int, str]:
    """후보 사진들의 "실제로 뭐가 보이는지" 한 줄 비전 설명(2026-09-09, 이미지-자막
    매칭 개선). modules/image_analyzer.analyze_images() 와 동일한 배치/모델 패턴 —
    블로그 캡션은 마케팅 톤이라 쿠폰·영수증 같은 사진엔 캡션이 없거나 실제 내용과
    안 맞을 수 있어서, 대본 LLM에게 더 정확한 신호를 준다. 배치 실패는 조용히
    건너뛴다(그 사진들은 기존처럼 블로그 캡션만으로 계속 진행 — 클립 기획 자체를
    막지 않는다).
    """
    idxs = sorted(set(candidates))
    if not idxs:
        return {}
    out: dict[int, str] = {}
    for start in range(0, len(idxs), _DESC_BATCH):
        chunk = idxs[start:start + _DESC_BATCH]
        content: list = [{"type": "text", "text": "각 사진에 실제로 무엇이 보이는지 한 줄로 설명하라."}]
        for n in chunk:
            path = images[n - 1] if 0 <= n - 1 < len(images) else None
            if not path:
                continue
            try:
                img_bytes = store.download(path)
            except Exception:
                continue
            content.append({"type": "text", "text": f"[사진{n}]"})
            content.append(prepare_image_block(img_bytes))
        if len(content) <= 1:
            continue
        try:
            result = call_json(
                model=config.VISION_MODEL,
                system="너는 사진을 있는 그대로 설명하는 어시스턴트다.",
                content=content,
                schema=_DESC_SCHEMA,
                max_tokens=2000,
            )
            for p in result.get("photos") or []:
                if isinstance(p.get("index"), int):
                    out[p["index"]] = (p.get("desc") or "").strip()
        except Exception:
            continue
    return out


def plan(row: dict) -> None:
    """data.clip_script_job.status=='requested' 인 draft 하나를 처리한다.

    shortform.make_script() 만 호출한다(사진 다운로드/ffmpeg 없음). 각 장면의
    motion_selected 기본값은 LLM 추천(motion_worth=='high')으로 채운다 — 유저가
    편집화면에서 이 기본값을 보고 장면별로 켜고 끌 수 있다.
    """
    draft_id = row["id"]
    data = row.get("data") or {}
    job = data.get("clip_script_job") or {}

    job = {**job, "status": "running"}
    data = {**data, "clip_script_job": job}
    store.update_draft(draft_id, {"data": data})

    try:
        body = row.get("body") or ""
        images = row.get("images") or []
        thumb_n = _thumbnail_photo_number(images)
        candidates = shortform.photo_candidates(body, data.get("clip_source_photos"))
        if thumb_n is not None:
            candidates = [n for n in candidates if n != thumb_n]
        photo_descriptions = _describe_photos(images, candidates)

        script = shortform.make_script(
            title=row.get("title") or "",
            body=body,
            captions=data.get("captions") or {},
            subheadings=data.get("subheadings") or [],
            allowed_photos=candidates,
            feedback=data.get("clip_feedback") or "",
            photo_descriptions=photo_descriptions,
        )
        for sc in script.get("scenes") or []:
            sc["motion_selected"] = sc.get("motion_worth") == "high"

        data = {
            **data,
            "clip_script_job": {**job, "status": "done", "error": ""},
            "clip_script": script,
        }
        store.update_draft(draft_id, {"data": data})
        print(f"  ✅ 클립 대본 완성: {draft_id[:8]} (씬 {len(script.get('scenes') or [])}개)")
    except Exception as e:
        msg = str(e)[:300]
        data = {**data, "clip_script_job": {**job, "status": "error", "error": msg}}
        store.update_draft(draft_id, {"data": data})
        print(f"  ⚠️ 클립 대본 생성 실패: {msg}")


def _effective_script(script: dict) -> dict:
    """유저가 고른 motion_selected 를 motion_worth/motion_prompt 에 반영한 사본을 만든다.

    video_maker.render() 는 이 두 필드만 보므로, 이 함수만 있으면 video_maker.py 를
    전혀 손대지 않고도 유저의 장면별 AI모션 선택을 그대로 반영할 수 있다.

    2026-09-09 정책 변경: 예전엔 LLM이 low 판정한 장면을 유저가 강제로 켜면 막연한
    자동 문구("gentle subtle motion...")로 Veo 를 돌렸는데, 실측해보니 거의 안 움직여
    비용만 쓰고 체감 효과가 없었다. 이제는 "LLM 이 준 진짜 프롬프트" 또는 "유저가
    직접 쓴 motion_prompt_override" 가 있을 때만 AI 모션을 켠다 — 설명 없이 체크만
    한 경우는 무시하고 안전하게 켄번즈로 남긴다.
    """
    script = copy.deepcopy(script)
    for sc in script.get("scenes") or []:
        if sc.get("motion_selected"):
            own = (sc.get("motion_prompt") or "").strip()
            override = (sc.get("motion_prompt_override") or "").strip()
            if own:
                sc["motion_worth"] = "high"
            elif override:
                sc["motion_worth"] = "high"
                sc["motion_prompt"] = override
            else:
                sc["motion_worth"] = "low"
                sc["motion_prompt"] = ""
        else:
            sc["motion_worth"] = "low"
            sc["motion_prompt"] = ""
    return script


def generate(row: dict) -> None:
    """data.clip_job.status=='requested' 인 draft 하나를 처리한다(실제 렌더).

    data.clip_script(먼저 plan() 으로 만들어져 있어야 함)를 읽어 유저가 고른
    motion_selected 를 반영해 렌더한다. 성공/실패 여부와 무관하게 data.clip_job 을
    최종 상태(done/error)로 남긴다.
    """
    draft_id = row["id"]
    data = row.get("data") or {}
    job = data.get("clip_job") or {}

    job = {**job, "status": "running"}
    data = {**data, "clip_job": job}
    store.update_draft(draft_id, {"data": data})

    tmp = Path(tempfile.mkdtemp(prefix="clip_"))
    try:
        base_script = data.get("clip_script")
        if not base_script or not base_script.get("scenes"):
            raise RuntimeError("먼저 대본을 기획해주세요.")

        images = row.get("images") or []
        if not images:
            raise RuntimeError("이 글에는 사진이 없어 클립을 만들 수 없습니다.")

        photo_paths = _download_photos(images, tmp, data.get("videos") or [])
        if not photo_paths:
            raise RuntimeError("사진을 하나도 내려받지 못했습니다.")

        script = _effective_script(base_script)
        render_opts = data.get("clip_render") or {}

        out_path = tmp / "clip.mp4"
        result = video_maker.render(
            script, photo_paths, out_path, narration="subtitle",
            font=render_opts.get("font", "default"),
            text_color=render_opts.get("text_color", "auto"),
            box_style=render_opts.get("box_style", "auto"),
            log=lambda msg: print(f"  [clip:{draft_id[:8]}] {msg}"),
        )
        if result is None:
            raise RuntimeError("영상 렌더링에 실패했습니다.")

        prev = data.get("clip") or {}
        rev = int(prev.get("rev", -1)) + 1
        storage_path = store.upload_bytes(
            f"{draft_id}/clip_r{rev}.mp4", result.read_bytes(), "video/mp4")
        old_path = prev.get("path")
        if old_path and old_path != storage_path:
            try:
                store.remove_paths([old_path])
            except Exception as e:
                print(f"  이전 클립 정리 실패(무시): {str(e)[:60]}")

        data = {
            **data,
            "clip_job": {**job, "status": "done", "error": ""},
            "clip": {
                "path": storage_path,
                "rev": rev,
                "duration": base_script.get("total_seconds"),
                "script": base_script,
            },
        }
        store.update_draft(draft_id, {"data": data})
        print(f"  ✅ 클립 완성: {draft_id[:8]} ({base_script.get('total_seconds')}초)")
    except Exception as e:
        msg = str(e)[:300]
        data = {**data, "clip_job": {**job, "status": "error", "error": msg}}
        store.update_draft(draft_id, {"data": data})
        print(f"  ⚠️ 클립 생성 실패: {msg}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
