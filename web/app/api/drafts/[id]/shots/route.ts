import { NextRequest, NextResponse } from "next/server";
import { getDraft, updateDraft, createSignedDownloads } from "@/lib/db";

export const runtime = "nodejs";

type Shot = { path: string; key?: string; label?: string; feedback?: string; rev?: number };

function shotsOf(data: Record<string, unknown> | null): Shot[] {
  const raw = (data as { product_shots?: unknown } | null)?.product_shots;
  return Array.isArray(raw) ? (raw as Shot[]) : [];
}

// GET /api/drafts/[id]/shots → 생성된 상세컷 + 미리보기 URL + 작업 상태
export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });
    const shots = shotsOf(draft.data);
    const urls = await createSignedDownloads(shots.map((s) => s.path));
    const job = (draft.data as { image_job?: unknown } | null)?.image_job ?? null;
    return NextResponse.json({
      job,
      shots: shots.map((s, i) => ({ ...s, url: urls[i] ?? "" })),
    });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}

// POST /api/drafts/[id]/shots → 맥에 사진 생성을 요청한다.
//   {}                         전체 생성(기본 3장)
//   { target, feedback }       그 사진 한 장만 피드백 반영해 다시 생성
export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const body = await req.json().catch(() => ({}));
    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });

    const link = String(
      ((draft.data as { request?: { product_link?: unknown } } | null)?.request?.product_link) ?? "",
    ).trim();
    if (!link) {
      return NextResponse.json({ error: "상품 링크가 없습니다. 글감에 링크를 넣어주세요." }, { status: 400 });
    }

    const target = Number.isInteger(body.target) ? Number(body.target) : undefined;
    const feedback = typeof body.feedback === "string" ? body.feedback.trim() : "";
    if (target !== undefined) {
      if (target < 0 || target >= shotsOf(draft.data).length) {
        return NextResponse.json({ error: "없는 사진입니다." }, { status: 400 });
      }
      if (!feedback) {
        return NextResponse.json({ error: "고치고 싶은 점을 적어주세요." }, { status: 400 });
      }
    }

    const count = Math.max(1, Math.min(4, Number(body.count) || 3));
    await updateDraft(id, {
      data: {
        ...(draft.data ?? {}),
        image_job: { status: "requested", count, target, feedback, error: "" },
      },
    });
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
