import type { ReviewIntake, ReviewResponse } from "./types";

async function decode(response: Response): Promise<ReviewResponse & { sessionId?: string }> {
  const body = await response.json();
  if (!response.ok) throw new Error(body.message || "review request failed");
  return body;
}

export function submitReview(intake: ReviewIntake) {
  return fetch("/api/reviews", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ intake }),
  }).then(decode);
}

export function resumeReview(sessionId: string, value: unknown) {
  return fetch("/api/reviews", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ sessionId, value }),
  }).then(decode);
}
