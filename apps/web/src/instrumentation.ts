/**
 * Runs once when the server starts. In multi-user mode, creates or updates Better Auth's
 * tables (in the "auth" schema, which `python -m anvesha.migrate` creates first).
 */

export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs" || process.env.ANVESHA_AUTH_MODE !== "jwt") return;
  const { getAuth } = await import("@/lib/auth-server");
  const { getMigrations } = await import("better-auth/db/migration");
  const { toBeCreated, toBeAdded, runMigrations } = await getMigrations(getAuth().options);
  if (toBeCreated.length || toBeAdded.length) {
    console.log(
      `Updating sign-in tables: ${[...toBeCreated, ...toBeAdded].map((t) => t.table).join(", ")}`,
    );
    await runMigrations();
  }
}
