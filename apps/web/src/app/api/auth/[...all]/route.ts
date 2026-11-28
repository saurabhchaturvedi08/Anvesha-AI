/** Better Auth's endpoints: sign-in, sign-up, sessions, and the JWKS the API verifies with. */

import { toNextJsHandler } from "better-auth/next-js";
import type { NextRequest } from "next/server";

import { authMode, getAuth } from "@/lib/auth-server";

export const dynamic = "force-dynamic";

function off(): Response {
  return Response.json(
    { detail: "Sign-in is off: this server runs in single-user mode.", code: "auth_disabled" },
    { status: 404 },
  );
}

async function handle(request: NextRequest): Promise<Response> {
  if (authMode !== "jwt") return off();
  const handlers = toNextJsHandler(getAuth());
  return request.method === "GET" ? handlers.GET(request) : handlers.POST(request);
}

export { handle as GET, handle as POST };
