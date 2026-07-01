"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  POST_TYPES,
  SPONSOR_TYPES,
  POST_LENGTHS,
  PHOTO_STYLES,
  OPTIONAL_FIELDS,
} from "@/lib/constants";

const field = "rounded-lg border border-neutral-300 px-3 py-2 text-base w-full bg-white";
const labelC = "text-sm font-medium text-neutral-700";

// 이미지를 디코드해 {width,height,draw} 로 반환.
// createImageBitmap(HEIC 실패 가능) → <img> 폴백(Safari 는 HEIC 도 네이티브 디코드).
type Decoded = {
  w: number;
  h: number;
  draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void;
  done: () => void;
};

async function decode(file: File): Promise<Decoded> {
  // 1) createImageBitmap (EXIF 회전 적용)
  for (const opts of [{ imageOrientation: "from-image" as const }, undefined]) {
    try {
      const bm = await createImageBitmap(file, opts as ImageBitmapOptions);
      return {
        w: bm.width,
        h: bm.height,
        draw: (ctx, w, h) => ctx.drawImage(bm, 0, 0, w, h),
        done: () => bm.close(),
      };
    } catch {
      /* 다음 방법 시도 */
    }
  }
  // 2) <img> 엘리먼트 폴백 (Safari HEIC 대응)
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((res, rej) => {
      const im = new Image();
      im.onload = () => res(im);
      im.onerror = () => rej(new Error("decode"));
      im.src = url;
    });
    return {
      w: img.naturalWidth,
      h: img.naturalHeight,
      draw: (ctx, w, h) => ctx.drawImage(img, 0, 0, w, h),
      done: () => URL.revokeObjectURL(url),
    };
  } catch {
    URL.revokeObjectURL(url);
    throw new Error(`이미지를 읽지 못했어요 (형식: ${file.type || "알수없음"})`);
  }
}

// 폰 원본(3~4MB, HEIC 포함)을 1600px JPEG 로 축소 → 업로드 가볍게 + 포맷/회전 정규화.
// (네이버도 최종 1600px JPEG 로 올리므로 화질 손실 없음)
async function resizeImage(file: File, maxEdge = 1600, quality = 0.82): Promise<Blob> {
  const src = await decode(file);
  let w = src.w;
  let h = src.h;
  if (!w || !h) throw new Error("이미지 크기를 읽지 못했어요");
  const longest = Math.max(w, h);
  if (longest > maxEdge) {
    const scale = maxEdge / longest;
    w = Math.round(w * scale);
    h = Math.round(h * scale);
  }
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas 미지원");
  src.draw(ctx, w, h);
  src.done();
  return await new Promise<Blob>((resolve, reject) =>
    canvas.toBlob(
      (bl) => (bl ? resolve(bl) : reject(new Error("이미지 변환 실패"))),
      "image/jpeg",
      quality,
    ),
  );
}

export default function NewPostPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [err, setErr] = useState("");
  const [showOptional, setShowOptional] = useState(false);
  const [photos, setPhotos] = useState<File[]>([]);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setErr("");
    const fd = new FormData(e.currentTarget);
    const str = (k: string) => (fd.get(k) as string | null)?.trim() ?? "";

    const optional: Record<string, string> = {};
    for (const { key } of OPTIONAL_FIELDS) {
      const v = (fd.get(`opt_${key}`) as string | null)?.trim();
      if (v) optional[key] = v;
    }

    if (!str("memo") && photos.length === 0) {
      setErr("메모나 사진 중 하나는 넣어주세요.");
      return;
    }

    setBusy(true);
    try {
      // 1) 메타 저장 + 서명 업로드 URL 발급
      const meta = {
        structure_key: str("structure_key"),
        keyword: str("keyword"),
        product_link: str("product_link"),
        required_links: str("required_links"),
        sponsor_type: str("sponsor_type"),
        memo: str("memo"),
        length: str("length"),
        photo_style: str("photo_style"),
        optional_fields: optional,
        photoCount: photos.length,
      };
      const res = await fetch("/api/drafts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(meta),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.error ?? "생성 요청 실패");
      }
      const { id, uploads } = (await res.json()) as {
        id: string;
        uploads: { path: string; url: string }[];
      };

      // 2) 사진을 축소해 서명 URL 로 직접 업로드 (Vercel 함수 우회).
      //    변환 실패(브라우저가 HEIC 디코드 못하는 등) 시 원본 그대로 업로드 → 맥이 처리.
      for (let i = 0; i < uploads.length; i++) {
        setProgress(`사진 업로드 ${i + 1}/${uploads.length}`);
        let body: Blob = photos[i];
        let contentType = photos[i].type || "application/octet-stream";
        try {
          body = await resizeImage(photos[i]);
          contentType = "image/jpeg";
        } catch {
          /* 원본 업로드로 폴백 */
        }
        const put = await fetch(uploads[i].url, {
          method: "PUT",
          headers: { "content-type": contentType },
          body,
        });
        if (!put.ok) throw new Error(`사진 ${i + 1} 업로드 실패 (${put.status})`);
      }

      // 3) 업로드 끝나면 생성 시작 상태로 전환
      if (uploads.length > 0) {
        await fetch(`/api/drafts/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "generating" }),
        });
      }

      router.push(`/edit/${id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
      setProgress("");
    }
  }

  return (
    <main className="mx-auto max-w-lg p-4 pb-24">
      <header className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">새 글 쓰기 ✍️</h1>
        <Link href="/list" className="text-sm text-blue-600">
          내 글 →
        </Link>
      </header>

      <form onSubmit={submit} className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1">
            <span className={labelC}>글 유형</span>
            <select name="structure_key" className={field} defaultValue="free">
              {POST_TYPES.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className={labelC}>협찬</span>
            <select name="sponsor_type" className={field} defaultValue="내돈내산">
              {SPONSOR_TYPES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className={labelC}>글 길이</span>
            <select name="length" className={field} defaultValue="1500">
              {POST_LENGTHS.map((n) => (
                <option key={n} value={n}>
                  {n}자
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className={labelC}>사진 노출</span>
            <select name="photo_style" className={field} defaultValue="감성 중심">
              {PHOTO_STYLES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
        </div>

        <label className="flex flex-col gap-1">
          <span className={labelC}>핵심 키워드 (비우면 AI가 제안)</span>
          <input name="keyword" className={field} placeholder="예: 신당동 키즈카페" />
        </label>

        <label className="flex flex-col gap-1">
          <span className={labelC}>메모 — 최우선 반영</span>
          <textarea
            name="memo"
            className={`${field} min-h-28`}
            placeholder="오늘 있었던 일, 느낌, 꼭 담고 싶은 내용을 편하게 적어주세요."
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className={labelC}>상품/필수 링크 (선택)</span>
          <input name="product_link" className={field} placeholder="상품 링크" />
          <textarea
            name="required_links"
            className={`${field} min-h-16 mt-1`}
            placeholder="본문에 꼭 넣을 링크 (여러 개는 줄바꿈)"
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className={labelC}>사진 {photos.length > 0 && `(${photos.length}장)`}</span>
          <input
            name="photos"
            type="file"
            accept="image/*"
            multiple
            className="text-sm"
            onChange={(e) => setPhotos(e.target.files ? Array.from(e.target.files) : [])}
          />
        </label>

        <button
          type="button"
          onClick={() => setShowOptional((v) => !v)}
          className="text-left text-sm text-blue-600"
        >
          {showOptional ? "▾ 선택 입력 접기" : "▸ 선택 입력 (장소·제품·가격 등)"}
        </button>
        {showOptional && (
          <div className="grid grid-cols-2 gap-3 rounded-lg bg-neutral-50 p-3">
            {OPTIONAL_FIELDS.map((f) => (
              <label key={f.key} className="flex flex-col gap-1">
                <span className="text-xs text-neutral-500">{f.label}</span>
                <input name={`opt_${f.key}`} className={`${field} text-sm`} />
              </label>
            ))}
          </div>
        )}

        {err && <p className="text-sm text-red-600">{err}</p>}

        <button
          type="submit"
          disabled={busy}
          className="fixed inset-x-0 bottom-0 mx-auto max-w-lg bg-neutral-900 px-4 py-4 text-base font-semibold text-white disabled:opacity-50 sm:static sm:rounded-lg"
        >
          {busy ? progress || "요청 중…" : "초안 생성 🚀"}
        </button>
      </form>
    </main>
  );
}
