import type { ReviewResponse } from "./types";

async function decode(response: Response): Promise<ReviewResponse & { sessionId?: string }> {
  const body = await response.json();
  if (!response.ok) throw new Error(body.message || "review request failed");
  return body;
}

export function submitReview(text: string) {
  return fetch("/api/reviews", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
  }).then(decode);
}

export function resumeReview(sessionId: string, value: unknown) {
  return fetch(`/api/reviews/${encodeURIComponent(sessionId)}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ value }),
  }).then(decode);
}
