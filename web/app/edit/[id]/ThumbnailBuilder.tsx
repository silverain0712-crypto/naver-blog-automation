"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// ── 비비 썸네일 템플릿 상수(2026-09-02 개편) ──
// 유저가 준 레퍼런스 스타일: 사진이 캔버스 전체를 꽉 채우고(정방형·가로형 모두 안 깨짐),
// 좌하단에 흰 배경 해시태그 알약 2개 + 흰 글자(검정 외곽선) 2줄 제목. 카톡 공유 등
// 캔버스가 잘리거나 축소돼도 사진이 커서 무너지지 않는다(예전 아치+크림 여백 템플릿의
// "카톡 공유하면 스타일이 깨진다"는 문제를 해결하려고 바꾼 것).
const S = 1080;
const MARGIN = 56; // 좌측 정렬 기준(해시태그·제목 공통)
// 2026-09-02: 유저 레퍼런스 이미지 폰트로 교체 — 공게임즈 "이사만루체" Light(= "공체 Light").
// 상업적 사용 무료(임의 수정 후 유료 판매만 금지). 눈누(noonnu.cc/font_page/463) 경유로
// 공식 배포처(gonggames.com)에서 받았다.
const FONT = "IsamanruLight";

// 2026-09-02: 레퍼런스 이미지(1080x1080, 우리 캔버스와 동일 크기)를 픽셀 단위로 실측해
// 아래 값들을 보정했다 — 텍스트 잉크 영역의 y 범위/x 범위를 감지하고, 실제 폰트 파일로
// 여러 크기를 렌더링해 잉크 높이·폭이 실측치와 가장 가까운 값을 역산했다(제목 72px).

// 해시태그 알약(좌하단, 제목 위) — 실측: 알약 밴드 y 745~795(높이 51), x 시작 50.
const PILL_Y = 745; // 알약 위쪽 y
const PILL_H = 50;
const PILL_GAP = 12; // 알약 사이 간격
const PILL_PAD_X = 24;
const PILL_FONT_SIZE = 27;
const PILL_BG = "rgba(255,255,255,0.94)";
const PILL_TEXT_C = "#1a1a1a";

// 제목 2줄 — 외곽선/그림자 없이 흰 글자만. 실측: 두 줄 다 폰트 크기 72px 에서 잉크
// 높이·폭이 레퍼런스와 가장 가깝게 일치(줄마다 크기 차이 없음 — 위계는 없다).
const TITLE_SIZE = 72;
const TITLE_LINE_GAP = 30; // 줄 사이 여백(실측: 줄 중심 간 거리 - 폰트 크기)
const TITLE_BOTTOM_Y = 980; // 마지막 줄 잉크 중심 y(실측)
const TITLE_MAX_W = 980; // 넘치면 자동으로 줄인다
const TITLE_FILL = "#ffffff";

const THUMB_PATH_SUFFIX = "/thumb.png"; // `${id}/thumb.png`

type Props = {
  draftId: string;
  images: string[]; // 이미 올라간 사진 경로들
  candidates: string[][]; // 문구 후보(각 후보 = 1~2줄)
  hashtags: string[]; // AI가 정한 해시태그(# 없이) — 알약 기본값
  // apply 시점의 최신 본문/데이터는 함수로 받아 클로저 캡처 문제를 피한다.
  getBody: () => string;
  getData: () => Record<string, unknown>;
  onApplied: (newBody: string, newImages: string[], newData: Record<string, unknown>) => void;
};

function pillPath(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number) {
  const r = h / 2;
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
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
  hashtags,
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
  // 알약 해시태그 2개 — AI가 정한 해시태그 앞 2개를 기본값으로, 직접 수정 가능.
  const [tag1, setTag1] = useState<string>(hashtags[0] ?? "");
  const [tag2, setTag2] = useState<string>(hashtags[1] ?? "");
  const [thumbUrls, setThumbUrls] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [open, setOpen] = useState(false);

  // 이사만루체 Light 로드(썸네일 만들 때만 — lazy)
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const ff = new FontFace(FONT, "url(/fonts/IsamanruLight.woff2)");
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

  // 캔버스 렌더 — 사진이 캔버스 전체를 채우고, 좌하단에 해시태그 알약 + 2줄 제목.
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, S, S);

    // 사진: 캔버스 전체를 cover-crop(정방형·가로형 어느 쪽으로 잘려도 사진이 크게 남는다)
    if (bitmap) {
      drawCover(ctx, bitmap, 0, 0, S, S);
    } else {
      ctx.fillStyle = "#E2DCD0"; // 사진 미선택 placeholder
      ctx.fillRect(0, 0, S, S);
    }

    ctx.textBaseline = "alphabetic";
    ctx.textAlign = "left";

    // 해시태그 알약(흰 배경 + 검정 글자), 좌측부터 나란히
    const tags = [tag1, tag2].map((t) => t.trim()).filter(Boolean);
    if (tags.length > 0) {
      ctx.font = `${PILL_FONT_SIZE}px "${FONT}", sans-serif`;
      let x = MARGIN;
      for (const t of tags) {
        const label = t.startsWith("#") ? t : `#${t}`;
        const w = ctx.measureText(label).width + PILL_PAD_X * 2;
        ctx.fillStyle = PILL_BG;
        pillPath(ctx, x, PILL_Y, w, PILL_H);
        ctx.fill();
        ctx.fillStyle = PILL_TEXT_C;
        const ink = drawInk(ctx, label, PILL_Y + PILL_H / 2, { left: x + PILL_PAD_X });
        ctx.fillText(label, ink.x, ink.y);
        x += w + PILL_GAP;
      }
    }

    // 제목 2줄, 좌측 정렬, 흰 글자만(외곽선·그림자 없음). 두 줄 다 같은 크기(55px).
    const lines = splitLines(phrase);
    ctx.fillStyle = TITLE_FILL;

    // 폭 넘치면 축소
    let size = TITLE_SIZE;
    ctx.font = `${size}px "${FONT}", sans-serif`;
    const widest = Math.max(0, ...lines.map((l) => ctx.measureText(l).width));
    if (widest > TITLE_MAX_W) size = Math.floor((size * TITLE_MAX_W) / widest);
    ctx.font = `${size}px "${FONT}", sans-serif`;

    // 아래→위로 쌓는다(마지막 줄을 TITLE_BOTTOM_Y 에 고정).
    const step = size + TITLE_LINE_GAP;
    const start = TITLE_BOTTOM_Y - (lines.length - 1) * step;
    lines.forEach((ln, i) => {
      const t = drawInk(ctx, ln, start + i * step, { left: MARGIN });
      ctx.fillText(ln, t.x, t.y);
    });
  }, [bitmap, phrase, tag1, tag2]);

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
      // 대표사진 자리 = 큰 제목/요약 블록 바로 아래(요약 뒤 빈 줄 다음, 도입부 앞).
      // 본문 맨 위(=그 빈 줄 앞)는 발행기가 '큰 제목/요약' 블록으로 파싱하는 구간이라
      // 거기에 마커를 두면 사진 대신 '[사진N]' 글자가 제목으로 찍힌다. 모델이 가끔
      // 키워드 한 줄 뒤에도 빈 줄을 넣어서, 본문 줄이 최소 2줄 나온 뒤의 빈 줄만
      // 경계로 인정한다(mac_generator.py _intro_boundary 와 동일한 기준).
      const lines = body.split("\n").filter((line) => line.trim() !== marker);
      let gi = -1;
      let seen = 0;
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].trim() === "") {
          if (seen >= 2) {
            gi = i;
            break;
          }
        } else {
          seen += 1;
        }
      }
      if (gi >= 0) lines.splice(gi + 1, 0, marker, "");
      else lines.unshift(marker, "");
      const newBody = lines.join("\n");
      const newImages = [...base, path];
      const newData = {
        ...data,
        thumb: { path, marker: n, phrase, tags: [tag1, tag2].map((t) => t.trim()).filter(Boolean) },
      };

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
      <p className="mb-1 text-xs text-neutral-400">
        문구(줄바꿈 = 2줄, 아래 줄이 더 크게 나와요). 직접 수정 가능.
      </p>
      <textarea
        value={phrase}
        onChange={(e) => setPhrase(e.target.value)}
        rows={2}
        placeholder={"예: 이곳에 가면 꼭 먹고 싶다!\nBEST 7"}
        className={`${fieldC} mb-3`}
      />

      {/* 해시태그 알약 2개 */}
      <p className="mb-1 text-xs font-medium text-neutral-600">해시태그 알약(최대 2개)</p>
      <div className="mb-3 flex gap-2">
        <input
          value={tag1}
          onChange={(e) => setTag1(e.target.value)}
          placeholder="도시여행"
          className={`${fieldC} text-sm`}
        />
        <input
          value={tag2}
          onChange={(e) => setTag2(e.target.value)}
          placeholder="미식여행"
          className={`${fieldC} text-sm`}
        />
      </div>

      {/* 배경 사진 선택 */}
      <p className="mb-1 text-xs font-medium text-neutral-600">배경 사진(캔버스 전체를 채워요)</p>
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
