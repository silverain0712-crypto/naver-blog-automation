// Supabase 서버 전용 접근 (service_role 키 — 절대 브라우저에 노출 금지).
// 모든 DB/Storage 호출은 Next.js 서버(Route Handler)에서만 이뤄진다.
import { createClient } from "@supabase/supabase-js";

const SUPABASE_URL = process.env.SUPABASE_URL ?? "";
const SUPABASE_KEY = process.env.SUPABASE_KEY ?? "";
export const SUPABASE_BUCKET = process.env.SUPABASE_BUCKET ?? "media";

if (!SUPABASE_URL || !SUPABASE_KEY) {
  // 빌드 타임엔 경고만; 런타임 호출 시 에러
  console.warn("[db] SUPABASE_URL / SUPABASE_KEY 미설정");
}

export const supabase = createClient(SUPABASE_URL, SUPABASE_KEY, {
  auth: { persistSession: false },
});

export type Draft = {
  id: string;
  created_at: string;
  status: string;
  title: string | null;
  body: string | null;
  data: Record<string, unknown> | null;
  images: string[] | null;
  thumbnail: string | null;
};

// modules/store.py save_draft 와 동일한 레코드 형태로 1건 생성
export async function insertDraft(record: {
  id: string;
  status: string;
  title: string;
  body: string;
  data: Record<string, unknown>;
  images: string[];
  thumbnail: string | null;
}): Promise<void> {
  const { error } = await supabase.from("drafts").insert(record);
  if (error) throw new Error(`insertDraft: ${error.message}`);
}

export async function listDrafts(status?: string): Promise<Draft[]> {
  let q = supabase.from("drafts").select("*").order("created_at", { ascending: false });
  if (status) q = q.eq("status", status);
  const { data, error } = await q;
  if (error) throw new Error(`listDrafts: ${error.message}`);
  return (data ?? []) as Draft[];
}

export async function getDraft(id: string): Promise<Draft | null> {
  const { data, error } = await supabase.from("drafts").select("*").eq("id", id).maybeSingle();
  if (error) throw new Error(`getDraft: ${error.message}`);
  return (data as Draft) ?? null;
}

export async function updateDraft(id: string, fields: Partial<Draft>): Promise<void> {
  const { error } = await supabase.from("drafts").update(fields).eq("id", id);
  if (error) throw new Error(`updateDraft: ${error.message}`);
}

export async function deleteDraft(id: string): Promise<void> {
  const { error } = await supabase.from("drafts").delete().eq("id", id);
  if (error) throw new Error(`deleteDraft: ${error.message}`);
}

// 사진 1장 업로드 → 저장 경로 반환 (store.py _upload 와 동일 규칙: {id}/{n}.jpg)
export async function uploadImage(path: string, bytes: ArrayBuffer, contentType: string): Promise<string> {
  const { error } = await supabase.storage
    .from(SUPABASE_BUCKET)
    .upload(path, bytes, { contentType, upsert: true });
  if (error) throw new Error(`uploadImage(${path}): ${error.message}`);
  return path;
}
