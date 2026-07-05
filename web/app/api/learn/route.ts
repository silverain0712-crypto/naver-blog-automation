import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { insertDraft } from "@/lib/db";

export const runtime = "nodejs";

// POST /api/learn → 맥에 '발행 글 학습' 신호를 남긴다(status='learn').
// 맥 mac_generator 가 폴링해 style_sync(발행 글 수집) 실행 후
// status='learn_done' + data.result({added,skipped,...}) 로 갱신한다.
// 폰은 반환된 id 로 /api/drafts/[id] 를 폴링해 결과를 보여준다.
export async function POST(req: NextRequest) {
  try {
    const b = await req.json().catch(() => ({}));
    const blog_id =
      typeof b?.blog_id === "string" && b.blog_id.trim() ? b.blog_id.trim() : "bbnation";
    const id = randomUUID();
    await insertDraft({
      id,
      status: "learn",
      title: "",
      body: "",
      data: { request: { blog_id } },
      images: [],
      thumbnail: null,
    });
    return NextResponse.json({ id });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
