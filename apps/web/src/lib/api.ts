import { createApiClient } from "@anvesha/api-client";

/** Calls go to this app's /api/v1 routes, which forward them to the Anvesha API. */
export const api = createApiClient();

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type ValidationItem = { loc?: (string | number)[]; msg?: string };

/** The API's error body ({detail, code}, or FastAPI's validation list) as a sentence. */
export function errorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      return (detail as ValidationItem[])
        .slice(0, 3)
        .map((item) => {
          const field = (item.loc ?? []).filter((part) => part !== "body").join(".");
          return field ? `${field}: ${item.msg ?? "invalid"}` : (item.msg ?? "Invalid input");
        })
        .join("; ");
    }
  }
  return fallback;
}

type Result<T> = { data?: T; error?: unknown; response: Response };

/** The response data, or an ApiError with the server's explanation. */
export async function unwrap<T>(request: Promise<Result<T>>): Promise<T> {
  let result: Result<T>;
  try {
    result = await request;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Can't reach the server. Check your connection and try again.", 0);
  }
  const { data, error, response } = result;
  if (!response.ok) {
    const code =
      error && typeof error === "object" && "code" in error
        ? String((error as { code: unknown }).code)
        : null;
    throw new ApiError(
      errorMessage(error, `The request failed (${response.status}).`),
      response.status,
      code,
    );
  }
  return data as T;
}

export function describeError(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong.";
}

/** Upload a file as a project source (multipart, so it bypasses the JSON client). */
export async function uploadSource(projectId: string, file: File): Promise<void> {
  const body = new FormData();
  body.append("file", file);
  let response: Response;
  try {
    response = await fetch(`/api/v1/projects/${projectId}/sources/upload`, {
      method: "POST",
      body,
    });
  } catch {
    throw new ApiError("Can't reach the server. Check your connection and try again.", 0);
  }
  if (!response.ok) {
    const error: unknown = await response.json().catch(() => null);
    const code =
      error && typeof error === "object" && "code" in error ? String(error.code) : null;
    throw new ApiError(
      errorMessage(error, `The upload failed (${response.status}).`),
      response.status,
      code,
    );
  }
}
