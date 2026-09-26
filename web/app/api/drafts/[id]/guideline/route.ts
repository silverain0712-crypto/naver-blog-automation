import { NextRequest, NextResponse } from "next/server";
import { getDraft, createSignedDownloads } from "@/lib/db";

export const runtime = "nodejs";

// GET /api/drafts/[id]/guideline → 첨부한 협찬 가이드/제품 설명서 파일이 실제로
// Supabase Storage 에 저장됐는지 확인용 목록.
//
// 2026-09-25: 가이드라인을 첨부했는데 실제로는 업로드가 안 됐던 사례(베르블랑
// 아기세제 글) 이후 추가 — 파일명이 메타에 있어도 스토리지에 실물이 없으면
// createSignedUrls 가 그 항목만 signedUrl 없이 돌려주므로(전체 에러 아님),
// ok:false 로 "실제로 없다"는 걸 구분해서 보여준다.
export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });

    const data = (draft.data ?? {}) as Record<string, unknown>;
    const req = (data.request ?? {}) as Record<string, unknown>;
    const names = Array.isArray(req.guideline_names) ? (req.guideline_names as string[]) : [];
    const paths = Array.isArray(req.guideline_paths) ? (req.guideline_paths as string[]) : [];
    const text = typeof req.guideline_text === "string" ? req.guideline_text : "";

    const urls = await createSignedDownloads(paths);
    const files = paths.map((path, i) => ({
      name: names[i] ?? path.split("/").pop() ?? path,
      path,
      url: urls[i] || "",
      ok: Boolean(urls[i]), // 서명 URL이 안 나오면 실제 파일이 스토리지에 없다는 뜻
    }));

    return NextResponse.json({ files, text });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
