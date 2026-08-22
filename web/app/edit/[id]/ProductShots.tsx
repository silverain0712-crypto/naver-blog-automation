"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// 맥이 상품 링크를 긁어 Gemini 로 만든 상세컷. 사진마다 피드백을 적어 다시 만들 수 있다.
type Shot = { path: string; url: string; label?: string; feedback?: string; rev?: number };
type Job = { status?: string; error?: string; target?: number } | null;

const BUSY = ["requested", "running"];

export default function ProductShots({ id, hasLink }: { id: string; hasLink: boolean }) {
  const [shots, setShots] = useState<Shot[]>([]);
  const [job, setJob] = useState<Job>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [err, setErr] = useState("");
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const load = useCallback(async () => {
    const res = await fetch(`/api/drafts/${id}/shots`, { cache: "no-store" });
    if (!res.ok) return null;
    const data = await res.json();
    setShots(data.shots ?? []);
    setJob(data.job ?? null);
    return data.job as Job;
  }, [id]);

  // 만드는 중이면 3초마다 확인. 끝나면 폴링을 멈춘다.
  useEffect(() => {
    let stop = false;
    async function tick() {
      const j = await load();
      if (stop) return;
      if (j && BUSY.includes(j.status ?? "")) timer.current = setTimeout(tick, 3000);
    }
    tick();
    return () => {
      stop = true;
      clearTimeout(timer.current);
    };
  }, [load, job?.status]);

  async function request(payload: Record<string, unknown>) {
    setErr("");
    const res = await fetch(`/api/drafts/${id}/shots`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const { error } = await res.json().catch(() => ({ error: "요청 실패" }));
      setErr(error ?? "요청 실패");
      return;
    }
    setOpen(true);
    load();
  }

  const busy = BUSY.includes(job?.status ?? "");
  const hasShots = shots.length > 0;

  return (
    <section className="mt-4 rounded-lg border border-neutral-200 bg-white">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2.5 text-left"
      >
        <span className="text-sm font-medium">
          상품 사진 생성
          {hasShots && <span className="ml-1 text-neutral-500">· {shots.length}장</span>}
          {busy && <span className="ml-1 text-blue-600">· 만드는 중…</span>}
        </span>
        <span className="text-xs text-neutral-400">{open ? "접기" : "펼치기"}</span>
      </button>

      {open && (
        <div className="border-t border-neutral-100 px-3 py-3">
          {!hasLink ? (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
              글감에 상품 링크가 없어서 만들 수 없어요. 링크를 넣으면 실제 상품 사진을 가져와
              그 제품 그대로 상세컷을 만듭니다.
            </p>
          ) : (
            <>
              <p className="mb-2 text-xs text-neutral-500">
                상품 링크에서 실제 사진을 가져와, 그 제품 그대로 상세컷을 만듭니다.
                직접 찍은 사진이 있으면 안 만드셔도 됩니다.
              </p>

              <button
                type="button"
                onClick={() => request({ count: 3 })}
                disabled={busy}
                className="w-full rounded-lg bg-neutral-900 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
              >
                {busy ? "만드는 중… (1~2분)" : hasShots ? "새로 3장 만들기" : "상세컷 3장 만들기 ✨"}
              </button>

              {err && <p className="mt-2 text-xs text-red-600">{err}</p>}
              {job?.status === "error" && job.error && (
                <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
                  실패: {job.error}
                </p>
              )}

              {hasShots && (
                <ul className="mt-3 flex flex-col gap-4">
                  {shots.map((s, i) => (
                    <li key={s.path} className="flex flex-col gap-2">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={s.url}
                        alt={s.label ?? `상세컷 ${i + 1}`}
                        className="w-full rounded-lg border border-neutral-200"
                      />
                      <div className="flex items-center justify-between text-xs text-neutral-500">
                        <span>{s.label ?? `상세컷 ${i + 1}`}</span>
                        <a href={s.url} download className="text-blue-600">
                          내려받기
                        </a>
                      </div>
                      {s.feedback && (
                        <p className="rounded-lg bg-neutral-50 px-2.5 py-1.5 text-xs text-neutral-600">
                          반영된 요청: {s.feedback}
                        </p>
                      )}
                      <textarea
                        value={notes[i] ?? ""}
                        onChange={(e) => setNotes((p) => ({ ...p, [i]: e.target.value }))}
                        placeholder="고치고 싶은 점 (예: 뚜껑을 닫아주세요, 배경을 더 밝게)"
                        rows={2}
                        className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
                      />
                      <button
                        type="button"
                        onClick={() => request({ target: i, feedback: notes[i] ?? "" })}
                        disabled={busy || !(notes[i] ?? "").trim()}
                        className="self-start rounded-lg border border-neutral-300 px-3 py-1.5 text-xs font-medium disabled:opacity-40"
                      >
                        이 피드백으로 다시 찍기
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
