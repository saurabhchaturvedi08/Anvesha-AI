"use client";

import { api, unwrap } from "@/lib/api";

const STATE_KEY = "anvesha:openrouter-oauth-state";
export const CALLBACK_PATH = "/settings/openrouter/callback";

/** Send the user to OpenRouter to approve a key for Anvesha on their own account. */
export async function connectOpenRouter(): Promise<void> {
  const started = await unwrap(
    api.POST("/api/v1/providers/openrouter/oauth/start", {
      body: { callback_url: `${window.location.origin}${CALLBACK_PATH}` },
    }),
  );
  sessionStorage.setItem(STATE_KEY, started.state);
  window.location.assign(started.authorization_url);
}

/** The state saved when the flow started (read once). */
export function takeOpenRouterState(): string | null {
  const state = sessionStorage.getItem(STATE_KEY);
  sessionStorage.removeItem(STATE_KEY);
  return state;
}
