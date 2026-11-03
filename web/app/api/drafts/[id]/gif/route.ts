import { NextRequest, NextResponse } from "next/server";
import { getDraft, updateDraft } from "@/lib/db";

export const runtime = "nodejs";

type VideoMeta = { path?: string; start?: number; end?: number };

// GET /api/drafts/[id]/gif → 진행 중인 동영상→GIF 변환 작업 상태(폴링용).
export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });
    const job = (draft.data as { gif_job?: unknown } | null)?.gif_job ?? null;
    return NextResponse.json({ job });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}

// POST /api/drafts/[id]/gif → { video_index } 로 해당 영상을 GIF로 변환하도록
// 맥(mac_clip_generator.py, modules/gif_extractor.py)에 신호를 보낸다. 트림 구간은
// 이미 PATCH /videos 로 저장된 값을 그대로 쓴다.
export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const body = await req.json().catch(() => ({}));
    const videoIndex = Number.isInteger(body.video_index) ? Number(body.video_index) : undefined;
    if (videoIndex === undefined) {
      return NextResponse.json({ error: "video_index가 필요합니다." }, { status: 400 });
    }

    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });
    const videos = ((draft.data as { videos?: unknown } | null)?.videos ?? []) as VideoMeta[];
    const video = videos[videoIndex];
    if (!video?.path) {
      return NextResponse.json({ error: "해당 영상을 찾을 수 없습니다." }, { status: 400 });
    }

    await updateDraft(id, {
      data: {
        ...(draft.data ?? {}),
        // width/fps 는 안 보낸다 — gif_extractor.py 가 10MB 제한 안에 들어올 때까지
        // 화질을 알아서 단계적으로 낮춘다(modules/gif_extractor.py::_convert_under_limit).
        gif_job: {
          status: "requested",
          video_index: videoIndex,
          start: video.start ?? 0,
          end: video.end ?? 0,
          error: "",
        },
      },
    });
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
