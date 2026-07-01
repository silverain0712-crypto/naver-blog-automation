"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { STATUS_LABEL } from "@/lib/constants";

type Draft = {
  id: string;
  created_at: string;
  status: string;
  title: string | null;
  images: string[] | null;
  data: Record<string, any> | null;
};

const badgeColor: Record<string, string> = {
  generating: "bg-blue-100 text-blue-700",
  draft_ready: "bg-green-100 text-green-700",
  queued: "bg-amber-100 text-amber-700",
  posting: "bg-amber-100 text-amber-700",
  posted: "bg-neutral-200 text-neutral-700",
  error: "bg-red-100 text-red-700",
};

export default function ListPage() {
  const [drafts, setDrafts] = useState<Draft[] | null>(null);

  useEffect(() => {
    let stop = false;
    async function load() {
      const res = await fetch("/api/drafts", { cache: "no-store" });
      if (res.ok && !stop) {
        const { drafts } = await res.json();
        setDrafts(drafts);
      }
    }
    load();
    const t = setInterval(load, 5000); // 상태 진행 반영
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, []);

  return (
    <main className="mx-auto max-w-lg p-4">
      <header className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">내 글</h1>
        <Link
          href="/"
          className="rounded-lg bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white"
        >
          + 새 글
        </Link>
      </header>

      {drafts === null && <p className="text-neutral-500">불러오는 중…</p>}
      {drafts?.length === 0 && <p className="text-neutral-500">아직 글이 없어요.</p>}

      <ul className="flex flex-col gap-2">
        {drafts?.map((d) => (
          <li key={d.id}>
            <Link
              href={`/edit/${d.id}`}
              className="flex items-center justify-between rounded-lg border border-neutral-200 p-3"
            >
              <div className="min-w-0">
                <p className="truncate font-medium">
                  {d.title || "(제목 없음)"}
                </p>
                <p className="text-xs text-neutral-400">
                  {new Date(d.created_at).toLocaleString("ko-KR", {
                    month: "numeric",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                  {d.images?.length ? ` · 사진 ${d.images.length}` : ""}
                </p>
                {d.status === "error" && d.data?.error && (
                  <p className="mt-0.5 truncate text-xs text-red-500">{d.data.error}</p>
                )}
              </div>
              <span
                className={`ml-2 shrink-0 rounded-full px-2 py-1 text-xs ${
                  badgeColor[d.status] ?? "bg-neutral-100 text-neutral-600"
                }`}
              >
                {STATUS_LABEL[d.status] ?? d.status}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
