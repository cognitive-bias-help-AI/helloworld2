import { parseStartReviewBody, type ReviewIntake } from "../../../lib/intake.ts";
import { resumeReview, startReview } from "../../../lib/review-worker.ts";

export const runtime = "nodejs";

export function createReviewPostHandler(
  beginReview: (intake: ReviewIntake) => Promise<unknown>,
  continueReview: (sessionId: string, value: unknown) => Promise<unknown> = resumeReview,
) {
  return async function POST(request: Request) {
    let body: unknown;
    try {
      body = await request.json();
    } catch {
      return Response.json({ message: "입력 내용을 다시 확인해주세요." }, { status: 400 });
    }

    if (typeof body === "object" && body !== null && !Array.isArray(body) && "sessionId" in body) {
      const resume = body as Record<string, unknown>;
      if (
        Object.keys(resume).length !== 2 ||
        typeof resume.sessionId !== "string" ||
        !resume.sessionId.trim() ||
        !("value" in resume) ||
        typeof resume.value !== "object" ||
        resume.value === null ||
        Array.isArray(resume.value)
      ) {
        return Response.json({ message: "재개 요청을 다시 확인해주세요." }, { status: 400 });
      }
      try {
        if (process.env.REVIEW_DEBUG_LOGS === "1") {
          console.error(`[api] RESUME session=${resume.sessionId}`);
        }
        return Response.json(await continueReview(resume.sessionId, resume.value));
      } catch {
        return Response.json(
          { kind: "error", code: "REVIEW_FAILED", message: "검토를 계속하지 못했습니다." },
          { status: 500 },
        );
      }
    }

    let intake;
    try {
      intake = parseStartReviewBody(body);
    } catch {
      return Response.json({ message: "입력 내용을 다시 확인해주세요." }, { status: 400 });
    }
    try {
      return Response.json(await beginReview(intake));
    } catch {
      return Response.json(
        { kind: "error", code: "REVIEW_FAILED", message: "검토를 시작하지 못했습니다." },
        { status: 500 },
      );
    }
  };
}

export const POST = createReviewPostHandler(startReview);
