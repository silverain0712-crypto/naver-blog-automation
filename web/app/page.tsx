"use client";

import { useEffect, useState } from "react";
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

type Pic = { file: File; url: string };

export default function NewPostPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [err, setErr] = useState("");
  const [showOptional, setShowOptional] = useState(false);
  const [pics, setPics] = useState<Pic[]>([]);
  const [guideFiles, setGuideFiles] = useState<File[]>([]);
  const [guideText, setGuideText] = useState("");
  const [dragPhotos, setDragPhotos] = useState(false);
  const [dragGuide, setDragGuide] = useState(false);
  const [learning, setLearning] = useState(false);
  const [learnMsg, setLearnMsg] = useState("");

  // PC 에서 드롭존 밖에 파일을 떨궜을 때 브라우저가 파일을 열어버리는 것 방지.
  useEffect(() => {
    const prevent = (e: DragEvent) => e.preventDefault();
    window.addEventListener("dragover", prevent);
    window.addEventListener("drop", prevent);
    return () => {
      window.removeEventListener("dragover", prevent);
      window.removeEventListener("drop", prevent);
    };
  }, []);

  // 사진 추가(입력/드롭 공용) — 이미지 파일만 받는다.
  function addFiles(list: FileList | null) {
    if (!list || list.length === 0) return;
    const imgs = Array.from(list).filter((f) => f.type.startsWith("image/"));
    if (imgs.length === 0) return;
    const next = imgs.map((f) => ({ file: f, url: URL.createObjectURL(f) }));
    setPics((prev) => [...prev, ...next]);
  }
  function removePic(i: number) {
    setPics((prev) => {
      URL.revokeObjectURL(prev[i].url);
      return prev.filter((_, idx) => idx !== i);
    });
  }

  // 가이드/설명서 파일 추가(입력/드롭 공용) — 여러 개 누적. 이미지 필터는 두지 않는다(엑셀·PDF 등).
  function addGuideFiles(list: FileList | null) {
    if (!list || list.length === 0) return;
    setGuideFiles((prev) => [...prev, ...Array.from(list)]);
  }
  function removeGuideFile(i: number) {
    setGuideFiles((prev) => prev.filter((_, idx) => idx !== i));
  }

  // '발행 글 학습' — 맥에 신호를 보내고 style_sync 결과가 올 때까지 폴링.
  async function learn() {
    setLearning(true);
    setLearnMsg("맥에 학습 요청 중…");
    try {
      const res = await fetch("/api/learn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error("학습 요청 실패");
      const { id } = await res.json();
      setLearnMsg("맥에서 발행 글을 수집하는 중… (맥 생성기가 켜져 있어야 해요)");
      for (let t = 0; t < 30; t++) {
        await new Promise((r) => setTimeout(r, 2000));
        const r = await fetch(`/api/drafts/${id}`, { cache: "no-store" });
        if (!r.ok) continue;
        const { draft } = await r.json();
        if (draft?.status === "learn_done") {
          const result = draft.data?.result ?? {};
          const added = result.added?.length ?? 0;
          if (result.error) setLearnMsg(`학습 실패: ${result.error}`);
          else if (added > 0) setLearnMsg(`📚 발행 글 ${added}편을 새로 학습했어요!`);
          else setLearnMsg("이미 최신 상태예요 ✓ (새 발행 글 없음)");
          setLearning(false);
          return;
        }
      }
      setLearnMsg("맥 응답이 없어요 — 맥에서 생성기(start_generator.sh)가 켜져 있는지 확인해주세요.");
    } catch (e) {
      setLearnMsg(e instanceof Error ? e.message : String(e));
    }
    setLearning(false);
  }

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

    if (!str("memo") && pics.length === 0) {
      setErr("메모나 사진 중 하나는 넣어주세요.");
      return;
    }

    setBusy(true);
    try {
      // 1) 메타 저장 + 서명 업로드 URL 발급 (사진 MIME 도 함께 보내 GIF 는 .gif 로 저장)
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
        photoCount: pics.length,
        photoMimes: pics.map((p) => p.file.type),
        guidelineNames: guideFiles.map((f) => f.name),
        guidelineText: guideText.trim(),
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
      const { id, uploads, guidelineUploads } = (await res.json()) as {
        id: string;
        uploads: { path: string; url: string }[];
        guidelineUploads: { path: string; url: string }[];
      };

      // 2) 사진을 서명 URL 로 직접 업로드 (Vercel 함수 우회).
      //    GIF: 애니메이션 유지 위해 원본 그대로. 그 외: 1600px JPEG 로 축소
      //    (변환 실패 시 원본 폴백 → 맥이 처리).
      for (let i = 0; i < uploads.length; i++) {
        setProgress(`사진 업로드 ${i + 1}/${uploads.length}`);
        const file = pics[i].file;
        let body: Blob = file;
        let contentType = file.type || "application/octet-stream";
        if (file.type === "image/gif") {
          contentType = "image/gif";
        } else {
          try {
            body = await resizeImage(file);
            contentType = "image/jpeg";
          } catch {
            /* 원본 업로드로 폴백 */
          }
        }
        const put = await fetch(uploads[i].url, {
          method: "PUT",
          headers: { "content-type": contentType },
          body,
        });
        if (!put.ok) throw new Error(`사진 ${i + 1} 업로드 실패 (${put.status})`);
      }

      // 2-b) 협찬 가이드/제품 설명서 파일 업로드(원본 그대로 — 여러 개)
      for (let i = 0; i < guidelineUploads.length; i++) {
        setProgress(`가이드/설명서 업로드 ${i + 1}/${guidelineUploads.length}`);
        const gf = guideFiles[i];
        const put = await fetch(guidelineUploads[i].url, {
          method: "PUT",
          headers: { "content-type": gf.type || "application/octet-stream" },
          body: gf,
        });
        if (!put.ok) throw new Error(`가이드/설명서 ${i + 1} 업로드 실패 (${put.status})`);
      }

      // 3) 업로드 끝나면 생성 시작 상태로 전환
      if (uploads.length > 0 || guidelineUploads.length > 0) {
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
      <header className="mb-3 flex items-center justify-between">
        <h1 className="text-lg font-semibold">새 글 쓰기 ✍️</h1>
        <div className="flex items-center gap-3 text-sm">
          <button
            type="button"
            onClick={learn}
            disabled={learning}
            className="text-blue-600 disabled:opacity-50"
          >
            {learning ? "학습 중…" : "📚 발행글 학습"}
          </button>
          <Link href="/list" className="text-blue-600">
            내 글 →
          </Link>
        </div>
      </header>

      {learnMsg && (
        <p className="mb-3 rounded-lg bg-blue-50 px-3 py-2 text-sm text-blue-700">{learnMsg}</p>
      )}

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

        <div className="flex flex-col gap-2">
          <span className={labelC}>사진 {pics.length > 0 && `· ${pics.length}장`}</span>
          <label
            onDragOver={(e) => {
              e.preventDefault();
              setDragPhotos(true);
            }}
            onDragLeave={() => setDragPhotos(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragPhotos(false);
              addFiles(e.dataTransfer.files);
            }}
            className={`flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed py-6 transition-colors ${
              dragPhotos
                ? "border-blue-400 bg-blue-50 text-blue-500"
                : "border-neutral-300 bg-neutral-50 text-neutral-500 active:bg-neutral-100"
            }`}
          >
            <span className="text-3xl">📷</span>
            <span className="text-sm font-medium text-neutral-600">탭하거나 끌어다 놓기</span>
            <span className="text-xs text-neutral-400">여러 장 · GIF 도 가능해요</span>
            <input
              type="file"
              accept="image/*,image/gif"
              multiple
              className="hidden"
              onChange={(e) => {
                addFiles(e.target.files);
                e.currentTarget.value = "";
              }}
            />
          </label>
          {pics.length > 0 && (
            <div className="grid grid-cols-4 gap-2">
              {pics.map((p, i) => (
                <div key={i} className="relative aspect-square">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={p.url}
                    alt={`사진 ${i + 1}`}
                    className="h-full w-full rounded-lg border border-neutral-200 object-cover"
                  />
                  <button
                    type="button"
                    onClick={() => removePic(i)}
                    aria-label="사진 삭제"
                    className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-neutral-900 text-xs leading-none text-white shadow"
                  >
                    ×
                  </button>
                  {p.file.type === "image/gif" && (
                    <span className="absolute bottom-1 left-1 rounded bg-black/60 px-1 text-[10px] font-medium text-white">
                      GIF
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-2">
          <span className={labelC}>
            협찬 가이드 · 제품 설명서 (선택){guideFiles.length > 0 && ` · ${guideFiles.length}개`}
          </span>
          <span className="-mt-1 text-xs text-neutral-400">
            협찬 가이드라인뿐 아니라 제품 설명서·스펙 자료도 인식해요. 파일 여러 개 + 텍스트 함께 가능.
          </span>
          <label
            onDragOver={(e) => {
              e.preventDefault();
              setDragGuide(true);
            }}
            onDragLeave={() => setDragGuide(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragGuide(false);
              addGuideFiles(e.dataTransfer.files);
            }}
            className={`flex cursor-pointer items-center gap-2 rounded-xl border-2 border-dashed px-3 py-4 transition-colors ${
              dragGuide
                ? "border-blue-400 bg-blue-50 text-blue-500"
                : "border-neutral-300 bg-neutral-50 text-neutral-500 active:bg-neutral-100"
            }`}
          >
            <span className="text-xl">📋</span>
            <span className="flex flex-col">
              <span className="text-sm font-medium text-neutral-600">파일 첨부 (여러 개 가능)</span>
              <span className="text-xs text-neutral-400">
                엑셀·PDF·워드·이미지 — 탭하거나 끌어다 놓기
              </span>
            </span>
            <input
              type="file"
              accept=".xlsx,.xls,.xlsm,.pdf,.docx,.csv,.txt,image/*"
              multiple
              className="hidden"
              onChange={(e) => {
                addGuideFiles(e.target.files);
                e.currentTarget.value = "";
              }}
            />
          </label>
          {guideFiles.length > 0 && (
            <div className="flex flex-col gap-1.5">
              {guideFiles.map((f, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between rounded-xl border border-neutral-300 bg-white px-3 py-2.5"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="text-lg">📋</span>
                    <span className="truncate text-sm text-neutral-700">{f.name}</span>
                  </span>
                  <button
                    type="button"
                    onClick={() => removeGuideFile(i)}
                    className="ml-2 shrink-0 rounded-lg border border-neutral-300 px-2 py-1 text-xs text-neutral-600"
                  >
                    제거
                  </button>
                </div>
              ))}
            </div>
          )}
          <textarea
            value={guideText}
            onChange={(e) => setGuideText(e.target.value)}
            className={`${field} min-h-20`}
            placeholder="가이드라인·제품 설명서 내용을 직접 붙여넣어도 돼요 (파일 없이 텍스트만도 가능)"
          />
        </div>

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
