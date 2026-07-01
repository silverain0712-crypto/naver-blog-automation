// 단순 비밀번호 잠금 (개인용 단일 사용자). service_role 키가 뒤에 있으므로 반드시 잠금.
// 쿠키 값 == APP_PASSWORD 이면 통과. https(Vercel) 뒤 httpOnly 쿠키라 개인용엔 충분.
export const AUTH_COOKIE = "app_auth";

export function appPassword(): string {
  return (process.env.APP_PASSWORD ?? "").trim();
}

// 잠금이 설정돼 있고 쿠키가 일치하는지
export function isAuthed(cookieValue: string | undefined): boolean {
  const pw = appPassword();
  if (!pw) return true; // 비번 미설정이면 잠금 없음
  return cookieValue === pw;
}
