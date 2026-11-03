import { NextRequest, NextResponse } from "next/server";
import { getDraft, updateDraft } from "@/lib/db";

export const runtime = "nodejs";

// PATCH /api/drafts/[id]/videos → { videos: [{path, name, size, start, end}] }
// 클라이언트가 영상 파일을 서명 URL로 직접 PUT 업로드한 뒤, 그 메타(트림 구간 포함)를
// 확정 저장한다(POST /api/drafts 시점엔 트림값을 아직 몰라서 별도 단계로 뺐다).
export async function PATCH(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const body = await req.json().catch(() => ({}));
    const videos = Array.isArray(body.videos) ? body.videos : [];

    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });

    await updateDraft(id, { data: { ...(draft.data ?? {}), videos } });
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
