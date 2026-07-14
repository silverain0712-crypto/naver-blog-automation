"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  STATUS_LABEL,
  POST_TYPES,
  SPONSOR_TYPES,
  POST_LENGTHS,
  PHOTO_STYLES,
} from "@/lib/constants";

type Draft = {
  id: string;
  status: string;
  title: string | null;
  body: string | null;
  data: Record<string, any> | null;
  images: string[] | null;
};

export default function EditPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [saving, setSaving] = useState(false);
  const [loadErr, setLoadErr] = useState("");
  const [stashUrls, setStashUrls] = useState<string[]>([]); // 보관 사진 미리보기 URL
  const [revisionReq, setRevisionReq] = useState(""); // 초안 확인 후 수정 요청
  const editedRef = useRef(false); // 사용자가 편집을 시작하면 폴링이 덮어쓰지 않게
  const stashFormRef = useRef<HTMLFormElement>(null);

  const load = useCallback(async () => {
    const res = await fetch(`/api/drafts/${id}`, { cache: "no-store" });
    if (!res.ok) {
      setLoadErr("불러오기 실패");
      return null;
    }
    const { draft } = await res.json();
    setDraft(draft);
    if (!editedRef.current) {
      setTitle(draft.title ?? "");
      setBody(draft.body ?? "");
    }
    return draft as Draft;
  }, [id]);

  // 생성 중이면 2초마다 폴링, draft_ready 되면 멈춤.
  // draft?.status 도 의존성에 둬서 photo_stash → generating 전환 시 폴링이 다시 시작되게 한다.
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      const d = await load();
      if (stop) return;
      if (d && (d.status === "generating" || d.status === "uploading")) {
        timer = setTimeout(tick, 2000);
      }
    }
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [load, draft?.status]);

  // 사진 보관 상태면 미리보기용 서명 URL 을 받아온다.
  useEffect(() => {
    if (draft?.status !== "photo_stash") return;
    let stop = false;
    fetch(`/api/drafts/${id}/images`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { urls: [] }))
      .then(({ urls }) => {
        if (!stop) setStashUrls(urls ?? []);
      })
      .catch(() => {});
    return () => {
      stop = true;
    };
  }, [draft?.status, id]);

  async function save(queue: boolean) {
    setSaving(true);
    const res = await fetch(`/api/drafts/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, body, status: queue ? "queued" : undefined }),
    });
    setSaving(false);
    if (res.ok && queue) router.push("/list");
    else if (res.ok) editedRef.current = false;
  }

  // 초안 확인 후 수정 요청 → 기존 초안 + 요청을 맥에 보내 다시 쓰게 한다(status='generating').
  async function requestRevision() {
    const text = revisionReq.trim();
    if (!text) return;
    setSaving(true);
    const res = await fetch(`/api/drafts/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title, // 사용자가 고른/고친 제목·본문을 기준으로 수정
        body,
        data: { ...(draft?.data ?? {}), revision_request: text },
        status: "generating",
      }),
    });
    setSaving(false);
    if (res.ok) {
      setRevisionReq("");
      editedRef.current = false;
      load(); // status 가 generating 으로 바뀌면 폴링 useEffect 가 다시 돌아 완성본을 보여줌
    }
  }

  // 보관해둔 사진으로 초안 생성 시작 — 폼 값을 request 에 합쳐 status='generating' 전환.
  async function generateFromStash() {
    const fd = new FormData(stashFormRef.current ?? undefined);
    const str = (k: string) => (fd.get(k) as string | null)?.trim() ?? "";
    const prev = (draft?.data?.request ?? {}) as Record<string, unknown>;
    const nextReq = {
      ...prev,
      structure_key: str("structure_key") || (prev.structure_key as string) || "free",
      keyword: str("keyword"),
      sponsor_type: str("sponsor_type") || (prev.sponsor_type as string) || "내돈내산",
      length: parseInt(str("length") || String(prev.length ?? 1500), 10) || 1500,
      photo_style: str("photo_style") || (prev.photo_style as string) || "감성 중심",
      memo: str("memo"),
    };
    setSaving(true);
    const res = await fetch(`/api/drafts/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        data: { ...(draft?.data ?? {}), request: nextReq },
        status: "generating",
      }),
    });
    setSaving(false);
    if (res.ok) {
      editedRef.current = false;
      load(); // status 가 generating 으로 바뀌면 폴링 useEffect 가 다시 돈다
    }
  }

  if (!draft) {
    return (
      <main className="mx-auto max-w-lg p-6">
        <p className="text-neutral-500">{loadErr || "불러오는 중…"}</p>
      </main>
    );
  }

  const d = draft.data ?? {};
  const titleCandidates: string[] = d.title_candidates ?? [];
  const hashtags: string[] = d.hashtags ?? [];
  const confirmNeeded: { item: string; note: string }[] = d.confirm_needed ?? [];
  const targetLen: number = d.request?.length ?? 0;
  const guidelineName: string = d.request?.guideline_name ?? "";
  // 네이버 '공백 포함' 글자수 기준: [사진N]/[영상N] 마커 제거 + 줄바꿈 제외(공백은 포함).
  const visibleLen = body
    .replace(/\[(?:사진|영상)\s*\d+\]/g, "")
    .replace(/[\r\n]/g, "").length;

  if (draft.status === "generating" || draft.status === "uploading") {
    return (
      <main className="mx-auto flex min-h-dvh max-w-lg flex-col items-center justify-center gap-4 p-6 text-center">
        <div className="h-10 w-10 animate-spin rounded-full border-4 border-neutral-200 border-t-neutral-800" />
        <p className="font-medium">
          {d.revision_request ? "맥이 수정 요청을 반영해 다시 쓰는 중이에요…" : "맥에서 초안을 쓰는 중이에요…"}
        </p>
        <p className="text-sm text-neutral-500">
          {d.revision_request ? (
            <>요청: “{String(d.revision_request).slice(0, 60)}”<br />맥이 깨어 있어야 진행됩니다.</>
          ) : (
            <>문체 학습 + 사진 분석 + 본문 작성. 보통 20~40초 걸려요.<br />맥이 깨어 있어야 진행됩니다.</>
          )}
        </p>
        <Link href="/list" className="text-sm text-blue-600">
          목록으로
        </Link>
      </main>
    );
  }

  if (draft.status === "error") {
    return (
      <main className="mx-auto max-w-lg p-6">
        <p className="mb-2 font-medium text-red-600">생성 실패</p>
        <p className="text-sm text-neutral-600">{d.error ?? "알 수 없는 오류"}</p>
        <Link href="/" className="mt-4 inline-block text-sm text-blue-600">
          ← 새 글 쓰기
        </Link>
      </main>
    );
  }

  // 사진만 보관해둔 상태 — 사진 미리보기 + 간단 폼으로 '이 사진들로 초안 생성' 시작.
  if (draft.status === "photo_stash") {
    const r = d.request ?? {};
    const fieldC =
      "rounded-lg border border-neutral-300 px-3 py-2 text-base w-full bg-white";
    const labelC = "text-sm font-medium text-neutral-700";
    return (
      <main className="mx-auto max-w-lg p-4 pb-24">
        <header className="mb-3 flex items-center justify-between">
          <Link href="/list" className="text-sm text-blue-600">
            ← 목록
          </Link>
          <span className="rounded-full bg-neutral-100 px-2 py-1 text-xs text-neutral-600">
            사진 보관 · {draft.images?.length ?? 0}장
          </span>
        </header>

        <p className="mb-3 rounded-lg bg-blue-50 px-3 py-2 text-sm text-blue-700">
          📷 보관해둔 사진이에요. 아래 정보를 채우고 <b>초안 생성</b>을 누르면 이 사진들로 글을 써요.
        </p>

        {stashUrls.length > 0 && (
          <div className="mb-4 grid grid-cols-4 gap-2">
            {stashUrls.map((u, i) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={i}
                src={u}
                alt={`보관 사진 ${i + 1}`}
                className="aspect-square w-full rounded-lg border border-neutral-200 object-cover"
              />
            ))}
          </div>
        )}

        <form ref={stashFormRef} className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1">
              <span className={labelC}>글 유형</span>
              <select
                name="structure_key"
                className={fieldC}
                defaultValue={r.structure_key || "free"}
              >
                {POST_TYPES.map((t) => (
                  <option key={t.key} value={t.key}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className={labelC}>협찬</span>
              <select
                name="sponsor_type"
                className={fieldC}
                defaultValue={r.sponsor_type || "내돈내산"}
              >
                {SPONSOR_TYPES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className={labelC}>글 길이</span>
              <select name="length" className={fieldC} defaultValue={String(r.length || 1500)}>
                {POST_LENGTHS.map((n) => (
                  <option key={n} value={n}>
                    {n}자
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className={labelC}>사진 노출</span>
              <select
                name="photo_style"
                className={fieldC}
                defaultValue={r.photo_style || "감성 중심"}
              >
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
            <input
              name="keyword"
              className={fieldC}
              defaultValue={r.keyword || ""}
              placeholder="예: 신당동 키즈카페"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className={labelC}>메모 — 최우선 반영</span>
            <textarea
              name="memo"
              className={`${fieldC} min-h-28`}
              defaultValue={r.memo || ""}
              placeholder="오늘 있었던 일, 느낌, 꼭 담고 싶은 내용을 편하게 적어주세요."
            />
          </label>
        </form>

        <button
          onClick={generateFromStash}
          disabled={saving}
          className="fixed inset-x-0 bottom-0 mx-auto max-w-lg bg-neutral-900 px-4 py-4 text-base font-semibold text-white disabled:opacity-50 sm:static sm:mt-4 sm:rounded-lg"
        >
          {saving ? "시작하는 중…" : "이 사진들로 초안 생성 🚀"}
        </button>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-lg p-4 pb-24">
      <header className="mb-3 flex items-center justify-between">
        <Link href="/list" className="text-sm text-blue-600">
          ← 목록
        </Link>
        <span className="rounded-full bg-neutral-100 px-2 py-1 text-xs text-neutral-600">
          {STATUS_LABEL[draft.status] ?? draft.status}
          {draft.images?.length ? ` · 사진 ${draft.images.length}장` : ""}
        </span>
      </header>

      {guidelineName && (
        <p className="mb-2 rounded-lg bg-emerald-50 px-3 py-1.5 text-xs text-emerald-700">
          📋 협찬 가이드 반영됨: {guidelineName}
        </p>
      )}

      {titleCandidates.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1">
          {titleCandidates.map((t, i) => (
            <button
              key={i}
              onClick={() => {
                editedRef.current = true;
                setTitle(t);
              }}
              className="rounded-full border border-neutral-300 px-2 py-1 text-xs text-neutral-700"
            >
              {t}
            </button>
          ))}
        </div>
      )}

      <input
        value={title}
        onChange={(e) => {
          editedRef.current = true;
          setTitle(e.target.value);
        }}
        placeholder="제목"
        className="mb-3 w-full rounded-lg border border-neutral-300 px-3 py-2 text-base font-medium"
      />

      <textarea
        value={body}
        onChange={(e) => {
          editedRef.current = true;
          setBody(e.target.value);
        }}
        className="min-h-[50vh] w-full rounded-lg border border-neutral-300 px-3 py-2 text-base leading-relaxed"
      />

      <p className="mt-1 text-right text-xs text-neutral-400">
        {visibleLen.toLocaleString()}자 (공백 포함, 사진자리 제외)
        {targetLen > 0 && (
          <span className={visibleLen >= targetLen ? "text-green-600" : "text-amber-600"}>
            {" "}/ 목표 {targetLen.toLocaleString()}자
          </span>
        )}
      </p>

      {confirmNeeded.length > 0 && (
        <div className="mt-3 rounded-lg bg-amber-50 p-3 text-sm">
          <p className="mb-1 font-medium text-amber-800">확인 필요</p>
          <ul className="list-disc pl-4 text-amber-700">
            {confirmNeeded.map((c, i) => (
              <li key={i}>
                {c.item} — {c.note}
              </li>
            ))}
          </ul>
        </div>
      )}

      {hashtags.length > 0 && (
        <p className="mt-3 text-xs text-neutral-500">#{hashtags.join(" #")}</p>
      )}

      <div className="mt-4 rounded-lg border border-neutral-200 bg-neutral-50 p-3">
        <p className="mb-1 text-sm font-medium text-neutral-700">✏️ 수정 요청</p>
        <p className="mb-2 text-xs text-neutral-400">
          고치고 싶은 점을 적어주세요. 맥이 이 초안을 바탕으로 요청만 반영해 다시 써요.
          <br />예: “도입부를 더 공감되게”, “가격 표 하나 추가”, “너무 광고 같은 문장 빼줘”.
        </p>
        <textarea
          value={revisionReq}
          onChange={(e) => setRevisionReq(e.target.value)}
          placeholder="수정 요청 입력…"
          className="min-h-20 w-full rounded-lg border border-neutral-300 px-3 py-2 text-base"
        />
        <button
          onClick={requestRevision}
          disabled={saving || !revisionReq.trim()}
          className="mt-2 w-full rounded-lg bg-blue-600 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
        >
          {saving ? "요청 보내는 중…" : "🔄 수정 요청해서 다시 쓰기"}
        </button>
      </div>

      <div className="fixed inset-x-0 bottom-0 mx-auto flex max-w-lg gap-2 bg-white/90 p-3 backdrop-blur">
        <button
          onClick={() => save(false)}
          disabled={saving}
          className="flex-1 rounded-lg border border-neutral-300 py-3 font-medium disabled:opacity-50"
        >
          임시 저장
        </button>
        <button
          onClick={() => save(true)}
          disabled={saving}
          className="flex-1 rounded-lg bg-neutral-900 py-3 font-semibold text-white disabled:opacity-50"
        >
          {saving ? "저장 중…" : "맥에 자동저장 🚀"}
        </button>
      </div>
    </main>
  );
}
