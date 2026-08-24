import { startReview } from "../../../lib/review-worker";

export const runtime = "nodejs";

export async function POST(request: Request) {
  try {
    const body = await request.json();
    if (typeof body.text !== "string" || !body.text.trim()) {
      return Response.json({ message: "판단 내용을 입력해주세요." }, { status: 400 });
    }
    return Response.json(await startReview(body.text.trim()));
  } catch {
    return Response.json(
      { kind: "error", code: "REVIEW_FAILED", message: "검토를 시작하지 못했습니다." },
      { status: 500 },
    );
  }
}
