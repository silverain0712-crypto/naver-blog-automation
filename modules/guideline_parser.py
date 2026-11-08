"""협찬 가이드라인 파일에서 텍스트(지시사항)를 추출한다.

지원 형식: xlsx/xlsm/xls(openpyxl), pdf(pypdf), docx(python-docx),
          txt/csv/tsv/md(직접 디코드), 이미지(Claude 비전 전사).
추출 텍스트는 post_generator 가 시스템 프롬프트에 '가이드라인'으로 주입해
글이 광고주 지침(필수 키워드/해시태그/글자수/금지사항/공정위 문구 등)을 따르게 한다.
"""

from __future__ import annotations

import io

_MAX_CHARS = 12000  # 프롬프트 폭주 방지 상한


def extract_text(data: bytes, filename: str) -> str:
    """파일 바이트에서 가이드라인 텍스트를 추출. 실패해도 예외 대신 안내 문자열 반환."""
    name = (filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    try:
        if ext in ("xlsx", "xlsm", "xls"):
            text = _from_xlsx(data)
        elif ext == "pdf":
            text = _from_pdf(data)
        elif ext == "docx":
            text = _from_docx(data)
        elif ext in ("txt", "csv", "tsv", "md"):
            text = data.decode("utf-8", "ignore")
        elif ext in ("png", "jpg", "jpeg", "webp", "gif", "heic"):
            text = _from_image(data)
        else:
            return f"(지원하지 않는 가이드 파일 형식: .{ext} — 파일명 {filename})"
    except Exception as e:
        return f"(가이드 파일 파싱 실패: {str(e)[:150]})"

    text = (text or "").strip()
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + "\n…(가이드 뒷부분 생략)"
    return text


def _from_xlsx(data: bytes) -> str:
    import openpyxl

    # 일부 파일은 스타일 정보가 비표준이라 기본 로더가 실패 → read_only 로 우회.
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out: list[str] = []
    for ws in wb.worksheets:
        out.append(f"[시트: {ws.title}]")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
            if cells:
                out.append(" | ".join(cells))
    return "\n".join(out)


def _from_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    import re

    reader = PdfReader(io.BytesIO(data))
    raw = "\n".join((p.extract_text() or "") for p in reader.pages)
    # 슬라이드형 PDF 는 단어마다 줄바꿈·이중 공백이 들어가 글자 수만 먹는다(실측: 미소 가이드 7.9K자 중 상당수가 공백).
    # 공백·줄바꿈을 한 칸으로 줄이고, 글머리 기호(● ■ ○ ※ ❌ ✅ ⭐)에서만 줄을 나눈다.
    flat = re.sub(r"\s+", " ", raw)
    flat = re.sub(r"\s*([●■○※❌✅⭐])", r"\n\1", flat)
    return flat.strip()


def _from_docx(data: bytes) -> str:
    import docx

    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(p for p in parts if p.strip())


def is_failure(text: str) -> bool:
    """extract_text 가 돌려준 안내 문자열(파싱 실패·미지원 형식)인지."""
    t = (text or "").strip()
    return t.startswith("(가이드 파일 파싱 실패") or t.startswith("(지원하지 않는 가이드 파일 형식")


_IMG_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}


def _from_image(data: bytes) -> str:
    """이미지 가이드를 비전으로 읽어 지시사항 텍스트로 옮긴다.

    2026-11-08: 예전엔 Anthropic API(client.messages.create)를 직접 불러 키가 없으면 조용히 실패했다.
    이제 llm.call_json 을 거쳐 LLM_BACKEND=codex 일 때 ChatGPT 구독(Codex CLI, 사진은 -i 첨부)으로 처리한다."""
    import config
    from modules.llm import call_json, prepare_image_block

    out = call_json(
        model=config.VISION_MODEL,
        system=(
            "너는 협찬 가이드라인 이미지를 읽어 그 안의 모든 지시사항을 한국어로 빠짐없이 옮겨 적는다. "
            "요약하지 말고 필수 키워드·해시태그·제목/글자수 규정·금지사항·필수 사진/영상 요구를 그대로 나열하라."
        ),
        content=[
            prepare_image_block(data),
            {"type": "text", "text": "이 가이드라인 이미지의 내용을 모두 텍스트로 옮겨 text 에 담아줘."},
        ],
        schema=_IMG_SCHEMA,
        max_tokens=4000,
    )
    return out.get("text", "")
