import { NextRequest, NextResponse } from "next/server";
import { getDraft, createSignedDownloads } from "@/lib/db";

export const runtime = "nodejs";

// GET /api/drafts/[id]/images  → 보관 사진 미리보기용 서명 다운로드 URL 목록
export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });
    const urls = await createSignedDownloads(draft.images ?? []);
    return NextResponse.json({ urls });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
