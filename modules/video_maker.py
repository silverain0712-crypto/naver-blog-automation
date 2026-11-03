"""숏폼 대본 + 사진 → 세로(9:16) mp4 렌더. ffmpeg 조립.

modules/shortform.py 가 만든 대본을 받아 씬별 클립을 만들고 이어 붙인다.

씬 하나가 그림이 되는 방법은 둘 중 하나다:
  - motion_worth=high  → video_engine 으로 사진을 영상으로 변환(유료, 컷당 과금)
  - 그 외             → 켄번즈(천천히 줌인) — 공짜, 정지 사진도 안 심심하다
AI 변환이 실패하면 자동으로 켄번즈로 내려간다. 영상이 아예 안 나오는 경우는 없다.

나레이션은 만들 때마다 고른다:
  - "subtitle" : 자막만(무음)
  - "tts"      : macOS say 로 성우 트랙을 깔고 씬 길이를 음성에 맞춰 늘린다
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont

import config
from modules import video_engine

W, H, FPS = 1080, 1920, 30
# 자막 안전 영역: 네이버 클립(실측, 채널명+캡션+우측 아이콘 줄이 하단 ≈24%=
# 1920px 기준 ≈455px 차지) · 인스타 릴스(조사, 가장 넓은 ≈500px) 둘 다 만족하는
# 보수적 하단 마진(2026-09-09 재조정, 기존 420 → 500).
SUB_BOTTOM_MARGIN = 500
FONT = config.BASE_DIR / "fonts" / "title.ttf"
# macOS say 한국어 음성. 'Yuna' 가 없는 시스템도 있어 실행 시 확인 후 폴백한다.
TTS_VOICE_PREFS = ("Yuna", "Flo", "Reed", "Eddy")
# 이미 실제로 찍힌 움직임(움짤/원본 영상) — AI 모션 합성 없이 그대로 쓴다.
_REAL_FOOTAGE_EXTS = {".gif", ".mp4", ".mov", ".m4v", ".webm"}
# gif_extractor.py 가 네이버 10MB 첨부 제한을 맞추려 GIF를 이 아래로도 압축할 수
# 있다(최저 240px). 그런 저화질 소스를 1080폭으로 그대로 늘리면 뭉개져 보이므로
# (2026-09-09 실측), 이 미만이면 "실제 영상 그대로" 대신 정지 프레임으로 내린다.
_MIN_FOOTAGE_WIDTH = 480

# 자막 폰트 선택지(2026-09-08, 클립 고도화). 전부 구글 폰트(OFL 라이선스) —
# fonts/ 에 실제 ttf 로 받아 번들. "default" 는 기존 title.ttf 그대로 유지.
_FONTS_DIR = config.BASE_DIR / "fonts"
FONT_CHOICES: dict[str, Path] = {
    "default": FONT,
    "jua": _FONTS_DIR / "Jua-Regular.ttf",
    "black_han_sans": _FONTS_DIR / "BlackHanSans-Regular.ttf",
    "gaegu": _FONTS_DIR / "Gaegu-Bold.ttf",
    "nanum_myeongjo": _FONTS_DIR / "NanumMyeongjo-Bold.ttf",
    "nanum_pen": _FONTS_DIR / "NanumPenScript-Regular.ttf",
}
# "auto" 는 기존 기본값(흰색)과 동일하게 유지 — 하위 호환.
TEXT_COLOR_CHOICES: dict[str, str] = {
    "auto": "white",
    "white": "white",
    "yellow": "#FDFF54",
    "pink": "#FF7BAC",
    "green": "#7DC242",
    "black": "#111111",
}
# "auto"/"dark" 는 기존 기본값(검정 반투명 박스)과 동일 — 하위 호환.
BOX_STYLE_CHOICES = {"auto", "dark", "light", "none"}
# 폰트가 아예 글자 자체를 못 그려 자막에 빈 네모(tofu box)가 박히는 경우 대신 쓸
# 폰트(2026-09-10 실측: Jua/Gaegu 는 흔한 한글 2350자만 담고 있어 "웻" 같은 드문
# 음절이 통째로 빠져 있다 — FONT(title.ttf)는 한글 전체 11172자를 담고 있다).
_FALLBACK_FONT = FONT


@functools.lru_cache(maxsize=None)
def _font_chars(font_path: str) -> frozenset[int]:
    try:
        return frozenset(TTFont(font_path).getBestCmap().keys())
    except Exception:
        return frozenset()


def _resolve_font(font_path: Path, texts: list[str], log) -> Path:
    """선택한 폰트가 실제로 쓸 글자를 다 그릴 수 있는지 확인하고, 못 그리면
    전체 글자를 담은 폰트로 조용히 대체한다(빈 네모 방지)."""
    covered = _font_chars(str(font_path))
    if not covered:
        return font_path
    missing = {ch for text in texts for ch in text if not ch.isspace() and ord(ch) not in covered}
    if missing and font_path != _FALLBACK_FONT:
        log(f"자막 폰트({font_path.name})에 없는 글자({''.join(sorted(missing))})가 있어 기본 폰트로 대체")
        return _FALLBACK_FONT
    return font_path


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg") or "ffmpeg"


def _run(args: list[str], log) -> bool:
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"    ffmpeg 실패: {r.stderr.strip().splitlines()[-1][:160] if r.stderr else '?'}")
        return False
    return True


def _esc(text: str) -> str:
    """drawtext 는 텍스트를 파일로 넘기지만, 경로/색 인자는 이스케이프가 필요하다."""
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _fit_pad(w: int, h: int, blur: int = 30) -> str:
    """원본 종횡비를 안 잃는 합성 조각(2026-09-09, 세로 크롭 개선).

    기존엔 scale+crop 으로 "꽉 채워 자르기"만 해서, 원본이 9:16 이 아니면 피사체가
    잘려 나갔다(실측: 보자기 포장 사진이 알아볼 수 없는 천 조각으로 잘림). 인스타/
    틱톡처럼 흐린 배경(꽉 채움) 위에 원본 전체(맞춤, 안 잘림)를 얹는 레터박스로
    바꾼다. filter_complex 조각이라 호출부는 [0:v] 를 앞에 붙이고 마지막에 라벨을
    달아 -filter_complex/-map 으로 실행해야 한다.
    """
    return (
        f"split=2[bg][fg];"
        f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"gblur=sigma={blur}[bgb];"
        f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
    )


def _footage_ok(path: Path) -> bool:
    """이 영상/GIF 를 "실제 영상 그대로" 경로로 써도 될 만큼 해상도가 충분한지.

    ffmpeg -i 의 stderr 에서 스트림 해상도를 정규식으로 읽는다(ffprobe 없이 이미
    쓰는 ffmpeg 바이너리 하나로 처리). 해상도를 못 읽으면 기존 동작을 유지하려고
    안전하게 True 를 돌려준다.
    """
    try:
        out = subprocess.run([_ffmpeg(), "-i", str(path)], capture_output=True, text=True).stderr
        m = re.search(r",\s*(\d{2,5})x(\d{2,5})[,\s]", out)
        if not m:
            return True
        return max(int(m.group(1)), int(m.group(2))) >= _MIN_FOOTAGE_WIDTH
    except Exception:
        return True


def _wrap_hook(text: str, max_chars: int = 11) -> str:
    """훅 자막(스키마상 한 줄·20자 이내지만, 대본 LLM이 씬1 캡션을 그대로 훅에
    복사해 줄바꿈이 이미 섞여 들어올 때가 있다 — 2026-09-10 실측)을 큰 폰트로
    화면 폭 안에 넣기 위해 최대 2줄로 접는다. 기존 줄바꿈은 먼저 공백으로 합쳐
    하나의 문장으로 본 다음, 중간에 가까운 공백에서 새로 자른다(그래야 원래
    줄바꿈 위치까지 끼어들어 3줄로 쪼개지는 걸 막는다). 공백이 없으면 그냥 글자
    수로 자른다 — 92pt 기준 1080px 폭에 11자가 딱 맞는다."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    if " " in text:
        mid = len(text) / 2
        best = min((i for i, c in enumerate(text) if c == " "), key=lambda i: abs(i - mid))
        first, second = text[:best].strip(), text[best + 1:].strip()
    else:
        first, second = text[:max_chars], text[max_chars:]
    return f"{first}\n{second}" if second else first


def _sub_filter(caption: str, tmp: Path, idx: int, font_path: Path,
                 color_hex: str, box_style: str, is_hook: bool = False) -> str:
    """자막 번인 필터.

    box_style: "dark"(검정 반투명 박스, 기본)/"light"(흰 반투명 박스)/"none"(테두리 글자).
    is_hook: 훅(첫 씬, 0~2초 승부 문구)이면 유저가 참고한 클립(비비 본인 인기
    클립 5개, 2026-09-10 실측)처럼 배경 박스 없이 굵은 흰 글자+검은 테두리로,
    화면 정중앙에 훨씬 크게 얹는다 — box_style 선택과 무관하게 항상 이 스타일을
    강제한다(훅은 항상 이렇게 보여야 승부가 나므로 유저가 고른 박스 스타일을
    따르지 않는다).
    """
    if not caption.strip():
        return ""
    txt = tmp / f"sub{idx}.txt"
    if is_hook:
        box = "borderw=6:bordercolor=black@0.9"
        fontsize = 92
        y = "(h-text_h)/2"
        txt.write_text(_wrap_hook(caption), encoding="utf-8")
    else:
        if box_style == "light":
            box = "box=1:boxcolor=white@0.65:boxborderw=28"
        elif box_style == "none":
            box = "borderw=3:bordercolor=black@0.8"
        else:
            box = "box=1:boxcolor=black@0.55:boxborderw=28"
        fontsize = 64
        y = f"h-text_h-{SUB_BOTTOM_MARGIN}"
        txt.write_text(caption.strip(), encoding="utf-8")
    return (
        f",drawtext=fontfile='{_esc(str(font_path))}':textfile='{_esc(str(txt))}'"
        f":fontcolor={color_hex}:fontsize={fontsize}:line_spacing=16:text_align=center"
        f":{box}"
        f":x=(w-text_w)/2:y={y}"
    )


def _kenburns_clip(photo: Path, seconds: float, caption: str, out: Path,
                   tmp: Path, idx: int, log, font_path: Path, color_hex: str,
                   box_style: str, is_hook: bool = False) -> bool:
    """사진 1장 → 천천히 줌인하는 9:16 클립.

    zoompan 은 저해상도에서 떨림이 생겨서, 2배로 키워 크롭한 뒤 줌하고 마지막에 1080 으로 내린다.
    """
    frames = max(1, int(seconds * FPS))
    vf = (
        f"[0:v]{_fit_pad(W, H)},"
        f"scale={W * 2}:{H * 2},"
        f"zoompan=z='min(zoom+0.0012,1.18)':d={frames}"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS}"
        f"{_sub_filter(caption, tmp, idx, font_path, color_hex, box_style, is_hook)},"
        f"fade=t=in:st=0:d=0.3,fade=t=out:st={max(0, seconds - 0.3):.2f}:d=0.3,"
        f"format=yuv420p,setsar=1[outv]"
    )
    return _run([_ffmpeg(), "-y", "-loop", "1", "-i", str(photo), "-t", f"{seconds:.2f}",
                 "-filter_complex", vf, "-map", "[outv]", "-r", str(FPS), "-c:v", "libx264",
                 "-preset", "medium", "-crf", "20", "-an", str(out)], log)


def _video_clip(src: Path, seconds: float, caption: str, out: Path,
                tmp: Path, idx: int, log, font_path: Path, color_hex: str,
                box_style: str, loop: bool = False, is_hook: bool = False) -> bool:
    """영상(AI 변환본 또는 움짤/원본 영상) → 9:16 로 맞추고 자막을 얹어 지정 길이로 자른다.

    loop=True 면 소재가 씬보다 짧아도 반복해서 채운다(움짤은 보통 4~8초라 필요하다).
    """
    vf = (
        f"[0:v]{_fit_pad(W, H)},fps={FPS}"
        f"{_sub_filter(caption, tmp, idx, font_path, color_hex, box_style, is_hook)},"
        f"fade=t=in:st=0:d=0.3,fade=t=out:st={max(0, seconds - 0.3):.2f}:d=0.3,"
        f"format=yuv420p,setsar=1[outv]"
    )
    pre = ["-stream_loop", "-1"] if loop else []
    return _run([_ffmpeg(), "-y", *pre, "-i", str(src), "-t", f"{seconds:.2f}",
                 "-filter_complex", vf, "-map", "[outv]", "-r", str(FPS), "-c:v", "libx264",
                 "-preset", "medium", "-crf", "20", "-an", str(out)], log)


def _tts_voice() -> str | None:
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    except Exception:
        return None
    names = {l.split()[0] for l in out.splitlines() if "ko_KR" in l}
    return next((v for v in TTS_VOICE_PREFS if v in names), None)


def _tts(text: str, out_aiff: Path, voice: str, log) -> float:
    """나레이션 한 줄을 음성 파일로. 길이(초)를 돌려준다(실패하면 0)."""
    if not text.strip():
        return 0.0
    r = subprocess.run(["say", "-v", voice, "-o", str(out_aiff), text.strip()],
                       capture_output=True, text=True)
    if r.returncode != 0 or not out_aiff.exists():
        log(f"    TTS 실패: {r.stderr[:100]}")
        return 0.0
    probe = subprocess.run([_ffmpeg(), "-i", str(out_aiff)], capture_output=True, text=True)
    for line in probe.stderr.splitlines():
        if "Duration:" in line:
            hh, mm, ss = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(hh) * 3600 + int(mm) * 60 + float(ss)
    return 0.0


def render(
    script: dict,
    photo_paths: dict[int, Path],
    out_path: str | Path,
    narration: str = "",
    font: str = "default",
    text_color: str = "auto",
    box_style: str = "auto",
    log=print,
) -> Path | None:
    """대본 + 사진으로 mp4 를 만든다. 성공하면 경로, 실패하면 None.

    photo_paths 는 {사진번호: 파일경로}. 대본의 scenes[].image 가 이 키를 가리킨다.
    narration 은 "subtitle"(자막만) | "tts"(AI 성우). 비우면 config 기본값.
    font/text_color/box_style 은 자막 타이포그래피(2026-09-08 클립 고도화) —
    FONT_CHOICES/TEXT_COLOR_CHOICES/BOX_STYLE_CHOICES 에 없는 값이 오면 기본값으로 폴백한다.
    """
    out_path = Path(out_path)
    narration = (narration or config.SHORTFORM_NARRATION).lower()
    scenes = script.get("scenes") or []
    if not scenes:
        log("대본에 씬이 없습니다.")
        return None

    font_path = FONT_CHOICES.get(font, FONT)
    if not font_path.exists():
        log(f"자막 폰트가 없습니다: {font_path} — 기본 폰트로 대체")
        font_path = FONT
    if not font_path.exists():
        log(f"기본 자막 폰트도 없습니다: {font_path}")
        return None

    # 훅은 첫 씬 자막 위에 얹지 않고, 첫 씬의 자막을 훅으로 바꿔 0~2초 승부를 살린다.
    hook = (script.get("hook") or "").strip()
    effective_captions = [
        hook if i == 0 and hook else (sc.get("caption") or "").strip()
        for i, sc in enumerate(scenes)
    ]
    font_path = _resolve_font(font_path, effective_captions, log)

    color_hex = TEXT_COLOR_CHOICES.get(text_color, TEXT_COLOR_CHOICES["auto"])
    box_style = box_style if box_style in BOX_STYLE_CHOICES else "auto"
    log(f"자막 스타일: 폰트={font_path.name}, 글자색={color_hex}, 배경={box_style}")

    voice = _tts_voice() if narration == "tts" else None
    if narration == "tts" and not voice:
        log("한국어 TTS 음성을 못 찾아 자막만으로 만듭니다.")
        narration = "subtitle"

    tmpdir = Path(tempfile.mkdtemp(prefix="shortform_"))
    clips, audios = [], []
    try:
        for i, sc in enumerate(scenes):
            n = sc.get("n") or (i + 1)
            seconds = float(sc.get("seconds") or 5)
            caption = (sc.get("caption") or "").strip()
            if i == 0 and hook:
                caption = hook

            # TTS 면 나레이션이 잘리지 않게 씬 길이를 음성에 맞춘다(+여유 0.6초).
            audio = None
            if narration == "tts":
                aiff = tmpdir / f"n{n}.aiff"
                dur = _tts(sc.get("narration") or "", aiff, voice, log)
                if dur > 0:
                    audio = aiff
                    seconds = max(seconds, dur + 0.6)

            photo = photo_paths.get(sc.get("image"))
            clip = tmpdir / f"c{n:02d}.mp4"
            made = False
            is_hook = i == 0 and bool(hook)

            # 움짤/원본 영상은 이미 '실제로 찍은 움직임'이다. AI 변환보다 진짜고
            # 공짜라 그대로 쓴다(2026-09-07: 글 작성 시 첨부한 원본 영상도 포함하도록
            # 확장 — modules/clip_generator.py 가 그 사진 번호에 원본 영상 파일을
            # 대신 넘겨준다). 단, 해상도가 너무 낮으면(2026-09-09 실측: 네이버 10MB
            # 제한 때문에 240px까지 압축된 GIF도 있음) 1080폭으로 늘렸을 때 뭉개지므로
            # 정지 프레임 하나만 뽑아 켄번즈로 대체한다.
            if photo and photo.suffix.lower() in _REAL_FOOTAGE_EXTS:
                if _footage_ok(photo):
                    made = _video_clip(photo, seconds, caption, clip, tmpdir, n, log,
                                        font_path, color_hex, box_style, loop=True, is_hook=is_hook)
                    if made:
                        log(f"  씬{n}: 실제 영상/움짤 사용(AI 변환 건너뜀)")
                else:
                    log(f"  씬{n}: 원본 화질이 낮아 정지 프레임으로 대체")
                    frame = tmpdir / f"still{n}.jpg"
                    if _run([_ffmpeg(), "-y", "-i", str(photo), "-frames:v", "1", str(frame)], log):
                        photo = frame

            if not made and sc.get("motion_worth") == "high" and photo and video_engine.enabled():
                log(f"  씬{n}: AI 영상 변환 시도")
                ai = video_engine.generate_clip(
                    photo, sc.get("motion_prompt") or "", seconds, tmpdir / f"ai{n}.mp4", log)
                if ai:
                    made = _video_clip(ai, seconds, caption, clip, tmpdir, n, log,
                                        font_path, color_hex, box_style, is_hook=is_hook)
                if not made:
                    log(f"  씬{n}: AI 변환 안 됨 → 켄번즈로 대체")

            if not made and photo:
                made = _kenburns_clip(photo, seconds, caption, clip, tmpdir, n, log,
                                       font_path, color_hex, box_style, is_hook=is_hook)

            if not made:
                log(f"  씬{n}: 쓸 사진이 없어 건너뜁니다.")
                continue
            clips.append(clip)
            audios.append((audio, seconds))
            log(f"  씬{n}: {seconds:.1f}초 클립 완성")

        if not clips:
            log("만들어진 클립이 없습니다.")
            return None

        listfile = tmpdir / "list.txt"
        listfile.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
        silent = tmpdir / "silent.mp4"
        if not _run([_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
                     "-c", "copy", str(silent)], log):
            return None

        if narration != "tts" or not any(a for a, _ in audios):
            shutil.move(str(silent), str(out_path))
        else:
            # 씬 시작 시각에 맞춰 나레이션을 이어 붙인 트랙을 만들고 영상에 얹는다.
            parts, t = [], 0.0
            for (aiff, sec) in audios:
                if aiff:
                    pad = tmpdir / f"p{len(parts)}.wav"
                    _run([_ffmpeg(), "-y", "-i", str(aiff), "-af",
                          f"adelay={int(t * 1000)}|{int(t * 1000)}", "-ar", "44100",
                          "-ac", "2", str(pad)], log)
                    if pad.exists():
                        parts.append(pad)
                t += sec
            track = tmpdir / "voice.wav"
            if len(parts) == 1:
                shutil.copy(parts[0], track)
            else:
                inputs = []
                for p in parts:
                    inputs += ["-i", str(p)]
                _run([_ffmpeg(), "-y", *inputs, "-filter_complex",
                      f"amix=inputs={len(parts)}:duration=longest:normalize=0",
                      "-ar", "44100", "-ac", "2", str(track)], log)
            if track.exists():
                _run([_ffmpeg(), "-y", "-i", str(silent), "-i", str(track),
                      "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest",
                      str(out_path)], log)
            else:
                shutil.move(str(silent), str(out_path))

        if out_path.exists():
            mb = out_path.stat().st_size / 1024 / 1024
            log(f"영상 완성: {out_path} ({mb:.1f}MB, 씬 {len(clips)}개)")
            return out_path
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
