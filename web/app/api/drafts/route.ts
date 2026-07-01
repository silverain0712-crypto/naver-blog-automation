import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { insertDraft, listDrafts, uploadImage } from "@/lib/db";

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

// POST /api/drafts  (multipart) → 글감 제출, status='generating' 로 생성 → 맥 생성기가 처리
export async function POST(req: NextRequest) {
  try {
    const form = await req.formData();
    const s = (k: string) => (form.get(k) as string | null)?.trim() ?? "";

    let optional_fields: Record<string, string> = {};
    try {
      optional_fields = JSON.parse(s("optional_fields") || "{}");
    } catch {
      optional_fields = {};
    }

    const required_links = s("required_links")
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);

    const request = {
      structure_key: s("structure_key") || "free",
      keyword: s("keyword"),
      product_link: s("product_link"),
      required_links,
      sponsor_type: s("sponsor_type") || "내돈내산",
      memo: s("memo"),
      length: parseInt(s("length") || "1500", 10),
      photo_style: s("photo_style") || "감성 중심",
      optional_fields,
      blog_id: s("blog_id") || "bbnation",
      video_count: 0,
      video_desc: "",
    };

    const draft_id = randomUUID();

    const files = form.getAll("photos").filter((f): f is File => f instanceof File && f.size > 0);
    const imagePaths: string[] = [];
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      const buf = await f.arrayBuffer();
      const path = `${draft_id}/${i + 1}.jpg`;
      await uploadImage(path, buf, f.type || "image/jpeg");
      imagePaths.push(path);
    }

    await insertDraft({
      id: draft_id,
      status: "generating",
      title: "",
      body: "",
      data: { request },
      images: imagePaths,
      thumbnail: null,
    });

    return NextResponse.json({ id: draft_id });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
