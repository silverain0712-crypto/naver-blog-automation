"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  POST_TYPES,
  POST_LENGTHS,
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
// GIF 는 애니메이션 유지 때문에 원본 그대로 올라간다(축소 불가).
// 움짤 몇 개로 Supabase 무료 한도(1GB)를 태울 수 있어 장당 상한을 둔다.
const GIF_MAX_BYTES = 10 * 1024 * 1024;

// 동영상 첨부(사진 저장소와 별개) — 글 작성 시 미리 잘라 GIF 로 만들면 블로그 사진처럼
// 쓰이고, 원본 영상은 나중에 클립(숏폼) 단계에서 그대로 활용된다.
const VIDEO_MAX_BYTES = 100 * 1024 * 1024;
const VIDEO_MAX_COUNT = 5;

// 로컬 파일에서 영상 길이(초)를 읽는다(업로드 없이 <video> 메타데이터만).
function getVideoDuration(file: File): Promise<number> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const v = document.createElement("video");
    v.preload = "metadata";
    v.onloadedmetadata = () => {
      URL.revokeObjectURL(url);
      resolve(Number.isFinite(v.duration) ? v.duration : 0);
    };
    v.onerror = () => {
      URL.revokeObjectURL(url);
      resolve(0);
    };
    v.src = url;
  });
}

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
type Vid = { file: File; url: string; duration: number; start: number; end: number; useGif: boolean };

export default function NewPostPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [err, setErr] = useState("");
  const [showOptional, setShowOptional] = useState(false);
  const [structureKey, setStructureKey] = useState("restaurant");
  const [showPhotoPicker, setShowPhotoPicker] = useState(false);
  const [pics, setPics] = useState<Pic[]>([]);
  const [vids, setVids] = useState<Vid[]>([]);
  const [guideFiles, setGuideFiles] = useState<File[]>([]);
  const [guideText, setGuideText] = useState("");
  const [dragPhotos, setDragPhotos] = useState(false);
  const [dragGuide, setDragGuide] = useState(false);
  const [learning, setLearning] = useState(false);
  const [learnMsg, setLearnMsg] = useState("");
  const [pendingIdeaId, setPendingIdeaId] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const memoRef = useRef<HTMLTextAreaElement>(null);

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

  // 글감 메모(/ideas)에서 "이걸로 글쓰기"로 넘어온 경우 메모칸을 채운다.
  // 실제로 초안이 만들어지면(submit 성공) 그 메모를 지운다.
  useEffect(() => {
    try {
      const raw = localStorage.getItem("pendingIdea");
      if (raw) {
        const parsed = JSON.parse(raw) as { id?: string; text?: string; memo?: string };
        // 글감 제목 + (있으면) 상세 메모를 합쳐서 채운다.
        const filled = [parsed?.text, parsed?.memo].filter((s) => s && s.trim()).join("\n\n");
        if (filled && memoRef.current) memoRef.current.value = filled;
        if (parsed?.id) setPendingIdeaId(parsed.id);
        localStorage.removeItem("pendingIdea");
      }
    } catch {
      /* 무시 */
    }
  }, []);

  // 사진 추가(입력/드롭 공용) — 이미지 파일만 받는다.
  function addFiles(list: FileList | null) {
    if (!list || list.length === 0) return;
    const imgs = Array.from(list).filter((f) => f.type.startsWith("image/"));
    if (imgs.length === 0) return;
    const tooBig = imgs.filter((f) => f.type === "image/gif" && f.size > GIF_MAX_BYTES);
    const ok = imgs.filter((f) => !tooBig.includes(f));
    if (tooBig.length > 0) {
      const mb = (GIF_MAX_BYTES / 1024 / 1024).toFixed(0);
      setErr(
        `움짤 ${tooBig.length}개가 ${mb}MB를 넘어 제외했습니다` +
          `(${tooBig.map((f) => `${f.name} ${(f.size / 1024 / 1024).toFixed(1)}MB`).join(", ")}).` +
          ` 길이나 화질을 줄여서 다시 넣어주세요.`,
      );
    }
    if (ok.length === 0) return;
    const next = ok.map((f) => ({ file: f, url: URL.createObjectURL(f) }));
    setPics((prev) => [...prev, ...next]);
  }
  function removePic(i: number) {
    setPics((prev) => {
      URL.revokeObjectURL(prev[i].url);
      return prev.filter((_, idx) => idx !== i);
    });
  }

  // 동영상 추가 — 최대 5개 · 개당 100MB. 각각 길이를 읽어 기본 트림 구간(0~8초 또는
  // 전체 길이)을 잡아준다. 실제 업로드는 제출 시점에 한다(여기선 로컬 미리보기만).
  async function addVideoFiles(list: FileList | null) {
    if (!list || list.length === 0) return;
    const files = Array.from(list).filter((f) => f.type.startsWith("video/"));
    if (files.length === 0) return;
    const room = VIDEO_MAX_COUNT - vids.length;
    if (room <= 0) {
      setErr(`동영상은 최대 ${VIDEO_MAX_COUNT}개까지 첨부할 수 있어요.`);
      return;
    }
    const tooBig = files.filter((f) => f.size > VIDEO_MAX_BYTES);
    const ok = files.filter((f) => !tooBig.includes(f)).slice(0, room);
    if (tooBig.length > 0) {
      const mb = (VIDEO_MAX_BYTES / 1024 / 1024).toFixed(0);
      setErr(`동영상 ${tooBig.length}개가 ${mb}MB를 넘어 제외했습니다.`);
    }
    const next: Vid[] = [];
    for (const f of ok) {
      const duration = await getVideoDuration(f);
      next.push({
        file: f,
        url: URL.createObjectURL(f),
        duration,
        start: 0,
        end: duration ? Math.min(duration, 8) : 8,
        useGif: true,
      });
    }
    setVids((prev) => [...prev, ...next]);
  }
  function removeVideo(i: number) {
    setVids((prev) => {
      URL.revokeObjectURL(prev[i].url);
      return prev.filter((_, idx) => idx !== i);
    });
  }
  function updateVideo(i: number, patch: Partial<Vid>) {
    setVids((prev) => prev.map((v, idx) => (idx === i ? { ...v, ...patch } : v)));
  }
  const videoRefs = useRef<(HTMLVideoElement | null)[]>([]);
  const trackRefs = useRef<(HTMLDivElement | null)[]>([]);
  // 핸들을 드래그하는 동안 그 지점으로 계속 옮겨서 미리보기가 바로 따라오게 한다
  // (영상은 autoPlay+muted로 이미 재생 중이라 별도로 play() 호출은 필요 없다).
  function seekVideo(i: number, t: number) {
    const el = videoRefs.current[i];
    if (el) el.currentTime = t;
  }
  // 재생 영역(트랙) 안에서 포인터의 x좌표를 그 영상의 초 단위 시각으로 변환한다.
  function clientXToTime(i: number, clientX: number, dur: number): number {
    const el = trackRefs.current[i];
    if (!el) return 0;
    const rect = el.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    return ratio * dur;
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

  // 폼 값 + 첨부를 메타로 모은다(초안 생성 / 사진만 보관 공용).
  function buildMeta() {
    const form = formRef.current;
    const fd = new FormData(form ?? undefined);
    const str = (k: string) => (fd.get(k) as string | null)?.trim() ?? "";
    const optional: Record<string, string> = {};
    for (const { key } of OPTIONAL_FIELDS) {
      const v = (fd.get(`opt_${key}`) as string | null)?.trim();
      if (v) optional[key] = v;
    }
    return {
      meta: {
        structure_key: str("structure_key"),
        keyword: str("keyword"),
        product_link: str("product_link"),
        required_links: str("required_links"),
        memo: str("memo"),
        length: str("length"),
        optional_fields: optional,
        photoCount: pics.length,
        photoMimes: pics.map((p) => p.file.type),
        videoCount: vids.length,
        videoNames: vids.map((v) => v.file.name),
        guidelineNames: guideFiles.map((f) => f.name),
        guidelineText: guideText.trim(),
        generateShots: Boolean(fd.get("generate_shots")) && Boolean(str("product_link")),
        isSponsored: Boolean(fd.get("is_sponsored")),
      },
      memo: str("memo"),
    };
  }

  // 서명 URL 발급 → 사진/가이드 파일 직접 업로드(초안 생성 / 사진 보관 공용). 초안 id 반환.
  async function uploadAssets(meta: Record<string, unknown>): Promise<string> {
    const res = await fetch("/api/drafts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(meta),
    });
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.error ?? "요청 실패");
    }
    const { id, uploads, guidelineUploads, videoUploads } = (await res.json()) as {
      id: string;
      uploads: { path: string; url: string }[];
      guidelineUploads: { path: string; url: string }[];
      videoUploads: { path: string; url: string }[];
    };

    // 사진을 서명 URL 로 직접 업로드(Vercel 함수 우회).
    // GIF: 애니메이션 유지 위해 원본 그대로. 그 외: 1600px JPEG 로 축소(실패 시 원본 폴백).
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

    // 협찬 가이드/제품 설명서 파일 업로드(원본 그대로 — 여러 개)
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

    // 동영상 업로드(원본 그대로) → 완료되면 트림 구간 포함 메타를 확정 저장.
    if (videoUploads.length > 0) {
      for (let i = 0; i < videoUploads.length; i++) {
        setProgress(`동영상 업로드 ${i + 1}/${videoUploads.length}`);
        const v = vids[i];
        const put = await fetch(videoUploads[i].url, {
          method: "PUT",
          headers: { "content-type": v.file.type || "video/mp4" },
          body: v.file,
        });
        if (!put.ok) throw new Error(`동영상 ${i + 1} 업로드 실패 (${put.status})`);
      }
      const videoMeta = videoUploads.map((u, i) => ({
        path: u.path,
        name: vids[i].file.name,
        size: vids[i].file.size,
        start: vids[i].start,
        end: vids[i].end,
        gif_photo_number: null as number | null,
      }));
      await fetch(`/api/drafts/${id}/videos`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ videos: videoMeta }),
      });
    }
    return id;
  }

  // 체크된 동영상마다 순서대로 GIF 변환을 요청하고 끝날 때까지 기다린다(3초 폴링).
  // 실패해도 그 영상만 건너뛰고 계속 진행 — 한 개 실패로 글 작성 전체를 막지 않는다.
  async function extractGifs(id: string) {
    const targets = vids.map((v, i) => ({ v, i })).filter(({ v }) => v.useGif);
    const failed: string[] = [];
    for (let k = 0; k < targets.length; k++) {
      const { v, i } = targets[k];
      setProgress(`GIF 변환 중 (${k + 1}/${targets.length})…`);
      const res = await fetch(`/api/drafts/${id}/gif`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_index: i }),
      });
      if (!res.ok) {
        failed.push(`${v.file.name}: 요청 실패`);
        continue; // 이 영상은 건너뛰고 다음으로 — 한 개 실패로 글 작성 전체를 막지 않는다.
      }
      for (let t = 0; t < 40; t++) {
        await new Promise((r) => setTimeout(r, 3000));
        const r = await fetch(`/api/drafts/${id}/gif`, { cache: "no-store" });
        if (!r.ok) continue;
        const { job } = (await r.json()) as { job?: { status?: string; error?: string } | null };
        if (job?.status === "done") break;
        if (job?.status === "error") {
          failed.push(`${v.file.name}: ${job.error || "변환 실패"}`);
          break;
        }
      }
    }
    if (failed.length > 0) {
      setErr(`일부 동영상은 GIF로 못 만들었어요 — ${failed.join(" / ")}`);
    }
  }

  const noPhotoNeeded = structureKey === "info" || structureKey === "hompiid";

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setErr("");
    const { meta, memo } = buildMeta();
    if (!memo && pics.length === 0 && vids.length === 0) {
      setErr("메모나 사진, 동영상 중 하나는 넣어주세요.");
      return;
    }
    setBusy(true);
    try {
      const id = await uploadAssets(meta);
      // GIF 로 만들 동영상이 있으면 초안 생성(status=generating) 을 켜기 전에 먼저
      // 끝낸다 — image_analyzer.py 가 이미지를 넘겨받는 시점에 그 GIF가 images[] 에
      // 들어가 있어야 다른 사진과 동일하게 분석·배치된다(블로그 생성 코드는 무수정).
      if (vids.some((v) => v.useGif)) {
        await extractGifs(id);
      }
      // 업로드가 있었으면 생성 시작 상태로 전환(사진/가이드/영상 없으면 POST 가 이미 generating).
      if (meta.photoCount || guideFiles.length > 0 || vids.length > 0) {
        setProgress("초안 생성 준비 중…");
        await fetch(`/api/drafts/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "generating" }),
        });
      }
      // 글감 메모에서 시작한 글이면, 실제로 써지기 시작했으니 메모는 지운다.
      if (pendingIdeaId) {
        fetch(`/api/ideas/${pendingIdeaId}`, { method: "DELETE" }).catch(() => {});
      }
      router.push(`/edit/${id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
      setProgress("");
    }
  }

  // 사진만 올려 보관 — 지금 글은 안 쓰고, 나중에 이 사진들로 초안을 생성한다.
  async function saveStash() {
    setErr("");
    if (pics.length === 0) {
      setErr("보관할 사진을 넣어주세요.");
      return;
    }
    setBusy(true);
    try {
      const { meta } = buildMeta();
      const id = await uploadAssets(meta);
      // 생성기·워커가 건드리지 않는 정지 상태로 보관.
      await fetch(`/api/drafts/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "photo_stash" }),
      });
      router.push("/list");
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
          <Link href="/ideas" className="text-blue-600">
            💡 글감 메모
          </Link>
          <Link href="/list" className="text-blue-600">
            내 글 →
          </Link>
        </div>
      </header>

      {learnMsg && (
        <p className="mb-3 rounded-lg bg-blue-50 px-3 py-2 text-sm text-blue-700">{learnMsg}</p>
      )}

      <form ref={formRef} onSubmit={submit} className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1">
            <span className={labelC}>글 유형</span>
            <select
              name="structure_key"
              className={field}
              value={structureKey}
              onChange={(e) => setStructureKey(e.target.value)}
            >
              {POST_TYPES.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
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
        </div>

        <label className="flex flex-col gap-1">
          <span className={labelC}>핵심 키워드 (비우면 AI가 제안)</span>
          <input name="keyword" className={field} placeholder="예: 신당동 키즈카페" />
        </label>

        <label className="flex flex-col gap-1">
          <span className={labelC}>메모 — 최우선 반영</span>
          <textarea
            ref={memoRef}
            name="memo"
            className={`${field} min-h-28`}
            placeholder="오늘 있었던 일, 느낌, 꼭 담고 싶은 내용을 편하게 적어주세요."
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className={labelC}>상품/필수 링크 (선택)</span>
          <input name="product_link" className={field} placeholder="상품 링크" />
          <label className="mt-1 flex items-start gap-2 text-sm text-neutral-600">
            <input type="checkbox" name="generate_shots" className="mt-1" />
            <span>
              이 링크로 <strong className="font-medium">상품 상세컷 3장</strong>도 만들기
              <span className="block text-xs text-neutral-500">
                실제 상품 사진을 가져와 그 제품 그대로 만듭니다. 직접 찍으셨으면 체크 안 해도 됩니다.
              </span>
            </span>
          </label>
          <textarea
            name="required_links"
            className={`${field} min-h-16 mt-1`}
            placeholder="본문에 꼭 넣을 링크 (여러 개는 줄바꿈)"
          />
        </label>

        <div className="flex flex-col gap-2">
          <span className={labelC}>사진 {pics.length > 0 && `· ${pics.length}장`}</span>

          {noPhotoNeeded && pics.length === 0 && !showPhotoPicker ? (
            <div className="rounded-xl border border-dashed border-emerald-300 bg-emerald-50 px-3 py-3 text-sm text-emerald-800">
              ✨ 이 글 유형은 사진 없이 제출해도 발행용 카드뉴스 이미지를 자동으로 만들어드려요.
              <button
                type="button"
                onClick={() => setShowPhotoPicker(true)}
                className="ml-1 font-medium text-emerald-700 underline underline-offset-2"
              >
                그래도 사진을 넣고 싶다면
              </button>
            </div>
          ) : (
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
              <span className="text-xs text-neutral-400">
                {noPhotoNeeded
                  ? "안 넣어도 자동으로 이미지가 만들어져요 · 여러 장 · GIF 가능"
                  : "여러 장 · GIF 도 가능해요"}
              </span>
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
          )}
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

        {pics.length > 0 && (
          <button
            type="button"
            onClick={saveStash}
            disabled={busy}
            className="rounded-lg border border-dashed border-neutral-400 bg-neutral-50 py-2.5 text-sm font-medium text-neutral-600 active:bg-neutral-100 disabled:opacity-50"
          >
            📥 사진만 저장 · 나중에 이 사진들로 글쓰기
          </button>
        )}

        <div className="flex flex-col gap-2">
          <span className={labelC}>
            동영상 (선택){vids.length > 0 && ` · ${vids.length}개`}
          </span>
          <span className="-mt-1 text-xs text-neutral-400">
            올린 구간을 잘라 GIF로 만들어 블로그 사진처럼 쓰고, 원본 영상은 나중에
            클립(숏폼) 만들 때 그대로 활용해요. 최대 {VIDEO_MAX_COUNT}개 · 개당{" "}
            {(VIDEO_MAX_BYTES / 1024 / 1024).toFixed(0)}MB.
          </span>
          {vids.length < VIDEO_MAX_COUNT && (
            <label className="flex cursor-pointer items-center gap-2 rounded-xl border-2 border-dashed border-neutral-300 bg-neutral-50 px-3 py-4 text-neutral-500 active:bg-neutral-100">
              <span className="text-xl">🎬</span>
              <span className="flex flex-col">
                <span className="text-sm font-medium text-neutral-600">동영상 추가</span>
                <span className="text-xs text-neutral-400">탭해서 선택</span>
              </span>
              <input
                type="file"
                accept="video/*"
                multiple
                className="hidden"
                onChange={(e) => {
                  addVideoFiles(e.target.files);
                  e.currentTarget.value = "";
                }}
              />
            </label>
          )}
          {vids.map((v, i) => {
            const dur = v.duration > 0 ? v.duration : Math.max(v.end, 30);
            const startPct = (v.start / dur) * 100;
            const endPct = (v.end / dur) * 100;
            return (
              <div key={i} className="flex flex-col gap-2 rounded-xl border border-neutral-300 bg-white p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm text-neutral-700">{v.file.name}</span>
                  <button
                    type="button"
                    onClick={() => removeVideo(i)}
                    aria-label="동영상 삭제"
                    className="shrink-0 rounded-full bg-neutral-900 px-2 py-1 text-xs leading-none text-white"
                  >
                    ×
                  </button>
                </div>

                {/* 재생 영역은 그대로 두고(가리지 않음), 구간 조정 핸들은 그 아래
                    별도 바에만 둔다. 네이티브 <video controls>는 iOS에서 재생 시
                    전체화면으로 튀어나가서 제거하고, autoPlay+muted+playsInline로
                    구간만 계속 반복 재생시킨다. */}
                <div className="relative aspect-video w-full overflow-hidden rounded-lg bg-black">
                  <video
                    ref={(el) => {
                      videoRefs.current[i] = el;
                    }}
                    src={v.url}
                    autoPlay
                    muted
                    playsInline
                    className="h-full w-full object-contain"
                    onTimeUpdate={(e) => {
                      // 지정한 구간만 반복 재생 — 끝에 닿으면 시작으로 되돌린다.
                      const el = e.currentTarget;
                      if (el.currentTime >= v.end || el.currentTime < v.start) {
                        el.currentTime = v.start;
                      }
                    }}
                  />
                </div>

                <div
                  ref={(el) => {
                    trackRefs.current[i] = el;
                  }}
                  className="relative h-8 w-full touch-none select-none rounded-lg bg-neutral-200"
                >
                  <div
                    className="pointer-events-none absolute inset-y-0 rounded-lg bg-neutral-900/80"
                    style={{ left: `${startPct}%`, width: `${Math.max(0, endPct - startPct)}%` }}
                  />
                  <div
                    onPointerDown={(e) => e.currentTarget.setPointerCapture(e.pointerId)}
                    onPointerMove={(e) => {
                      if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
                      const t = clientXToTime(i, e.clientX, dur);
                      const s = Math.min(t, v.end - 0.2);
                      updateVideo(i, { start: Math.max(0, s) });
                      seekVideo(i, Math.max(0, s));
                    }}
                    className="absolute inset-y-0 flex w-7 touch-none cursor-ew-resize items-center justify-center"
                    style={{ left: `calc(${startPct}% - 14px)` }}
                  >
                    <div className="h-full w-1.5 rounded-full bg-white shadow" />
                  </div>
                  <div
                    onPointerDown={(e) => e.currentTarget.setPointerCapture(e.pointerId)}
                    onPointerMove={(e) => {
                      if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
                      const t = clientXToTime(i, e.clientX, dur);
                      const en = Math.max(t, v.start + 0.2);
                      updateVideo(i, { end: Math.min(dur, en) });
                      seekVideo(i, Math.min(dur, en));
                    }}
                    className="absolute inset-y-0 flex w-7 touch-none cursor-ew-resize items-center justify-center"
                    style={{ left: `calc(${endPct}% - 14px)` }}
                  >
                    <div className="h-full w-1.5 rounded-full bg-white shadow" />
                  </div>
                </div>

                <span className="text-xs text-neutral-500">
                  구간 {v.start.toFixed(1)}초 ~ {v.end.toFixed(1)}초 (
                  {(v.end - v.start).toFixed(1)}초)
                  {v.duration ? ` · 전체 ${v.duration.toFixed(1)}초` : ""}
                </span>

                <label className="flex items-center gap-1.5 text-xs text-neutral-600">
                  <input
                    type="checkbox"
                    checked={v.useGif}
                    onChange={(e) => updateVideo(i, { useGif: e.target.checked })}
                  />
                  GIF로 만들어 본문에 쓰기(끄면 클립에도 못 써요)
                </label>
              </div>
            );
          })}
        </div>

        <label className="flex items-start gap-2 text-sm text-neutral-600">
          <input type="checkbox" name="is_sponsored" className="mt-1" />
          <span>
            <strong className="font-medium">협찬/제공받은 글이에요</strong>
            <span className="block text-xs text-neutral-500">
              체크하면 본문에 아쉬운 점 없이 좋았던 점만 정리해서 써요. 가이드라인 첨부 여부와
              무관하게 이 체크박스로만 판단해요.
            </span>
          </span>
        </label>

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
