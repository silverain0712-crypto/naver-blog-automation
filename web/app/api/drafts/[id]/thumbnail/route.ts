import { NextRequest, NextResponse } from "next/server";
import { getDraft, createSignedUpload } from "@/lib/db";

export const runtime = "nodejs";

// POST /api/drafts/[id]/thumbnail
// 편집 화면에서 만든 썸네일 PNG 를 브라우저가 Supabase 로 직접 PUT 하도록
// 서명 업로드 URL 을 발급한다(사진 업로드와 동일한 방식, Vercel 함수 4.5MB 우회).
// 경로는 항상 `${id}/thumb.png` 로 고정 → 다시 만들면 같은 자리에 덮어쓴다(upsert).
export async function POST(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });
    const path = `${id}/thumb.png`;
    const url = await createSignedUpload(path, true); // 재생성 시 같은 자리에 덮어쓰기
    return NextResponse.json({ path, url });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
