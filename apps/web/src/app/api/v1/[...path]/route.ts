/**
 * Forwards /api/v1/* to the Anvesha API, so the browser only ever talks to this app
 * (no CORS, and the API needn't be exposed). Bodies and responses are streamed, which
 * keeps uploads and live Server-Sent Events working.
 *
 * In multi-user mode this is where the session becomes the API's credentials: a
 * short-lived JWT for the signed-in user. The workspace the user picked (a cookie set by
 * the workspace switcher) goes along as X-Anvesha-Workspace.
 */

import type { NextRequest } from "next/server";

import { apiToken, authMode } from "@/lib/auth-server";
import { WORKSPACE_COOKIE } from "@/lib/workspace";

export const dynamic = "force-dynamic";

const API_URL = (process.env.ANVESHA_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

const REQUEST_HEADERS = ["accept", "content-type", "last-event-id"];
const RESPONSE_HEADERS = [
  "content-type",
  "cache-control",
  "content-disposition",
  "x-accel-buffering",
  "retry-after",
];

type Context = { params: Promise<{ path: string[] }> };

async function forward(request: NextRequest, { params }: Context): Promise<Response> {
  const { path } = await params;
  const target = `${API_URL}/api/v1/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;

  const headers = new Headers();
  for (const name of REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const workspace = request.cookies.get(WORKSPACE_COOKIE)?.value;
  if (workspace && /^[0-9a-f-]{36}$/i.test(workspace)) headers.set("x-anvesha-workspace", workspace);
  if (authMode === "jwt") {
    const token = await apiToken(request.headers);
    if (!token) {
      return Response.json({ detail: "Sign in first.", code: "unauthenticated" }, { status: 401 });
    }
    headers.set("authorization", `Bearer ${token}`);
  }
  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      // Required by fetch to stream a request body.
      ...(hasBody ? { duplex: "half" } : {}),
      redirect: "manual",
      cache: "no-store",
      // Stop the upstream request (e.g. an event stream) when the browser goes away.
      signal: request.signal,
    } as RequestInit);
  } catch (error) {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    console.error(`Anvesha API unreachable at ${API_URL}:`, error);
    return Response.json(
      {
        detail: "The Anvesha API isn't reachable. Check that the api service is running.",
        code: "api_unreachable",
      },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers();
  for (const name of RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export { forward as DELETE, forward as GET, forward as PATCH, forward as POST, forward as PUT };
