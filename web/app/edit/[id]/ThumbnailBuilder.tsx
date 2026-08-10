"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// ── 비비 고정 템플릿 상수 ──
// 미리캔버스 원본 썸네일 3장을 픽셀 측정해서 맞춘 값이다(잉크 박스 기준).
// 좌표는 '글자가 실제로 칠해지는 영역'을 뜻하며, baseline 이 아니다 — drawInk() 참고.
const S = 1080;
const BG = "#EEE9E3"; // 크림 배경 (238,233,227)
const ACCENT = "#568A35"; // @bbnation / REVIEW / 가로선 (86,138,53)
const TITLE_C = "#41661B"; // 하단 제목 (65,102,27)
// 아치: 원본은 캔버스 정중앙이 아니라 살짝 오른쪽(중심 551)이고 하단 모서리는 직각이다.
const ARCH_X = 195;
const ARCH_W = 713;
const ARCH_TOP = 105;
const ARCH_BOTTOM = 855;
const BRAND_SIZE = 25;
const BRAND_INK_X = 35; // '@' 왼쪽 끝이 닿는 x
const BRAND_CY = 54; // 잉크 세로 중심
const REVIEW_SIZE = 34;
const REVIEW_INK_RIGHT = 1018; // 'W' 오른쪽 끝이 닿는 x
const REVIEW_CY = 74.5;
// 원본의 REVIEW 는 텍스트 상자를 세로로 늘려 놓은 상태(가로폭 대비 글자가 높다).
// 같은 서체로 폭·높이를 동시에 맞추려면 세로 스케일이 필요하다.
const REVIEW_STRETCH = 1.4;
const LINE_Y = 57;
const LINE_X0 = 195;
const LINE_X1 = 872;
const LINE_W = 3;
const TITLE_SIZE = 70;
const TITLE_STEP = 114;
const TITLE_CENTER_Y = 966;
const TITLE_MAX_W = 980; // 넘치면 자동으로 줄인다(원본은 손으로 맞췄지만 여긴 자동)
const FONT = "Ghanachocolate";

const THUMB_PATH_SUFFIX = "/thumb.png"; // `${id}/thumb.png`

type Props = {
  draftId: string;
  images: string[]; // 이미 올라간 사진 경로들
  candidates: string[][]; // 문구 후보(각 후보 = 1~2줄)
  // apply 시점의 최신 본문/데이터는 함수로 받아 클로저 캡처 문제를 피한다.
  getBody: () => string;
  getData: () => Record<string, unknown>;
  onApplied: (newBody: string, newImages: string[], newData: Record<string, unknown>) => void;
};

function archPath(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number) {
  const r = w / 2; // 상단 반원 반지름
  ctx.beginPath();
  ctx.moveTo(x, y + r);
  ctx.arc(x + r, y + r, r, Math.PI, 2 * Math.PI, false); // 상단 반원(좌→위→우)
  ctx.lineTo(x + w, y + h); // 하단은 직각(원본과 동일)
  ctx.lineTo(x, y + h);
  ctx.closePath();
}

/** 글자를 '잉크 박스' 기준으로 놓는다. baseline 기준으로 두면 문자열마다 위아래로 흔들린다. */
function drawInk(
  ctx: CanvasRenderingContext2D,
  text: string,
  cy: number,
  anchor: { left: number } | { right: number } | { center: number },
) {
  const m = ctx.measureText(text);
  const y = cy - (m.actualBoundingBoxDescent - m.actualBoundingBoxAscent) / 2;
  const x =
    "left" in anchor
      ? anchor.left + m.actualBoundingBoxLeft
      : "right" in anchor
        ? anchor.right - m.actualBoundingBoxRight
        : anchor.center;
  return { x, y };
}

function drawCover(
  ctx: CanvasRenderingContext2D,
  bmp: ImageBitmap,
  dx: number,
  dy: number,
  dw: number,
  dh: number,
) {
  const ir = bmp.width / bmp.height;
  const dr = dw / dh;
  let sx = 0,
    sy = 0,
    sw = bmp.width,
    sh = bmp.height;
  if (ir > dr) {
    sw = bmp.height * dr;
    sx = (bmp.width - sw) / 2;
  } else {
    sh = bmp.width / dr;
    sy = (bmp.height - sh) / 2;
  }
  ctx.drawImage(bmp, sx, sy, sw, sh, dx, dy, dw, dh);
}

function splitLines(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .slice(0, 2);
}

export default function ThumbnailBuilder({
  draftId,
  images,
  candidates,
  getBody,
  getData,
  onApplied,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [fontReady, setFontReady] = useState(false);
  const [bitmap, setBitmap] = useState<ImageBitmap | null>(null);
  const [phrase, setPhrase] = useState<string>(() =>
    candidates[0] ? candidates[0].join("\n") : "",
  );
  const [thumbUrls, setThumbUrls] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [open, setOpen] = useState(false);

  // 가나초콜릿체 로드(썸네일 만들 때만 — lazy)
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const ff = new FontFace(FONT, "url(/fonts/Ghanachocolate.woff2)");
    ff.load()
      .then((f) => {
        document.fonts.add(f);
        if (alive) setFontReady(true);
      })
      .catch(() => alive && setFontReady(true)); // 실패해도 폴백 폰트로 그림
    return () => {
      alive = false;
    };
  }, [open]);

  // 이미 올라간 사진들의 미리보기 URL(중앙 이미지 빠른 선택용)
  useEffect(() => {
    if (!open || images.length === 0) return;
    let alive = true;
    fetch(`/api/drafts/${draftId}/images`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { urls: [] }))
      .then(({ urls }) => alive && setThumbUrls(urls ?? []))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [open, draftId, images.length]);

  // 캔버스 렌더
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, S, S);
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, S, S);

    // 헤더: @bbnation + 가로선 + REVIEW
    ctx.fillStyle = ACCENT;
    ctx.strokeStyle = ACCENT;
    ctx.textBaseline = "alphabetic";
    ctx.textAlign = "left";

    ctx.font = `${BRAND_SIZE}px "${FONT}", sans-serif`;
    const b = drawInk(ctx, "@bbnation", BRAND_CY, { left: BRAND_INK_X });
    ctx.fillText("@bbnation", b.x, b.y);

    ctx.font = `${REVIEW_SIZE}px "${FONT}", sans-serif`;
    const r = drawInk(ctx, "REVIEW", REVIEW_CY, { right: REVIEW_INK_RIGHT });
    ctx.save();
    ctx.translate(0, REVIEW_CY);
    ctx.scale(1, REVIEW_STRETCH);
    ctx.translate(0, -REVIEW_CY);
    ctx.fillText("REVIEW", r.x, r.y);
    ctx.restore();

    ctx.lineWidth = LINE_W;
    ctx.beginPath();
    ctx.moveTo(LINE_X0, LINE_Y);
    ctx.lineTo(LINE_X1, LINE_Y);
    ctx.stroke();

    // 아치 사진(cover-crop → 아치 마스크)
    const boxH = ARCH_BOTTOM - ARCH_TOP;
    ctx.save();
    archPath(ctx, ARCH_X, ARCH_TOP, ARCH_W, boxH);
    if (bitmap) {
      ctx.clip();
      drawCover(ctx, bitmap, ARCH_X, ARCH_TOP, ARCH_W, boxH);
    } else {
      ctx.fillStyle = "#E2DCD0"; // 사진 미선택 placeholder
      ctx.fill();
    }
    ctx.restore();

    // 하단 제목(초록, 가운데, 1~2줄). 폭이 넘치면 자동 축소.
    const lines = splitLines(phrase);
    ctx.fillStyle = TITLE_C;
    ctx.strokeStyle = TITLE_C;
    ctx.textAlign = "center";
    ctx.lineWidth = 1.5;
    let size = TITLE_SIZE;
    ctx.font = `${size}px "${FONT}", sans-serif`;
    const widest = Math.max(0, ...lines.map((l) => ctx.measureText(l).width));
    if (widest > TITLE_MAX_W) size = Math.floor((size * TITLE_MAX_W) / widest);
    ctx.font = `${size}px "${FONT}", sans-serif`;
    const step = (TITLE_STEP * size) / TITLE_SIZE;
    const start = TITLE_CENTER_Y - ((lines.length - 1) * step) / 2;
    lines.forEach((ln, i) => {
      const t = drawInk(ctx, ln, start + i * step, { center: S / 2 });
      ctx.fillText(ln, t.x, t.y);
      ctx.strokeText(ln, t.x, t.y); // 살짝 두껍게(원본 굵기 재현)
    });
  }, [bitmap, phrase]);

  useEffect(() => {
    if (open) draw();
  }, [open, fontReady, draw]);

  // 중앙 이미지 선택(로컬 파일 또는 이미 올라간 사진)
  async function pickFile(file: File) {
    try {
      const bmp = await createImageBitmap(file);
      setBitmap(bmp);
    } catch {
      setMsg("이미지를 읽지 못했어요. 다른 사진으로 시도해 주세요.");
    }
  }
  async function pickUrl(url: string) {
    try {
      const res = await fetch(url);
      const blob = await res.blob();
      const bmp = await createImageBitmap(blob);
      setBitmap(bmp);
    } catch {
      setMsg("이 사진은 여기서 못 불러왔어요. '다른 사진 올리기'로 넣어 주세요.");
    }
  }

  async function apply() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (!bitmap) {
      setMsg("가운데 이미지를 먼저 골라 주세요.");
      return;
    }
    if (splitLines(phrase).length === 0) {
      setMsg("썸네일 문구를 입력해 주세요.");
      return;
    }
    setBusy(true);
    setMsg("");
    try {
      const blob: Blob = await new Promise((resolve, reject) =>
        canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG 변환 실패"))), "image/png"),
      );
      // 1) 서명 업로드 URL 발급 → 직접 PUT(Vercel 함수 우회)
      const r = await fetch(`/api/drafts/${draftId}/thumbnail`, { method: "POST" });
      if (!r.ok) throw new Error("업로드 URL 발급 실패");
      const { path, url } = (await r.json()) as { path: string; url: string };
      const put = await fetch(url, {
        method: "PUT",
        headers: { "content-type": "image/png", "x-upsert": "true" },
        body: blob,
      });
      if (!put.ok) throw new Error(`썸네일 업로드 실패 (${put.status})`);

      // 2) images 맨 뒤에 썸네일 추가 + 본문 맨 위에 [사진N] 한 줄(대표사진 배선)
      const data = getData();
      const body = getBody();
      const base = (images || []).filter((p) => !p.endsWith(THUMB_PATH_SUFFIX));
      const n = base.length + 1; // 우리 마커 번호(항상 사진들보다 큼 → 충돌 없음)
      const marker = `[사진${n}]`;
      // 대표사진 자리 = 인사말('안녕하세요 … 비비') 바로 아래.
      // 본문 맨 위(=인사말 앞)는 발행기가 '큰 제목/요약' 블록으로 파싱하는 구간이라
      // 거기에 마커를 두면 사진 대신 '[사진N]' 글자가 제목으로 찍힌다.
      const lines = body.split("\n").filter((line) => line.trim() !== marker);
      const gi = lines.findIndex((l) => l.includes("안녕하세요") && l.includes("비비"));
      if (gi >= 0) lines.splice(gi + 1, 0, "", marker);
      else lines.unshift(marker, "");
      const newBody = lines.join("\n");
      const newImages = [...base, path];
      const newData = { ...data, thumb: { path, marker: n } };

      const patch = await fetch(`/api/drafts/${draftId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ images: newImages, body: newBody, data: newData }),
      });
      if (!patch.ok) throw new Error("초안 저장 실패");

      onApplied(newBody, newImages, newData);
      setMsg("✅ 썸네일이 대표사진으로 들어갔어요. (인사말 아래 [사진" + n + "])");
    } catch (e) {
      setMsg("실패: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="mt-4 w-full rounded-lg border border-emerald-300 bg-emerald-50 py-2.5 text-sm font-semibold text-emerald-700"
      >
        🖼️ 썸네일 만들기
      </button>
    );
  }

  const fieldC = "rounded-lg border border-neutral-300 px-3 py-2 text-base w-full bg-white";

  return (
    <div className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50/50 p-3">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-sm font-semibold text-emerald-800">🖼️ 썸네일 만들기</p>
        <button onClick={() => setOpen(false)} className="text-xs text-neutral-500">
          닫기
        </button>
      </div>

      {/* 미리보기 */}
      <canvas
        ref={canvasRef}
        width={S}
        height={S}
        className="mx-auto mb-3 block w-full max-w-[320px] rounded-lg border border-neutral-200 bg-white"
      />

      {/* 문구 후보 */}
      {candidates.length > 0 && (
        <div className="mb-1 flex flex-wrap gap-1">
          {candidates.map((c, i) => (
            <button
              key={i}
              onClick={() => setPhrase(c.join("\n"))}
              className="rounded-full border border-emerald-300 bg-white px-2 py-1 text-xs text-emerald-700"
            >
              {c.join(" / ")}
            </button>
          ))}
        </div>
      )}
      <p className="mb-1 text-xs text-neutral-400">문구(줄바꿈 = 2줄, 각 줄 10자 안팎). 직접 수정 가능.</p>
      <textarea
        value={phrase}
        onChange={(e) => setPhrase(e.target.value)}
        rows={2}
        placeholder={"예: 제주 아르떼 키즈파크\n16개월 비추"}
        className={`${fieldC} mb-3`}
      />

      {/* 중앙 이미지 선택 */}
      <p className="mb-1 text-xs font-medium text-neutral-600">가운데 이미지</p>
      {thumbUrls.length > 0 && (
        <div className="mb-2 grid grid-cols-5 gap-1">
          {thumbUrls.map((u, i) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              key={i}
              src={u}
              alt={`사진 ${i + 1}`}
              onClick={() => pickUrl(u)}
              className="aspect-square w-full cursor-pointer rounded border border-neutral-200 object-cover hover:ring-2 hover:ring-emerald-400"
            />
          ))}
        </div>
      )}
      <label className="mb-3 block">
        <span className="text-xs text-blue-600">＋ 다른 사진 올리기</span>
        <input
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) pickFile(f);
          }}
        />
      </label>

      {msg && <p className="mb-2 text-xs text-neutral-600">{msg}</p>}

      <button
        onClick={apply}
        disabled={busy}
        className="w-full rounded-lg bg-emerald-600 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
      >
        {busy ? "만드는 중…" : "이 썸네일로 확정 (대표사진)"}
      </button>
    </div>
  );
}
