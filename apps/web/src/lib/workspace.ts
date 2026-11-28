/** The workspace the user is working in, remembered in a cookie the API proxy forwards. */

export const WORKSPACE_COOKIE = "anvesha_workspace";

/** Switch workspaces: later API calls act in this one (null: back to the default). */
export function setWorkspaceCookie(workspaceId: string | null): void {
  const maxAge = workspaceId ? 60 * 60 * 24 * 365 : 0;
  document.cookie = `${WORKSPACE_COOKIE}=${workspaceId ?? ""}; Path=/; Max-Age=${maxAge}; SameSite=Lax`;
}
