import { NextRequest, NextResponse } from "next/server";
import { getDraft, updateDraft } from "@/lib/db";

export const runtime = "nodejs";

// POST /api/drafts/[id]/retry → 실패한 글을 다시 시도한다.
// 어느 단계에서 실패했는지에 따라 되돌릴 자리가 다르다.
//   generate  초안을 쓰다 실패 → 'generating' 으로 되돌려 맥이 다시 쓴다
//   post      네이버 저장에서 실패 → 'queued' 로 되돌려 워커가 다시 저장한다
export async function POST(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });
    if (draft.status !== "error") {
      return NextResponse.json({ error: "실패한 글만 다시 시도할 수 있어요." }, { status: 400 });
    }

    const data = (draft.data ?? {}) as Record<string, unknown>;
    // 예전 글에는 error_stage 가 없다 — 본문이 있으면 저장 단계에서 실패한 것으로 본다.
    const stage = typeof data.error_stage === "string"
      ? data.error_stage
      : (draft.body ?? "").trim()
        ? "post"
        : "generate";

    const next = { ...data };
    delete next.error;
    delete next.error_stage;

    await updateDraft(id, { status: stage === "generate" ? "generating" : "queued", data: next });
    return NextResponse.json({ ok: true, status: stage === "generate" ? "generating" : "queued" });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
