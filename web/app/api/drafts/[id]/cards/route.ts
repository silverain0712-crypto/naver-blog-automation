import { NextRequest, NextResponse } from "next/server";
import { getDraft, updateDraft, createSignedDownloads } from "@/lib/db";

export const runtime = "nodejs";

type CardSpecEntry = {
  photo_number: number;
  spec?: { role?: string };
  feedback?: string;
  rev?: number;
};
type Placement = { photo_number: number; section?: string; caption?: string };

function specsOf(data: Record<string, unknown> | null): CardSpecEntry[] {
  const raw = (data as { card_specs?: unknown } | null)?.card_specs;
  return Array.isArray(raw) ? (raw as CardSpecEntry[]) : [];
}
function placementOf(data: Record<string, unknown> | null): Placement[] {
  const raw = (data as { photo_placement?: unknown } | null)?.photo_placement;
  return Array.isArray(raw) ? (raw as Placement[]) : [];
}

// GET /api/drafts/[id]/cards → 자동 생성된 카드뉴스 목록(미리보기 URL 포함) + 재생성 작업 상태.
// 정보성/화제성 글이 아니거나 아직 자동 삽화가 없으면 cards: [] 를 돌려준다.
export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });

    const specs = specsOf(draft.data);
    const placement = placementOf(draft.data);
    const images = draft.images ?? [];
    const byNum = new Map(placement.map((p) => [p.photo_number, p]));

    const paths = specs.map((s) => images[s.photo_number - 1] ?? "");
    const urls = await createSignedDownloads(paths);

    const cards = specs
      .map((s, i) => ({
        photo_number: s.photo_number,
        role: s.spec?.role ?? "card",
        section: byNum.get(s.photo_number)?.section ?? "",
        caption: byNum.get(s.photo_number)?.caption ?? "",
        feedback: s.feedback ?? "",
        rev: s.rev ?? 0,
        url: urls[i] ?? "",
      }))
      .sort((a, b) => a.photo_number - b.photo_number);

    const job = (draft.data as { card_job?: unknown } | null)?.card_job ?? null;
    return NextResponse.json({ job, cards });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}

// POST /api/drafts/[id]/cards → { target: photo_number, feedback } 로 그 카드 한 장만
// 자동 생성 당시 내용은 그대로 두고 피드백만 반영해 다시 만들도록 맥에 신호를 보낸다.
export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const body = await req.json().catch(() => ({}));
    const draft = await getDraft(id);
    if (!draft) return NextResponse.json({ error: "not found" }, { status: 404 });

    const target = Number.isInteger(body.target) ? Number(body.target) : undefined;
    const feedback = typeof body.feedback === "string" ? body.feedback.trim() : "";
    if (target === undefined) {
      return NextResponse.json({ error: "target이 필요합니다." }, { status: 400 });
    }
    if (!feedback) {
      return NextResponse.json({ error: "고치고 싶은 점을 적어주세요." }, { status: 400 });
    }
    const specs = specsOf(draft.data);
    if (!specs.some((s) => s.photo_number === target)) {
      return NextResponse.json({ error: "자동 생성된 카드가 아닙니다." }, { status: 400 });
    }
    await updateDraft(id, {
      data: {
        ...(draft.data ?? {}),
        card_job: { status: "requested", target, feedback, error: "" },
      },
    });
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
