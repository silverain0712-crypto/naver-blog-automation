// config.py 의 입력 옵션을 그대로 미러링 (POST_TYPES / SPONSOR_TYPES / POST_LENGTHS / PHOTO_STYLES)

export const POST_TYPES: { label: string; key: string }[] = [
  { label: "제품 후기", key: "product" },
  { label: "아기랑 방문 후기", key: "baby_visit" },
  { label: "맛집/카페 후기", key: "restaurant" },
  { label: "정보성 육아 글", key: "parenting_info" },
  { label: "자유 후기", key: "free" },
];

export const SPONSOR_TYPES = [
  "내돈내산",
  "제품제공",
  "원고료 제공",
  "네이버 쇼핑 커넥트 포함",
];

export const POST_LENGTHS = [1500, 2000, 2500];

export const PHOTO_STYLES = ["정보 중심", "감성 중심", "시간순", "제품 디테일 중심"];

// 선택 입력 필드 (post_generator._format_optional 의 labels 와 동일)
export const OPTIONAL_FIELDS: { key: string; label: string }[] = [
  { key: "title_hint", label: "제목 후보" },
  { key: "must_include", label: "꼭 넣고 싶은 문장" },
  { key: "place_name", label: "방문 장소명" },
  { key: "product_name", label: "제품명" },
  { key: "brand", label: "브랜드명" },
  { key: "price", label: "가격" },
  { key: "location", label: "위치" },
  { key: "parking", label: "주차 정보" },
  { key: "reservation", label: "예약 정보" },
  { key: "pros", label: "좋았던 점" },
  { key: "cons", label: "아쉬웠던 점" },
  { key: "audience", label: "추천 대상" },
];

// draft.status 흐름
//  generating   폰이 글감 제출 → 맥 생성기 대기
//  draft_ready  맥 생성 완료 → 폰에서 편집
//  queued       사용자가 저장 → 맥 워커가 네이버 임시저장
//  posting/posted/error  기존 워커 상태
export const STATUS_LABEL: Record<string, string> = {
  generating: "초안 생성 중",
  draft_ready: "초안 완성",
  queued: "저장 대기",
  posting: "네이버 저장 중",
  posted: "임시저장 완료",
  error: "실패",
  ready: "대기",
  draft: "작성 중",
};
