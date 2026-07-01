import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { insertDraft, listDrafts, createSignedUpload } from "@/lib/db";

export const runtime = "nodejs";

// GET /api/drafts  → 내 글 목록(최신순)
export async function GET() {
  try {
    const drafts = await listDrafts();
    return NextResponse.json({ drafts });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}

// POST /api/drafts  (JSON) → 글감 메타 저장 + 사진 서명 업로드 URL 발급.
// 사진 바이트는 함수를 거치지 않고 브라우저가 URL 로 직접 Supabase 에 올린다(4.5MB 제한 우회).
// 사진이 있으면 status='uploading' 로 두고, 업로드 완료 후 폰이 PATCH 로 'generating' 전환.
export async function POST(req: NextRequest) {
  try {
    const b = await req.json();
    const str = (k: string) => (typeof b[k] === "string" ? b[k].trim() : "");

    let required_links: string[] = [];
    if (Array.isArray(b.required_links)) {
      required_links = b.required_links.map((l: string) => String(l).trim()).filter(Boolean);
    } else if (typeof b.required_links === "string") {
      required_links = b.required_links.split("\n").map((l: string) => l.trim()).filter(Boolean);
    }

    const request = {
      structure_key: str("structure_key") || "free",
      keyword: str("keyword"),
      product_link: str("product_link"),
      required_links,
      sponsor_type: str("sponsor_type") || "내돈내산",
      memo: str("memo"),
      length: parseInt(String(b.length ?? "1500"), 10) || 1500,
      photo_style: str("photo_style") || "감성 중심",
      optional_fields:
        b.optional_fields && typeof b.optional_fields === "object" ? b.optional_fields : {},
      blog_id: str("blog_id") || "bbnation",
      video_count: 0,
      video_desc: "",
    };

    const photoCount = Math.max(0, Math.min(40, Number(b.photoCount) || 0));
    const draft_id = randomUUID();

    const uploads: { path: string; url: string }[] = [];
    const imagePaths: string[] = [];
    for (let i = 1; i <= photoCount; i++) {
      const path = `${draft_id}/${i}.jpg`;
      uploads.push({ path, url: await createSignedUpload(path) });
      imagePaths.push(path);
    }

    await insertDraft({
      id: draft_id,
      status: photoCount > 0 ? "uploading" : "generating",
      title: "",
      body: "",
      data: { request },
      images: imagePaths,
      thumbnail: null,
    });

    return NextResponse.json({ id: draft_id, uploads });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
