/**
 * Forwards /api/v1/* to the Anvesha API, so the browser only ever talks to this app
 * (no CORS, and the API needn't be exposed). Bodies and responses are streamed, which
 * keeps uploads and live Server-Sent Events working.
 *
 * When multi-user auth arrives, this is where the session becomes the API's credentials.
 */

import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const API_URL = (process.env.ANVESHA_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

const REQUEST_HEADERS = ["accept", "content-type", "last-event-id"];
const RESPONSE_HEADERS = ["content-type", "cache-control", "content-disposition", "x-accel-buffering"];

type Context = { params: Promise<{ path: string[] }> };

async function forward(request: NextRequest, { params }: Context): Promise<Response> {
  const { path } = await params;
  const target = `${API_URL}/api/v1/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;

  const headers = new Headers();
  for (const name of REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
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
