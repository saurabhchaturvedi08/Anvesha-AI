/**
 * Multi-user mode: send visitors without a session to the sign-in page.
 *
 * This only looks for the session cookie (cheap, and good enough to route people); the
 * API checks every request properly. Single-user mode lets everything through.
 */

import { getSessionCookie } from "better-auth/cookies";
import { type NextRequest, NextResponse } from "next/server";

const PUBLIC_PATHS = ["/sign-in", "/sign-up"];

export function proxy(request: NextRequest): NextResponse {
  if (process.env.ANVESHA_AUTH_MODE !== "jwt") return NextResponse.next();
  const { pathname, search } = request.nextUrl;
  const isPublic = PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`));
  const signedIn = !!getSessionCookie(request);
  if (!signedIn && !isPublic) {
    if (pathname.startsWith("/api/")) {
      return NextResponse.json({ detail: "Sign in first.", code: "unauthenticated" }, { status: 401 });
    }
    const url = new URL("/sign-in", request.url);
    url.searchParams.set("next", `${pathname}${search}`);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  // Everything except Better Auth's own endpoints and static files.
  matcher: ["/((?!api/auth|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|ico|webp)$).*)"],
};
