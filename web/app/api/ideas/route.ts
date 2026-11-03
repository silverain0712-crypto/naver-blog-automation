import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { insertDraft, listDrafts } from "@/lib/db";

export const runtime = "nodejs";

// GET /api/ideas → 글감 메모 목록(적은 순서대로)
export async function GET() {
  try {
    const rows = await listDrafts("idea");
    const ideas = rows
      .map((r) => ({ id: r.id, text: r.title ?? "", memo: r.body ?? "", created_at: r.created_at }))
      .sort((a, b) => a.created_at.localeCompare(b.created_at));
    return NextResponse.json({ ideas });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}

// POST /api/ideas { text, memo? } → 글감 메모 추가. drafts 테이블을 status='idea' 로 재사용해
// 별도 테이블 없이 둔다("내 글" 목록에서는 GET /api/drafts 가 이 status 를 걸러낸다).
// text=짧은 글감 제목(title 컬럼), memo=새 글 화면 "메모 — 최우선 반영"에 넣을 내용(body 컬럼).
export async function POST(req: NextRequest) {
  try {
    const b = await req.json().catch(() => ({}));
    const text = typeof b.text === "string" ? b.text.trim() : "";
    const memo = typeof b.memo === "string" ? b.memo.trim() : "";
    if (!text) return NextResponse.json({ error: "내용을 입력해주세요." }, { status: 400 });

    const id = randomUUID();
    await insertDraft({
      id,
      status: "idea",
      title: text,
      body: memo,
      data: {},
      images: [],
      thumbnail: null,
    });
    return NextResponse.json({ id });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
