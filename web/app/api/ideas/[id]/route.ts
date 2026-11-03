import { NextRequest, NextResponse } from "next/server";
import { getDraft, deleteDraft } from "@/lib/db";

export const runtime = "nodejs";

// DELETE /api/ideas/[id] → 글감 메모 삭제(직접 지우거나, 그 메모로 글을 쓰기 시작했을 때).
export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const row = await getDraft(id);
    if (!row) return NextResponse.json({ ok: true });
    if (row.status !== "idea") {
      return NextResponse.json({ error: "글감 메모가 아닙니다." }, { status: 400 });
    }
    await deleteDraft(id);
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
