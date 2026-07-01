"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  POST_TYPES,
  SPONSOR_TYPES,
  POST_LENGTHS,
  PHOTO_STYLES,
  OPTIONAL_FIELDS,
} from "@/lib/constants";

const field = "rounded-lg border border-neutral-300 px-3 py-2 text-base w-full bg-white";
const labelC = "text-sm font-medium text-neutral-700";

export default function NewPostPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [showOptional, setShowOptional] = useState(false);
  const [photoCount, setPhotoCount] = useState(0);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setErr("");
    const formEl = e.currentTarget;
    const fd = new FormData(formEl);

    // 선택 입력들을 optional_fields JSON 으로 묶기
    const optional: Record<string, string> = {};
    for (const { key } of OPTIONAL_FIELDS) {
      const v = (fd.get(`opt_${key}`) as string | null)?.trim();
      if (v) optional[key] = v;
      fd.delete(`opt_${key}`);
    }
    fd.set("optional_fields", JSON.stringify(optional));

    if (!(fd.get("memo") as string)?.trim() && photoCount === 0) {
      setErr("메모나 사진 중 하나는 넣어주세요.");
      return;
    }

    setBusy(true);
    const res = await fetch("/api/drafts", { method: "POST", body: fd });
    setBusy(false);
    if (res.ok) {
      const { id } = await res.json();
      router.push(`/edit/${id}`);
    } else {
      const j = await res.json().catch(() => ({}));
      setErr(j.error ?? "생성 요청 실패");
    }
  }

  return (
    <main className="mx-auto max-w-lg p-4 pb-24">
      <header className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">새 글 쓰기 ✍️</h1>
        <Link href="/list" className="text-sm text-blue-600">
          내 글 →
        </Link>
      </header>

      <form onSubmit={submit} className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1">
            <span className={labelC}>글 유형</span>
            <select name="structure_key" className={field} defaultValue="free">
              {POST_TYPES.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className={labelC}>협찬</span>
            <select name="sponsor_type" className={field} defaultValue="내돈내산">
              {SPONSOR_TYPES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className={labelC}>글 길이</span>
            <select name="length" className={field} defaultValue="1500">
              {POST_LENGTHS.map((n) => (
                <option key={n} value={n}>
                  {n}자
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className={labelC}>사진 노출</span>
            <select name="photo_style" className={field} defaultValue="감성 중심">
              {PHOTO_STYLES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
        </div>

        <label className="flex flex-col gap-1">
          <span className={labelC}>핵심 키워드 (비우면 AI가 제안)</span>
          <input name="keyword" className={field} placeholder="예: 신당동 키즈카페" />
        </label>

        <label className="flex flex-col gap-1">
          <span className={labelC}>메모 — 최우선 반영</span>
          <textarea
            name="memo"
            className={`${field} min-h-28`}
            placeholder="오늘 있었던 일, 느낌, 꼭 담고 싶은 내용을 편하게 적어주세요."
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className={labelC}>상품/필수 링크 (선택)</span>
          <input name="product_link" className={field} placeholder="상품 링크" />
          <textarea
            name="required_links"
            className={`${field} min-h-16 mt-1`}
            placeholder="본문에 꼭 넣을 링크 (여러 개는 줄바꿈)"
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className={labelC}>사진 {photoCount > 0 && `(${photoCount}장)`}</span>
          <input
            name="photos"
            type="file"
            accept="image/*"
            multiple
            className="text-sm"
            onChange={(e) => setPhotoCount(e.target.files?.length ?? 0)}
          />
        </label>

        <button
          type="button"
          onClick={() => setShowOptional((v) => !v)}
          className="text-left text-sm text-blue-600"
        >
          {showOptional ? "▾ 선택 입력 접기" : "▸ 선택 입력 (장소·제품·가격 등)"}
        </button>
        {showOptional && (
          <div className="grid grid-cols-2 gap-3 rounded-lg bg-neutral-50 p-3">
            {OPTIONAL_FIELDS.map((f) => (
              <label key={f.key} className="flex flex-col gap-1">
                <span className="text-xs text-neutral-500">{f.label}</span>
                <input name={`opt_${f.key}`} className={`${field} text-sm`} />
              </label>
            ))}
          </div>
        )}

        {err && <p className="text-sm text-red-600">{err}</p>}

        <button
          type="submit"
          disabled={busy}
          className="fixed inset-x-0 bottom-0 mx-auto max-w-lg bg-neutral-900 px-4 py-4 text-base font-semibold text-white disabled:opacity-50 sm:static sm:rounded-lg"
        >
          {busy ? "요청 중…" : "초안 생성 🚀"}
        </button>
      </form>
    </main>
  );
}
