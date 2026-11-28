/**
 * Sign-in for multi-user mode (ANVESHA_AUTH_MODE=jwt), with Better Auth.
 *
 * People sign in here; this server keeps their session in a cookie. When it forwards a
 * request to the Anvesha API (app/api/v1), it attaches a short-lived JWT signed with
 * keys it publishes at /api/auth/jwks, which the API verifies. Browsers never see the JWT.
 *
 * Better Auth's tables live in the "auth" schema of Anvesha's database (created by
 * `python -m anvesha.migrate`); this app creates and updates the tables themselves when
 * it starts (see instrumentation.ts).
 *
 * In single-user mode (the default) none of this runs: there is no sign-in.
 */

import { betterAuth, type BetterAuthOptions } from "better-auth";
import { nextCookies } from "better-auth/next-js";
import { jwt } from "better-auth/plugins/jwt";
import { Pool } from "pg";

export type AuthMode = "local" | "jwt";

export const authMode: AuthMode = process.env.ANVESHA_AUTH_MODE === "jwt" ? "jwt" : "local";

/** Must match the API's ANVESHA_AUTH_AUDIENCE. */
export const API_AUDIENCE = process.env.ANVESHA_AUTH_AUDIENCE || "anvesha-api";

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set when ANVESHA_AUTH_MODE=jwt.`);
  return value;
}

function socialProviders(): BetterAuthOptions["socialProviders"] {
  const providers: NonNullable<BetterAuthOptions["socialProviders"]> = {};
  if (process.env.GITHUB_CLIENT_ID && process.env.GITHUB_CLIENT_SECRET) {
    providers.github = {
      clientId: process.env.GITHUB_CLIENT_ID,
      clientSecret: process.env.GITHUB_CLIENT_SECRET,
    };
  }
  if (process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET) {
    providers.google = {
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    };
  }
  return providers;
}

/** Which sign-in methods the sign-in page offers. */
export function signInMethods() {
  const social = socialProviders() ?? {};
  return {
    signUp: process.env.ANVESHA_AUTH_DISABLE_SIGNUP !== "true",
    github: "github" in social,
    google: "google" in social,
  };
}

function options(): BetterAuthOptions {
  const baseURL = required("BETTER_AUTH_URL").replace(/\/+$/, "");
  return {
    baseURL,
    secret: required("BETTER_AUTH_SECRET"),
    database: new Pool({
      connectionString: required("ANVESHA_AUTH_DATABASE_URL"),
      // Keep Better Auth's tables apart from Anvesha's.
      options: "-c search_path=auth",
      max: 5,
    }),
    emailAndPassword: {
      enabled: true,
      minPasswordLength: 10,
      disableSignUp: process.env.ANVESHA_AUTH_DISABLE_SIGNUP === "true",
    },
    socialProviders: socialProviders(),
    rateLimit: { enabled: true, storage: "database" },
    plugins: [
      jwt({
        jwt: {
          issuer: baseURL,
          audience: API_AUDIENCE,
          expirationTime: "5m",
          definePayload: ({ user }) => ({ email: user.email, name: user.name }),
        },
      }),
      // Lets server actions set cookies; must stay last.
      nextCookies(),
    ],
  };
}

type Auth = ReturnType<typeof betterAuth>;
let instance: Auth | null = null;

/** The Better Auth instance (multi-user mode only), created on first use. */
export function getAuth(): Auth {
  if (authMode !== "jwt") throw new Error("Sign-in is off (ANVESHA_AUTH_MODE is not jwt).");
  instance ??= betterAuth(options());
  return instance;
}

/** A JWT for the signed-in user of this request, or null when there is no session. */
export async function apiToken(headers: Headers): Promise<string | null> {
  const auth = getAuth() as Auth & {
    api: { getToken: (input: { headers: Headers }) => Promise<{ token: string }> };
  };
  try {
    const { token } = await auth.api.getToken({ headers });
    return token || null;
  } catch {
    return null; // no session, or it expired
  }
}
