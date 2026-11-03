import { NextRequest, NextResponse } from "next/server";
import { getDraft, updateDraft } from "@/lib/db";

export const runtime = "nodejs";

// POST /api/drafts/[id]/clips/plan → 클립 대본(clip_script)만 (다시) 기획하도록
// 맥에 신호를 보낸다. 이미 대본이 있어도 덮어쓴다("대본 새로 기획" 버튼).
// 바디 { source_photos?: number[], feedback?: string }
//   - source_photos: 이 글의 사진 중 클립 후보로 쓸 번호만 골랐으면 data.clip_source_photos
//     에 저장(모듈 shortform.make_script 의 allowed_photos 로 전달됨). 안 보내면
//     기존처럼 본문의 모든 [사진N]이 후보가 된다.
//   - feedback: 클립 전체에 대한 수정 요청(2026-09-09). 문자열이면(빈 문자열 포함)
//     data.clip_feedback 에 저장 — 안 보내면 기존 값 유지, 빈 문자열을 보내면
//     지운 것으로 간주해 다음 기획부터 반영되지 않는다.
export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const body = await req.json().catch(() => ({}));
    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });

    const sourcePhotos = Array.isArray(body.source_photos)
      ? body.source_photos.filter((n: unknown) => Number.isInteger(n))
      : undefined;
    const feedback = typeof body.feedback === "string" ? body.feedback : undefined;

    await updateDraft(id, {
      data: {
        ...(draft.data ?? {}),
        ...(sourcePhotos ? { clip_source_photos: sourcePhotos } : {}),
        ...(feedback !== undefined ? { clip_feedback: feedback } : {}),
        clip_script_job: { status: "requested", error: "" },
      },
    });
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
