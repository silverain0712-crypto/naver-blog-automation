"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// 블로그 글의 사진/캡션으로 만드는 네이버 클립(세로 숏폼 mp4).
// mac_clip_generator.py(modules/clip_generator.py)가 완전히 별도 프로세스로 만든다 —
// 블로그 글쓰기/발행 파이프라인과는 무관.
//
// 흐름: 소스 사진 선택(선택, 기본 전체) → ① 대본 기획(clip_script, 장면별 AI모션
// 추천 포함) → 유저가 장면마다 자막 수정 + "AI 영상으로" 체크박스(motion_selected,
// 원본 영상이 있는 장면은 항상 그 영상을 쓰므로 체크박스 대신 안내만 보여줌).
// LLM이 추천 안 한(motion_worth !== "high") 장면을 강제로 켜면 모션 설명을 직접
// 입력해야 한다(motion_prompt_override, 2026-09-09) — 설명 없이 막연하게 켜면
// AI 영상이 거의 안 움직이는 문제가 실측돼서, 설명 없인 정지 사진으로 남긴다.
// 렌더 옵션(폰트/글자색/배경박스) 선택 → ② 영상 만들기(clip).
type Scene = {
  n?: number;
  caption?: string;
  narration?: string;
  seconds?: number;
  motion_worth?: string;
  motion_selected?: boolean;
  motion_prompt_override?: string;
  photoUrl?: string;
  videoBacked?: boolean;
};
type ClipScript = {
  hook?: string;
  cta?: string;
  clip?: { title?: string; caption?: string; tags?: string[] };
  total_seconds?: number;
  scenes?: Scene[];
};
type Clip = { path?: string; rev?: number; duration?: number; url: string; downloadUrl?: string };
type Job = { status?: string; error?: string } | null;
type Source = { n: number; url: string; videoBacked: boolean };
type RenderOpts = { font?: string; text_color?: string; box_style?: string };

const BUSY = ["requested", "running"];

const FONT_OPTIONS = [
  { value: "default", label: "기본 고딕" },
  { value: "jua", label: "주아 — 둥글둥글 귀여운" },
  { value: "black_han_sans", label: "검은고딕 — 두껍고 강한" },
  { value: "gaegu", label: "개구 — 손글씨 느낌" },
  { value: "nanum_myeongjo", label: "나눔명조 — 감성 세리프" },
  { value: "nanum_pen", label: "나눔펜 — 펜글씨" },
];
const COLOR_OPTIONS = [
  { value: "auto", label: "글자색: 자동(흰색)" },
  { value: "white", label: "흰색" },
  { value: "yellow", label: "형광 노랑" },
  { value: "pink", label: "핑크" },
  { value: "green", label: "연두" },
  { value: "black", label: "검정" },
];
const BOX_OPTIONS = [
  { value: "auto", label: "배경: 자동(검정 반투명)" },
  { value: "dark", label: "검정 반투명 상자" },
  { value: "light", label: "흰 반투명 상자" },
  { value: "none", label: "상자 없음(테두리 글자)" },
];

type Props = { id: string };

export default function ClipBuilder({ id }: Props) {
  const [script, setScript] = useState<ClipScript | null>(null);
  const [scriptJob, setScriptJob] = useState<Job>(null);
  const [clip, setClip] = useState<Clip | null>(null);
  const [job, setJob] = useState<Job>(null);
  const [render, setRender] = useState<RenderOpts>({});
  const [sources, setSources] = useState<Source[]>([]);
  const [selected, setSelected] = useState<Set<number> | null>(null); // null = 전체
  const [captionDrafts, setCaptionDrafts] = useState<Record<number, string>>({});
  const [hookDraft, setHookDraft] = useState<string | undefined>(undefined);
  const [motionPromptDrafts, setMotionPromptDrafts] = useState<Record<number, string>>({});
  const [feedback, setFeedback] = useState("");
  const [err, setErr] = useState("");
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // opts.full=false 면 job/scriptJob 상태만 갱신하고 사진/영상 URL은 건드리지 않는다 —
  // createSignedUrls 는 호출할 때마다 새 서명 URL을 발급해서 <img src>가 매번 바뀌면
  // 브라우저 캐시가 무의미해져 3초 폴링 내내 같은 사진을 계속 재다운로드하게 된다
  // (Veo 렌더는 씬당 최대 10분 걸려 폴링이 수백 번 돌 수 있음 — 실제로 Supabase 무료
  // egress 한도를 넘겨 서비스가 잠긴 원인 중 하나로 확인됨, 2026-09-09).
  const load = useCallback(async (opts: { full?: boolean } = {}) => {
    const res = await fetch(`/api/drafts/${id}/clips`, { cache: "no-store" });
    if (!res.ok) return { scriptJob: null, job: null } as { scriptJob: Job; job: Job };
    const data = await res.json();
    setScriptJob(data.scriptJob ?? null);
    setJob(data.job ?? null);
    if (opts.full) {
      setScript(data.script ?? null);
      setClip(data.clip ?? null);
      setRender(data.render ?? {});
      setSources(data.sources ?? []);
      setSelected(
        Array.isArray(data.sourcePhotos) ? new Set<number>(data.sourcePhotos) : null,
      );
      setFeedback(typeof data.feedback === "string" ? data.feedback : "");
    }
    return { scriptJob: data.scriptJob as Job, job: data.job as Job };
  }, [id]);

  // 대본 기획 중이거나 렌더 중이면 3초마다 상태만 확인 — 완료된 순간에만 전체(사진/
  // 영상 URL 포함) 다시 불러온다.
  useEffect(() => {
    let stop = false;
    let wasBusy = false;
    async function tick() {
      const { scriptJob: sj, job: j } = await load({ full: !wasBusy });
      if (stop) return;
      const busyNow = BUSY.includes(sj?.status ?? "") || BUSY.includes(j?.status ?? "");
      if (busyNow) {
        wasBusy = true;
        timer.current = setTimeout(tick, 3000);
      } else if (wasBusy) {
        await load({ full: true });
      }
    }
    tick();
    return () => {
      stop = true;
      clearTimeout(timer.current);
    };
  }, [load, scriptJob?.status, job?.status]);

  function toggleSource(n: number) {
    setSelected((prev) => {
      // null(전체 선택) 상태에서 하나를 끄면, 그때부터 "전체에서 이것만 뺀 목록"이 된다.
      const base = prev ?? new Set(sources.map((s) => s.n));
      const next = new Set(base);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });
  }

  async function requestPlan() {
    setErr("");
    const sourcePhotos = selected ? Array.from(selected) : undefined;
    const res = await fetch(`/api/drafts/${id}/clips/plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(sourcePhotos ? { source_photos: sourcePhotos } : {}),
        feedback,
      }),
    });
    if (!res.ok) {
      const { error } = await res.json().catch(() => ({ error: "요청 실패" }));
      setErr(error ?? "요청 실패");
      return;
    }
    setOpen(true);
    load({ full: true });
  }

  async function requestRender() {
    setErr("");
    const res = await fetch(`/api/drafts/${id}/clips`, { method: "POST" });
    if (!res.ok) {
      const { error } = await res.json().catch(() => ({ error: "요청 실패" }));
      setErr(error ?? "요청 실패");
      return;
    }
    load({ full: true });
  }

  async function patchScene(
    n: number,
    patch: { motion_selected?: boolean; caption?: string; motion_prompt_override?: string },
  ) {
    const res = await fetch(`/api/drafts/${id}/clips`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ n, ...patch }),
    });
    if (!res.ok) {
      setErr("장면 저장 실패");
      load({ full: true });
    }
  }

  async function reorderScenes(newOrder: Scene[]) {
    setScript((s) => (s ? { ...s, scenes: newOrder } : s));
    const res = await fetch(`/api/drafts/${id}/clips`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reorder: newOrder.map((sc) => sc.n) }),
    });
    if (!res.ok) {
      setErr("순서 저장 실패");
      load({ full: true });
    }
  }

  function moveScene(n: number, direction: "up" | "down") {
    const scenes = script?.scenes ?? [];
    const i = scenes.findIndex((sc) => sc.n === n);
    const j = direction === "up" ? i - 1 : i + 1;
    if (i < 0 || j < 0 || j >= scenes.length) return;
    const next = [...scenes];
    [next[i], next[j]] = [next[j], next[i]];
    reorderScenes(next);
  }

  function toggleMotion(n: number, next: boolean) {
    setScript((s) =>
      s ? { ...s, scenes: (s.scenes ?? []).map((sc) => (sc.n === n ? { ...sc, motion_selected: next } : sc)) } : s,
    );
    patchScene(n, { motion_selected: next });
  }

  function saveCaption(n: number) {
    const text = captionDrafts[n];
    if (text === undefined) return;
    setScript((s) =>
      s ? { ...s, scenes: (s.scenes ?? []).map((sc) => (sc.n === n ? { ...sc, caption: text } : sc)) } : s,
    );
    patchScene(n, { caption: text });
  }

  async function saveHook() {
    const text = hookDraft;
    if (text === undefined) return;
    setScript((s) => (s ? { ...s, hook: text } : s));
    const res = await fetch(`/api/drafts/${id}/clips`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hook: text }),
    });
    if (!res.ok) {
      setErr("훅 저장 실패");
      load({ full: true });
    }
  }

  function saveMotionPrompt(n: number) {
    const text = motionPromptDrafts[n];
    if (text === undefined) return;
    setScript((s) =>
      s
        ? {
            ...s,
            scenes: (s.scenes ?? []).map((sc) =>
              sc.n === n ? { ...sc, motion_prompt_override: text } : sc,
            ),
          }
        : s,
    );
    patchScene(n, { motion_prompt_override: text });
  }

  async function updateRender(patch: RenderOpts) {
    const next = { ...render, ...patch };
    setRender(next);
    await fetch(`/api/drafts/${id}/clips`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ render: patch }),
    });
  }

  const scriptBusy = BUSY.includes(scriptJob?.status ?? "");
  const renderBusy = BUSY.includes(job?.status ?? "");
  const scenes = script?.scenes ?? [];
  const selectedCount = selected ? selected.size : sources.length;

  return (
    <section className="mt-4 rounded-lg border border-neutral-200 bg-white">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2.5 text-left"
      >
        <span className="text-sm font-medium">
          🎬 네이버 클립
          {clip && <span className="ml-1 text-neutral-500">· 만들어짐</span>}
          {(scriptBusy || renderBusy) && <span className="ml-1 text-blue-600">· 진행 중…</span>}
        </span>
        <span className="text-xs text-neutral-400">{open ? "접기" : "펼치기"}</span>
      </button>

      {open && (
        <div className="border-t border-neutral-100 px-3 py-3">
          {err && <p className="mb-2 text-xs text-red-600">{err}</p>}
          {scriptJob?.status === "error" && scriptJob.error && (
            <p className="mb-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
              대본 기획 실패: {scriptJob.error}
            </p>
          )}
          {job?.status === "error" && job.error && (
            <p className="mb-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
              영상 생성 실패: {job.error}
            </p>
          )}

          <div className="mb-3 flex flex-col gap-1">
            <span className="text-xs font-medium text-neutral-600">
              클립 전체 수정 요청(선택)
            </span>
            <textarea
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              rows={2}
              placeholder="예: 템포를 더 빠르게, 자막을 더 짧게, 도입부를 더 임팩트있게"
              className="w-full rounded border border-neutral-300 px-2 py-1.5 text-xs"
            />
            <span className="text-[11px] text-neutral-400">
              다음 대본 기획(재기획 포함)에 반영돼요.
            </span>
          </div>

          {!script && !scriptBusy && (
            <div className="flex flex-col gap-2">
              <p className="text-xs text-neutral-500">
                이 글의 사진과 캡션으로 세로 숏폼 대본을 먼저 짜요. 클립에 쓸 사진을
                고르고 싶으면 아래에서 체크를 해제하세요(기본은 전체 선택).
              </p>
              {sources.length > 0 && (
                <div className="grid grid-cols-5 gap-1.5">
                  {sources.map((s) => {
                    const checked = selected ? selected.has(s.n) : true;
                    return (
                      <label key={s.n} className="relative aspect-square cursor-pointer">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={s.url}
                          alt={`사진 ${s.n}`}
                          className={`h-full w-full rounded-md border object-cover ${
                            checked ? "border-neutral-900" : "border-neutral-200 opacity-40"
                          }`}
                        />
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleSource(s.n)}
                          className="absolute right-1 top-1"
                        />
                        {s.videoBacked && (
                          <span className="absolute bottom-1 left-1 rounded bg-black/60 px-1 text-[9px] text-white">
                            🎥
                          </span>
                        )}
                      </label>
                    );
                  })}
                </div>
              )}
              {sources.length > 0 && (
                <p className="text-xs text-neutral-400">{selectedCount}장 선택됨</p>
              )}
              <button
                type="button"
                onClick={requestPlan}
                disabled={selectedCount === 0}
                className="self-start rounded-lg border border-neutral-300 px-3 py-1.5 text-xs font-medium disabled:opacity-40"
              >
                ① 대본 기획
              </button>
            </div>
          )}

          {scriptBusy && <p className="mb-2 text-xs text-blue-600">대본 기획 중이에요…</p>}

          {script && (
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-1 rounded-lg bg-amber-50 p-2.5">
                <span className="text-xs font-medium text-amber-800">
                  훅(0~2초 문구) — 1번째 장면 자막을 이 문구로 바꿔서 렌더돼요
                </span>
                <textarea
                  value={hookDraft ?? script.hook ?? ""}
                  onChange={(e) => setHookDraft(e.target.value)}
                  onBlur={saveHook}
                  rows={2}
                  className="w-full rounded border border-amber-300 bg-white px-2 py-1 text-xs"
                />
              </div>

              <div className="flex flex-col gap-1.5 rounded-lg bg-neutral-50 p-2.5">
                <span className="text-xs font-medium text-neutral-600">자막 스타일</span>
                <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-3">
                  <select
                    value={render.font ?? "default"}
                    onChange={(e) => updateRender({ font: e.target.value })}
                    className="rounded border border-neutral-300 px-2 py-1.5 text-xs"
                  >
                    {FONT_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                  <select
                    value={render.text_color ?? "auto"}
                    onChange={(e) => updateRender({ text_color: e.target.value })}
                    className="rounded border border-neutral-300 px-2 py-1.5 text-xs"
                  >
                    {COLOR_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                  <select
                    value={render.box_style ?? "auto"}
                    onChange={(e) => updateRender({ box_style: e.target.value })}
                    className="rounded border border-neutral-300 px-2 py-1.5 text-xs"
                  >
                    {BOX_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <ul className="flex flex-col gap-2">
                {scenes.map((sc, idx) => (
                  <li
                    key={sc.n}
                    className="flex items-start gap-2 rounded-lg border border-neutral-200 p-2"
                  >
                    <div className="flex shrink-0 flex-col gap-0.5">
                      <button
                        type="button"
                        onClick={() => moveScene(sc.n as number, "up")}
                        disabled={idx === 0}
                        aria-label="위로 이동"
                        className="rounded border border-neutral-300 px-1.5 py-0.5 text-xs leading-none disabled:opacity-30"
                      >
                        ▲
                      </button>
                      <button
                        type="button"
                        onClick={() => moveScene(sc.n as number, "down")}
                        disabled={idx === scenes.length - 1}
                        aria-label="아래로 이동"
                        className="rounded border border-neutral-300 px-1.5 py-0.5 text-xs leading-none disabled:opacity-30"
                      >
                        ▼
                      </button>
                    </div>
                    {sc.photoUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={sc.photoUrl}
                        alt={`장면 ${sc.n}`}
                        className="h-14 w-14 shrink-0 rounded-md border border-neutral-200 object-cover"
                      />
                    ) : (
                      <div className="h-14 w-14 shrink-0 rounded-md border border-dashed border-neutral-200" />
                    )}
                    <div className="flex-1">
                      <span className="text-xs text-neutral-400">
                        {idx + 1}번째 · {sc.seconds}초
                      </span>
                      <textarea
                        value={captionDrafts[sc.n as number] ?? sc.caption ?? ""}
                        onChange={(e) =>
                          setCaptionDrafts((p) => ({ ...p, [sc.n as number]: e.target.value }))
                        }
                        onBlur={() => saveCaption(sc.n as number)}
                        rows={2}
                        className="mt-0.5 w-full rounded border border-neutral-300 px-2 py-1 text-xs"
                      />
                      {idx === 0 && (
                        <p className="mt-0.5 text-xs text-amber-700">
                          ⚠️ 이 캡션 대신 위쪽 &quot;훅&quot; 문구가 자막으로 렌더돼요
                        </p>
                      )}
                      {sc.narration && (
                        <p className="mt-0.5 text-xs text-neutral-400">나레이션: {sc.narration}</p>
                      )}
                      {sc.videoBacked ? (
                        <p className="mt-1 text-xs text-emerald-700">🎥 원본 영상 사용</p>
                      ) : (
                        <>
                          <label className="mt-1 flex items-center gap-1.5 text-xs text-neutral-600">
                            <input
                              type="checkbox"
                              checked={!!sc.motion_selected}
                              onChange={(e) => toggleMotion(sc.n as number, e.target.checked)}
                            />
                            AI 영상으로(움직임)
                            {sc.motion_worth === "high" && (
                              <span className="text-neutral-400">· 추천</span>
                            )}
                          </label>
                          {sc.motion_selected && sc.motion_worth !== "high" && (
                            <div className="mt-1 flex flex-col gap-0.5">
                              <textarea
                                value={
                                  motionPromptDrafts[sc.n as number] ?? sc.motion_prompt_override ?? ""
                                }
                                onChange={(e) =>
                                  setMotionPromptDrafts((p) => ({
                                    ...p,
                                    [sc.n as number]: e.target.value,
                                  }))
                                }
                                onBlur={() => saveMotionPrompt(sc.n as number)}
                                rows={2}
                                placeholder="이 장면을 어떻게 움직이면 좋을지 설명해주세요(영어 권장) — 비워두면 AI 영상 대신 정지 사진을 씁니다"
                                className="w-full rounded border border-neutral-300 px-2 py-1 text-xs"
                              />
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  </li>
                ))}
              </ul>

              {renderBusy && (
                <p className="text-xs text-blue-600">
                  영상 만드는 중이에요(AI 모션을 켠 장면이 있으면 씬당 1~10분 걸릴 수 있어요)…
                </p>
              )}

              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={requestRender}
                  disabled={renderBusy}
                  className="rounded-lg border border-neutral-300 px-3 py-1.5 text-xs font-medium disabled:opacity-40"
                >
                  {clip ? "다시 만들기" : "② 영상 만들기"}
                </button>
                <button
                  type="button"
                  onClick={requestPlan}
                  disabled={scriptBusy || renderBusy}
                  className="rounded-lg border border-neutral-300 px-3 py-1.5 text-xs font-medium disabled:opacity-40"
                >
                  대본 새로 기획
                </button>
              </div>

              {clip && (
                <div className="flex flex-col gap-2 border-t border-neutral-100 pt-2">
                  <video
                    controls
                    src={clip.url}
                    className="w-full max-w-[280px] rounded-lg border border-neutral-200"
                  />
                  <div className="text-xs text-neutral-500">
                    {clip.duration ? `${clip.duration}초` : ""}
                  </div>
                  {script.clip?.title && (
                    <p className="text-xs text-neutral-600">
                      클립 제목 후보: <span className="font-medium">{script.clip.title}</span>
                    </p>
                  )}
                  {script.clip?.caption && (
                    <p className="whitespace-pre-line text-xs text-neutral-600">
                      클립 캡션: {script.clip.caption}
                    </p>
                  )}
                  {!!script.clip?.tags?.length && (
                    <p className="text-xs text-neutral-600">
                      태그: {script.clip.tags.map((t) => `#${t}`).join(" ")}
                    </p>
                  )}
                  <a
                    href={clip.downloadUrl || clip.url}
                    download
                    className="self-start rounded-lg border border-neutral-300 px-3 py-1.5 text-xs font-medium"
                  >
                    다운로드
                  </a>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
