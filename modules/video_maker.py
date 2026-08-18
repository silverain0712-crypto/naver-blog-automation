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

import shutil
import subprocess
import tempfile
from pathlib import Path

import config
from modules import video_engine

W, H, FPS = 1080, 1920, 30
# 자막 안전 영역: 네이버 클립·쇼츠 UI(하단 버튼/설명)에 가리지 않는 높이.
SUB_BOTTOM_MARGIN = 420
FONT = config.BASE_DIR / "fonts" / "title.ttf"
# macOS say 한국어 음성. 'Yuna' 가 없는 시스템도 있어 실행 시 확인 후 폴백한다.
TTS_VOICE_PREFS = ("Yuna", "Flo", "Reed", "Eddy")


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


def _sub_filter(caption: str, tmp: Path, idx: int) -> str:
    """자막 번인 필터. 굵은 흰 글씨 + 반투명 검정 박스(어떤 사진 위에서도 읽힌다)."""
    if not caption.strip():
        return ""
    txt = tmp / f"sub{idx}.txt"
    txt.write_text(caption.strip(), encoding="utf-8")
    return (
        f",drawtext=fontfile='{_esc(str(FONT))}':textfile='{_esc(str(txt))}'"
        f":fontcolor=white:fontsize=64:line_spacing=16"
        f":box=1:boxcolor=black@0.55:boxborderw=28"
        f":x=(w-text_w)/2:y=h-text_h-{SUB_BOTTOM_MARGIN}"
    )


def _kenburns_clip(photo: Path, seconds: float, caption: str, out: Path,
                   tmp: Path, idx: int, log) -> bool:
    """사진 1장 → 천천히 줌인하는 9:16 클립.

    zoompan 은 저해상도에서 떨림이 생겨서, 2배로 키워 크롭한 뒤 줌하고 마지막에 1080 으로 내린다.
    """
    frames = max(1, int(seconds * FPS))
    vf = (
        f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,"
        f"crop={W * 2}:{H * 2},"
        f"zoompan=z='min(zoom+0.0012,1.18)':d={frames}"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS}"
        f"{_sub_filter(caption, tmp, idx)},"
        f"fade=t=in:st=0:d=0.3,fade=t=out:st={max(0, seconds - 0.3):.2f}:d=0.3,"
        f"format=yuv420p,setsar=1"
    )
    return _run([_ffmpeg(), "-y", "-loop", "1", "-i", str(photo), "-t", f"{seconds:.2f}",
                 "-vf", vf, "-r", str(FPS), "-c:v", "libx264", "-preset", "medium",
                 "-crf", "20", "-an", str(out)], log)


def _video_clip(src: Path, seconds: float, caption: str, out: Path,
                tmp: Path, idx: int, log) -> bool:
    """AI 변환 영상 → 9:16 로 맞추고 자막을 얹어 지정 길이로 자른다."""
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}"
        f"{_sub_filter(caption, tmp, idx)},"
        f"fade=t=in:st=0:d=0.3,fade=t=out:st={max(0, seconds - 0.3):.2f}:d=0.3,"
        f"format=yuv420p,setsar=1"
    )
    return _run([_ffmpeg(), "-y", "-i", str(src), "-t", f"{seconds:.2f}",
                 "-vf", vf, "-r", str(FPS), "-c:v", "libx264", "-preset", "medium",
                 "-crf", "20", "-an", str(out)], log)


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
    log=print,
) -> Path | None:
    """대본 + 사진으로 mp4 를 만든다. 성공하면 경로, 실패하면 None.

    photo_paths 는 {사진번호: 파일경로}. 대본의 scenes[].image 가 이 키를 가리킨다.
    narration 은 "subtitle"(자막만) | "tts"(AI 성우). 비우면 config 기본값.
    """
    out_path = Path(out_path)
    narration = (narration or config.SHORTFORM_NARRATION).lower()
    scenes = script.get("scenes") or []
    if not scenes:
        log("대본에 씬이 없습니다.")
        return None
    if not FONT.exists():
        log(f"자막 폰트가 없습니다: {FONT}")
        return None

    voice = _tts_voice() if narration == "tts" else None
    if narration == "tts" and not voice:
        log("한국어 TTS 음성을 못 찾아 자막만으로 만듭니다.")
        narration = "subtitle"

    tmpdir = Path(tempfile.mkdtemp(prefix="shortform_"))
    clips, audios = [], []
    try:
        # 훅은 첫 씬 자막 위에 얹지 않고, 첫 씬의 자막을 훅으로 바꿔 0~2초 승부를 살린다.
        hook = (script.get("hook") or "").strip()

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

            if sc.get("motion_worth") == "high" and photo and video_engine.enabled():
                log(f"  씬{n}: AI 영상 변환 시도")
                ai = video_engine.generate_clip(
                    photo, sc.get("motion_prompt") or "", seconds, tmpdir / f"ai{n}.mp4", log)
                if ai:
                    made = _video_clip(ai, seconds, caption, clip, tmpdir, n, log)
                if not made:
                    log(f"  씬{n}: AI 변환 안 됨 → 켄번즈로 대체")

            if not made and photo:
                made = _kenburns_clip(photo, seconds, caption, clip, tmpdir, n, log)

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
