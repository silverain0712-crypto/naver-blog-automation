"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { STATUS_LABEL } from "@/lib/constants";

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
  const editedRef = useRef(false); // 사용자가 편집을 시작하면 폴링이 덮어쓰지 않게

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

  // 생성 중이면 2초마다 폴링, draft_ready 되면 멈춤
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
  }, [load]);

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
  // [사진N]/[영상N] 자리표시는 실제 글자수에 안 들어가므로 빼고 센다(공백 포함).
  const visibleLen = body.replace(/\[(?:사진|영상)\s*\d+\]/g, "").length;

  if (draft.status === "generating" || draft.status === "uploading") {
    return (
      <main className="mx-auto flex min-h-dvh max-w-lg flex-col items-center justify-center gap-4 p-6 text-center">
        <div className="h-10 w-10 animate-spin rounded-full border-4 border-neutral-200 border-t-neutral-800" />
        <p className="font-medium">맥에서 초안을 쓰는 중이에요…</p>
        <p className="text-sm text-neutral-500">
          문체 학습 + 사진 분석 + 본문 작성. 보통 20~40초 걸려요.
          <br />맥이 깨어 있어야 진행됩니다.
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
