"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// 정보성/화제성 글에 자동으로 만들어지는 카드뉴스(modules/card_images.py, gpt-image-1/
// 힉스필드). 카드마다 피드백을 적어 그 한 장만 같은 내용으로 다시 만들 수 있다.
type Card = {
  photo_number: number;
  role: string;
  section: string;
  caption: string;
  feedback: string;
  rev: number;
  url: string;
};
type Job = { status?: string; error?: string; target?: number } | null;

const BUSY = ["requested", "running"];

const ROLE_LABEL: Record<string, string> = {
  cover: "표지",
  timeline: "타임라인",
  comparison: "비교표",
  card: "카드형",
  checklist: "체크리스트",
  lifestyle: "실사",
};

type Props = { id: string };

export default function CardImages({ id }: Props) {
  const [cards, setCards] = useState<Card[]>([]);
  const [job, setJob] = useState<Job>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [err, setErr] = useState("");
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const load = useCallback(async () => {
    const res = await fetch(`/api/drafts/${id}/cards`, { cache: "no-store" });
    if (!res.ok) return null;
    const data = await res.json();
    setCards(data.cards ?? []);
    setJob(data.job ?? null);
    setLoaded(true);
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

  async function regenerate(photoNumber: number, feedback: string) {
    setErr("");
    const res = await fetch(`/api/drafts/${id}/cards`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: photoNumber, feedback }),
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
  // 자동 삽화가 없는 글(사진을 직접 올린 맛집/여행 등)은 아예 렌더링하지 않는다.
  if (loaded && cards.length === 0) return null;

  return (
    <section className="mt-4 rounded-lg border border-neutral-200 bg-white">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2.5 text-left"
      >
        <span className="text-sm font-medium">
          🖼️ 자동 생성 이미지
          {cards.length > 0 && <span className="ml-1 text-neutral-500">· {cards.length}장</span>}
          {busy && <span className="ml-1 text-blue-600">· 다시 만드는 중…</span>}
        </span>
        <span className="text-xs text-neutral-400">{open ? "접기" : "펼치기"}</span>
      </button>

      {open && (
        <div className="border-t border-neutral-100 px-3 py-3">
          <p className="mb-2 text-xs text-neutral-500">
            사진 없이 자동으로 만든 카드뉴스예요. 마음에 안 드는 카드가 있으면 아래에 고치고
            싶은 점을 적고 그 카드만 다시 만들 수 있어요(같은 자리 · 같은 내용 기준).
          </p>

          {err && <p className="mb-2 text-xs text-red-600">{err}</p>}
          {job?.status === "error" && job.error && (
            <p className="mb-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
              실패: {job.error}
            </p>
          )}

          <ul className="flex flex-col gap-4">
            {cards.map((c) => {
              const isTarget = busy && job?.target === c.photo_number;
              return (
                <li key={c.photo_number} className="flex flex-col gap-2">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={c.url}
                    alt={c.section || `사진 ${c.photo_number}`}
                    className="w-full rounded-lg border border-neutral-200"
                  />
                  <div className="flex items-center justify-between text-xs text-neutral-500">
                    <span>
                      사진 {c.photo_number} · {ROLE_LABEL[c.role] ?? c.role}
                      {c.section && ` · ${c.section}`}
                    </span>
                    {isTarget && <span className="text-blue-600">다시 만드는 중…</span>}
                  </div>
                  {c.feedback && (
                    <p className="rounded-lg bg-neutral-50 px-2.5 py-1.5 text-xs text-neutral-600">
                      반영된 요청: {c.feedback}
                    </p>
                  )}
                  <textarea
                    value={notes[c.photo_number] ?? ""}
                    onChange={(e) =>
                      setNotes((p) => ({ ...p, [c.photo_number]: e.target.value }))
                    }
                    placeholder="고치고 싶은 점 (예: 글자를 더 크게, 오탈자 고쳐줘, 색을 더 밝게)"
                    rows={2}
                    className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
                  />
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        regenerate(c.photo_number, (notes[c.photo_number] ?? "").trim())
                      }
                      disabled={busy || !(notes[c.photo_number] ?? "").trim()}
                      className="rounded-lg border border-neutral-300 px-3 py-1.5 text-xs font-medium disabled:opacity-40"
                    >
                      이 피드백으로 다시 만들기
                    </button>
                    <button
                      type="button"
                      onClick={() => regenerate(c.photo_number, "")}
                      disabled={busy}
                      className="rounded-lg border border-neutral-200 px-3 py-1.5 text-xs text-neutral-600 disabled:opacity-40"
                    >
                      그대로 한 번 더 뽑기
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </section>
  );
}
