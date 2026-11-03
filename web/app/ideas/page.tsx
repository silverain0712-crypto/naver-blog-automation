"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

type Idea = { id: string; text: string; memo: string; created_at: string };

export default function IdeasPage() {
  const router = useRouter();
  const [ideas, setIdeas] = useState<Idea[] | null>(null);
  const [text, setText] = useState("");
  const [memo, setMemo] = useState("");
  const [adding, setAdding] = useState(false);
  const [err, setErr] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  async function load() {
    const res = await fetch("/api/ideas", { cache: "no-store" });
    if (res.ok) setIdeas((await res.json()).ideas);
  }

  useEffect(() => {
    load();
  }, []);

  async function add() {
    // adding 가드 — Enter 중복 입력 등으로 add()가 짧은 간격에 두 번 불려도
    // 두 번째 호출은 여기서 막는다(버튼 disabled만으론 동시 호출을 못 막음).
    if (adding) return;
    const t = text.trim();
    if (!t) return;
    setAdding(true);
    setErr("");
    try {
      const res = await fetch("/api/ideas", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: t, memo: memo.trim() }),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.error ?? "저장 실패");
      }
      setText("");
      setMemo("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
    setAdding(false);
  }

  async function remove(id: string) {
    setBusyId(id);
    await fetch(`/api/ideas/${id}`, { method: "DELETE" });
    setBusyId(null);
    await load();
  }

  // 이 메모로 글쓰기 화면으로 이동. 실제로 글이 써지면(초안 생성 성공) 그때 메모를 지운다
  // — pendingIdea 를 새 글 화면이 읽어서 메모칸에 채우고, 성공 시 DELETE 한다.
  function writeThis(idea: Idea) {
    try {
      localStorage.setItem(
        "pendingIdea",
        JSON.stringify({ id: idea.id, text: idea.text, memo: idea.memo }),
      );
    } catch {
      /* 저장소 접근 실패해도 그냥 빈 메모로 이동 */
    }
    router.push("/");
  }

  return (
    <main className="mx-auto max-w-lg p-4 pb-24">
      <header className="mb-3 flex items-center justify-between">
        <h1 className="text-lg font-semibold">💡 글감 메모</h1>
        <Link href="/" className="text-sm text-blue-600">
          새 글 쓰기 →
        </Link>
      </header>

      <p className="mb-3 text-sm text-neutral-500">
        생각나는 글감을 적어두면 안 잊어버려요. 이걸로 글을 쓰기 시작하면 자동으로 지워져요.
      </p>

      <div className="mb-4 flex flex-col gap-2 rounded-lg border border-neutral-200 bg-white p-3">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            // 한글 입력 중 조합 확정 Enter 는 무시 — 안 그러면 조합 확정용 Enter 와
            // 그 다음 실제 Enter, 두 번이 다 잡혀 글감이 2개씩 저장된다.
            if (e.key === "Enter" && !e.nativeEvent.isComposing) {
              e.preventDefault();
              add();
            }
          }}
          placeholder="예: 신당동 새로 생긴 파스타집"
          className="rounded-lg border border-neutral-300 px-3 py-2 text-base"
        />
        <textarea
          value={memo}
          onChange={(e) => setMemo(e.target.value)}
          placeholder="메모 — 최우선 반영에 넣을 내용 (선택, 나중에 이 글감으로 글쓰기 누르면 자동으로 들어가요)"
          rows={2}
          className="rounded-lg border border-neutral-300 px-3 py-2 text-sm"
        />
        <button
          type="button"
          onClick={add}
          disabled={adding || !text.trim()}
          className="self-end rounded-lg bg-neutral-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
        >
          추가
        </button>
      </div>
      {err && <p className="mb-3 text-sm text-red-600">{err}</p>}

      {ideas === null ? (
        <p className="text-sm text-neutral-400">불러오는 중…</p>
      ) : ideas.length === 0 ? (
        <p className="text-sm text-neutral-400">아직 메모한 글감이 없어요.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {ideas.map((idea) => (
            <li
              key={idea.id}
              className="flex items-start gap-2 rounded-lg border border-neutral-200 bg-white px-3 py-2.5"
            >
              <div className="flex-1">
                <p className="text-sm">{idea.text}</p>
                {idea.memo && (
                  <p className="mt-0.5 line-clamp-2 text-xs text-neutral-500">{idea.memo}</p>
                )}
              </div>
              <button
                type="button"
                onClick={() => writeThis(idea)}
                className="shrink-0 rounded-lg border border-neutral-300 px-3 py-1.5 text-xs font-medium"
              >
                이걸로 글쓰기 →
              </button>
              <button
                type="button"
                onClick={() => remove(idea.id)}
                disabled={busyId === idea.id}
                className="shrink-0 text-neutral-400 disabled:opacity-40"
                aria-label="삭제"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
