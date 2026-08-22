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

// 초안 폴더({id}/) 안의 모든 객체를 스토리지에서 제거하고 지운 개수를 돌려준다.
// DB 행만 지우면 사진이 버킷에 영구 누적돼 무료 한도(1GB)를 넘긴다.
export async function removeDraftFiles(id: string): Promise<number> {
  const paths: string[] = [];
  const page = 100;
  for (let offset = 0; ; offset += page) {
    const { data, error } = await supabase.storage
      .from(SUPABASE_BUCKET)
      .list(id, { limit: page, offset });
    if (error) throw new Error(`removeDraftFiles(list ${id}): ${error.message}`);
    if (!data || data.length === 0) break;
    for (const it of data) paths.push(`${id}/${it.name}`);
    if (data.length < page) break;
  }
  if (paths.length === 0) return 0;
  const { error } = await supabase.storage.from(SUPABASE_BUCKET).remove(paths);
  if (error) throw new Error(`removeDraftFiles(remove ${id}): ${error.message}`);
  return paths.length;
}

export async function deleteDraft(id: string): Promise<void> {
  // 사진을 먼저 정리한다. 실패해도 DB 행 삭제는 진행 — 남은 고아 파일은
  // scripts/purge_old_media.py 가 회수한다.
  try {
    await removeDraftFiles(id);
  } catch (e) {
    console.warn(`[db] deleteDraft: 스토리지 정리 실패 ${id}`, e);
  }
  const { error } = await supabase.from("drafts").delete().eq("id", id);
  if (error) throw new Error(`deleteDraft: ${error.message}`);
}

// 브라우저가 Vercel 함수(4.5MB 제한)를 거치지 않고 Supabase 로 직접 PUT 하도록
// 서명 업로드 URL 생성 → 절대 URL 반환. (사진 장수 제한 없이 안정적)
export async function createSignedUpload(path: string, upsert = false): Promise<string> {
  const { data, error } = await supabase.storage
    .from(SUPABASE_BUCKET)
    .createSignedUploadUrl(path, upsert ? { upsert: true } : undefined);
  if (error || !data) throw new Error(`createSignedUpload(${path}): ${error?.message}`);
  let url = data.signedUrl;
  if (url.startsWith("/")) url = `${SUPABASE_URL}${url}`;
  return url;
}

// 보관 사진 미리보기용 — 저장된 이미지 경로들의 서명 다운로드 URL(절대 URL) 발급.
export async function createSignedDownloads(
  paths: string[],
  expiresIn = 60 * 60,
): Promise<string[]> {
  if (paths.length === 0) return [];
  const { data, error } = await supabase.storage
    .from(SUPABASE_BUCKET)
    .createSignedUrls(paths, expiresIn);
  if (error || !data) throw new Error(`createSignedDownloads: ${error?.message}`);
  return data.map((d) => {
    const url = d.signedUrl ?? "";
    return url.startsWith("/") ? `${SUPABASE_URL}${url}` : url;
  });
}
