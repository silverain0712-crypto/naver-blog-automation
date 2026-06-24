"""영상 → GIF 변환. 고품질 2-pass 팔레트 방식(ffmpeg).

네이버 블로그 업로드 제한: 10MB 이하.
"""

import os
import subprocess
import tempfile


def _ffmpeg_exe() -> str:
    """imageio_ffmpeg 번들 ffmpeg → 없으면 시스템 ffmpeg(Mac brew 등) 사용."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def convert_to_gif(
    video_bytes: bytes,
    start: float,
    end: float,
    width: int = 480,
    fps: int = 10,
) -> tuple[bytes, float]:
    """영상 bytes → (GIF bytes, 파일크기 MB).

    2-pass 팔레트 방식으로 색상 품질을 최대화한다.
    """
    ffmpeg = _ffmpeg_exe()
    duration = round(end - start, 2)
    vf_base = f"fps={fps},scale={width}:-2:flags=lanczos"

    with tempfile.TemporaryDirectory() as tmp:
        in_path  = os.path.join(tmp, "input.mp4")
        palette  = os.path.join(tmp, "palette.png")
        out_path = os.path.join(tmp, "output.gif")

        with open(in_path, "wb") as f:
            f.write(video_bytes)

        # pass 1: 팔레트 생성 (색상 품질 향상)
        subprocess.run([
            ffmpeg, "-y",
            "-ss", str(start), "-t", str(duration),
            "-i", in_path,
            "-vf", f"{vf_base},palettegen=stats_mode=diff",
            palette,
        ], capture_output=True, check=True)

        # pass 2: 팔레트 적용해 GIF 생성
        subprocess.run([
            ffmpeg, "-y",
            "-ss", str(start), "-t", str(duration),
            "-i", in_path, "-i", palette,
            "-lavfi", f"{vf_base}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5",
            out_path,
        ], capture_output=True, check=True)

        with open(out_path, "rb") as f:
            gif_bytes = f.read()

    size_mb = len(gif_bytes) / (1024 * 1024)
    return gif_bytes, size_mb
