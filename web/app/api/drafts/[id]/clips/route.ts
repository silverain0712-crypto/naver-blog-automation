import { NextRequest, NextResponse } from "next/server";
import { getDraft, updateDraft, createSignedDownloads } from "@/lib/db";

export const runtime = "nodejs";

// 맥 클립 생성기(mac_clip_generator.py, modules/clip_generator.py)가 만드는 네이버 클립
// (세로 숏폼 mp4). 대본 기획(clip_script)과 실제 렌더(clip)가 분리돼 있다 — 장면별로
// AI 모션(Veo) 여부·자막 텍스트를 유저가 직접 고를 수 있게 하기 위해서다.
type Scene = {
  n?: number;
  image?: number | null;
  caption?: string;
  narration?: string;
  seconds?: number;
  motion_worth?: string;
  motion_prompt?: string;
  motion_selected?: boolean;
  motion_prompt_override?: string;
};
type ClipScript = {
  hook?: string;
  cta?: string;
  clip?: { title?: string; caption?: string; tags?: string[] };
  total_seconds?: number;
  scenes?: Scene[];
};
type ClipMeta = {
  path?: string;
  rev?: number;
  duration?: number;
  script?: ClipScript;
};
type VideoMeta = { path?: string; gif_photo_number?: number | null };
type ClipRender = { font?: string; text_color?: string; box_style?: string };

function scriptOf(data: Record<string, unknown> | null): ClipScript | null {
  const raw = (data as { clip_script?: unknown } | null)?.clip_script;
  return raw && typeof raw === "object" ? (raw as ClipScript) : null;
}
function clipOf(data: Record<string, unknown> | null): ClipMeta | null {
  const raw = (data as { clip?: unknown } | null)?.clip;
  return raw && typeof raw === "object" ? (raw as ClipMeta) : null;
}
function videosOf(data: Record<string, unknown> | null): VideoMeta[] {
  const raw = (data as { videos?: unknown } | null)?.videos;
  return Array.isArray(raw) ? (raw as VideoMeta[]) : [];
}
function renderOf(data: Record<string, unknown> | null): ClipRender {
  const raw = (data as { clip_render?: unknown } | null)?.clip_render;
  return raw && typeof raw === "object" ? (raw as ClipRender) : {};
}

// GET /api/drafts/[id]/clips → 대본 기획 상태·장면(사진 썸네일 포함) + 렌더된 클립 + 렌더 옵션.
export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });

    const images = draft.images ?? [];
    const script = scriptOf(draft.data);
    const scenes = script?.scenes ?? [];
    // 원본 영상이 있는 사진 번호 집합 — 그 장면은 이미 실사 영상을 쓰므로
    // AI 모션(Veo) 선택 자체가 의미 없다(modules/video_maker.py 가 항상 원본 영상을
    // 우선 사용하도록 되어 있음).
    const videoBackedNumbers = new Set(
      videosOf(draft.data)
        .map((v) => v.gif_photo_number)
        .filter((n): n is number => typeof n === "number"),
    );

    const photoPaths = scenes.map((sc) =>
      typeof sc.image === "number" && sc.image > 0 ? images[sc.image - 1] ?? "" : "",
    );
    const photoUrls = await createSignedDownloads(photoPaths.filter(Boolean));
    let ui = 0;
    const scenesWithUrl = scenes.map((sc) => {
      const hasPhoto = typeof sc.image === "number" && sc.image > 0 && !!images[sc.image - 1];
      return {
        ...sc,
        photoUrl: hasPhoto ? photoUrls[ui++] ?? "" : "",
        videoBacked: typeof sc.image === "number" && videoBackedNumbers.has(sc.image),
      };
    });

    const scriptJob = (draft.data as { clip_script_job?: unknown } | null)?.clip_script_job ?? null;
    const job = (draft.data as { clip_job?: unknown } | null)?.clip_job ?? null;

    const meta = clipOf(draft.data);
    let clip = null;
    if (meta?.path) {
      const [url] = await createSignedDownloads([meta.path]);
      const [downloadUrl] = await createSignedDownloads([meta.path], 60 * 60, {
        download: "naver_clip.mp4",
      });
      clip = { ...meta, url: url ?? "", downloadUrl: downloadUrl ?? url ?? "" };
    }

    // 이 글의 사진 전체(소스 선택 체크리스트용) — 대본이 아직 없어도 항상 내려준다.
    // 썸네일 빌더(ThumbnailBuilder.tsx)가 images[] 맨 뒤에 붙인 합성 이미지(제목
    // 문구·해시태그가 이미 박혀 있는 `{id}/thumb.png`)는 클립 소재로 안 어울려서
    // 체크리스트 자체에서 뺀다(2026-09-09) — 백엔드(clip_generator._thumbnail_photo_number)
    // 에서도 같은 기준으로 한 번 더 제외하니, 여기서 빠뜨려도 최종 대본엔 안 들어간다.
    const allUrls = await createSignedDownloads(images);
    const sources = images
      .map((path, i) => ({
        n: i + 1,
        url: allUrls[i] ?? "",
        videoBacked: videoBackedNumbers.has(i + 1),
        isThumbnail: path.endsWith("/thumb.png"),
      }))
      .filter((s) => !s.isThumbnail)
      .map(({ n, url, videoBacked }) => ({ n, url, videoBacked }));
    const sourcePhotosRaw = (draft.data as { clip_source_photos?: unknown } | null)
      ?.clip_source_photos;
    const sourcePhotos = Array.isArray(sourcePhotosRaw) ? (sourcePhotosRaw as number[]) : null;
    const feedback = (draft.data as { clip_feedback?: unknown } | null)?.clip_feedback;

    return NextResponse.json({
      scriptJob,
      script: script ? { ...script, scenes: scenesWithUrl } : null,
      job,
      clip,
      render: renderOf(draft.data),
      sources,
      sourcePhotos,
      feedback: typeof feedback === "string" ? feedback : "",
    });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}

// POST /api/drafts/[id]/clips → 지금 저장된 clip_script(+유저가 고른 motion_selected/
// caption/clip_render)로 실제 렌더를 요청한다. 대본이 아직 없으면 400.
export async function POST(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });

    if (!(draft.images ?? []).length) {
      return NextResponse.json({ error: "이 글에는 사진이 없어 클립을 만들 수 없어요." }, { status: 400 });
    }
    if (!scriptOf(draft.data)?.scenes?.length) {
      return NextResponse.json({ error: "먼저 대본을 기획해주세요." }, { status: 400 });
    }

    await updateDraft(id, {
      data: {
        ...(draft.data ?? {}),
        clip_job: { status: "requested", error: "" },
      },
    });
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}

// PATCH /api/drafts/[id]/clips → 세 가지 바디를 받는다.
//   1) { n, motion_selected?, caption?, motion_prompt_override? } — 장면 하나의 필드를 즉시 갱신
//   2) { render: { font?, text_color?, box_style? } } — 렌더 옵션(자막 타이포그래피) 갱신
//   3) { reorder: number[] } — 씬 순서 교체(2026-09-09). 배열은 기존 씬들의 n 값을
//      새 순서대로 나열한 것 — video_maker.render()가 scenes 배열 순서 그대로 이어
//      붙이므로(n 값 자체는 그냥 식별자, 순서에 안 쓰임) 여기서 배열만 재정렬하면
//      충분하다. hook 자막은 항상 "배열의 첫 번째" 씬에 얹히므로, 순서를 바꾸면
//      자연스럽게 새 1번 씬이 훅을 받는다.
// 셋 다 잡 큐를 거치지 않고 DB만 바로 갱신한다 — 다음 렌더(②영상 만들기/다시 만들기)
// 때 반영된다.
export async function PATCH(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const body = await req.json().catch(() => ({}));
    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });

    if (body.render && typeof body.render === "object") {
      const prev = renderOf(draft.data);
      const next = { ...prev, ...body.render };
      await updateDraft(id, { data: { ...(draft.data ?? {}), clip_render: next } });
      return NextResponse.json({ ok: true });
    }

    if (typeof body.hook === "string") {
      const script = scriptOf(draft.data);
      if (!script) {
        return NextResponse.json({ error: "대본이 없습니다." }, { status: 400 });
      }
      await updateDraft(id, {
        data: { ...(draft.data ?? {}), clip_script: { ...script, hook: body.hook } },
      });
      return NextResponse.json({ ok: true });
    }

    if (Array.isArray(body.reorder)) {
      const script = scriptOf(draft.data);
      if (!script?.scenes?.length) {
        return NextResponse.json({ error: "대본이 없습니다." }, { status: 400 });
      }
      const order = body.reorder.filter((n: unknown) => Number.isInteger(n)) as number[];
      const byN = new Map(script.scenes.map((sc) => [sc.n, sc]));
      const currentNs = script.scenes.map((sc) => sc.n);
      const sameSet =
        order.length === currentNs.length &&
        currentNs.every((n) => n !== undefined && order.includes(n));
      if (!sameSet) {
        return NextResponse.json({ error: "순서 배열이 기존 씬 구성과 다릅니다." }, { status: 400 });
      }
      const scenes = order.map((n) => byN.get(n)!);
      await updateDraft(id, {
        data: { ...(draft.data ?? {}), clip_script: { ...script, scenes } },
      });
      return NextResponse.json({ ok: true });
    }

    const n = Number.isInteger(body.n) ? Number(body.n) : undefined;
    if (n === undefined) {
      return NextResponse.json({ error: "n이 필요합니다." }, { status: 400 });
    }
    const script = scriptOf(draft.data);
    if (!script?.scenes?.length) {
      return NextResponse.json({ error: "대본이 없습니다." }, { status: 400 });
    }
    const scenes = script.scenes.map((sc) => {
      if (sc.n !== n) return sc;
      const patched = { ...sc };
      if (typeof body.motion_selected === "boolean") patched.motion_selected = body.motion_selected;
      if (typeof body.caption === "string") patched.caption = body.caption;
      if (typeof body.motion_prompt_override === "string") {
        patched.motion_prompt_override = body.motion_prompt_override;
      }
      return patched;
    });
    await updateDraft(id, {
      data: { ...(draft.data ?? {}), clip_script: { ...script, scenes } },
    });
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
