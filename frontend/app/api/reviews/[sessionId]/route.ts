import { resumeReview } from "../../../../lib/review-worker";

export const runtime = "nodejs";

export async function POST(request: Request, context: { params: Promise<{ sessionId: string }> }) {
  try {
    const { sessionId } = await context.params;
    const body = await request.json();
    if (!("value" in body)) return Response.json({ message: "재개 값이 필요합니다." }, { status: 400 });
    return Response.json(await resumeReview(sessionId, body.value));
  } catch {
    return Response.json(
      { kind: "error", code: "REVIEW_FAILED", message: "검토를 계속하지 못했습니다." },
      { status: 500 },
    );
  }
}
