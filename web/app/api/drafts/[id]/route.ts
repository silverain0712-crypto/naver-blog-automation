import { NextRequest, NextResponse } from "next/server";
import { getDraft, updateDraft, deleteDraft, type Draft } from "@/lib/db";

export const runtime = "nodejs";

// GET /api/drafts/[id]  → 폴링용 단건 조회
export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });
    return NextResponse.json({ draft });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}

// PATCH /api/drafts/[id]  → 편집 저장 / 상태 변경(queued 로 맥 자동저장 요청)
export async function PATCH(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const body = (await req.json()) as Partial<Draft>;
    const allowed: Partial<Draft> = {};
    if (body.title !== undefined) allowed.title = body.title;
    if (body.body !== undefined) allowed.body = body.body;
    if (body.status !== undefined) allowed.status = body.status;
    if (body.data !== undefined) allowed.data = body.data;
    await updateDraft(id, allowed);
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}

// DELETE /api/drafts/[id]
export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    await deleteDraft(id);
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
