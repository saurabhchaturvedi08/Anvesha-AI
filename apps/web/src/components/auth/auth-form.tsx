"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Callout, Spinner } from "@/components/ui/feedback";
import { FieldHint, Input, Label } from "@/components/ui/input";
import { authClient } from "@/lib/auth-client";
import { safeNext } from "@/lib/redirects";

export type SignInMethods = { signUp: boolean; github: boolean; google: boolean };

function message(error: { message?: string; status?: number } | null | undefined): string {
  if (!error) return "Something went wrong. Try again.";
  if (error.status === 429) return "Too many attempts. Wait a minute and try again.";
  return error.message || "Something went wrong. Try again.";
}

export function AuthForm({ mode, methods }: { mode: "sign-in" | "sign-up"; methods: SignInMethods }) {
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNext(params.get("next"));
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const signingUp = mode === "sign-up";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const result = signingUp
      ? await authClient.signUp.email({ name: name.trim() || (email.split("@")[0] ?? email), email, password })
      : await authClient.signIn.email({ email, password });
    setBusy(false);
    if (result.error) {
      setError(message(result.error));
      return;
    }
    router.replace(next);
    router.refresh();
  }

  async function social(provider: "github" | "google") {
    setError(null);
    const result = await authClient.signIn.social({ provider, callbackURL: next });
    if (result?.error) setError(message(result.error));
  }

  const other = signingUp ? "/sign-in" : "/sign-up";
  const otherQuery = next !== "/" ? `?next=${encodeURIComponent(next)}` : "";

  return (
    <Card className="mx-auto w-full max-w-sm">
      <CardHeader>
        <CardTitle>{signingUp ? "Create your account" : "Sign in to Anvesha"}</CardTitle>
        <CardDescription>
          {signingUp
            ? "You get a personal workspace. Teams can share workspaces by invitation."
            : "Your projects, keys and research are in your workspaces."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {(methods.github || methods.google) && (
          <div className="grid gap-2">
            {methods.github && (
              <Button type="button" variant="outline" onClick={() => void social("github")}>
                Continue with GitHub
              </Button>
            )}
            {methods.google && (
              <Button type="button" variant="outline" onClick={() => void social("google")}>
                Continue with Google
              </Button>
            )}
            <p className="text-center text-xs text-muted-foreground">or with email</p>
          </div>
        )}
        <form className="space-y-3" onSubmit={(event) => void submit(event)}>
          {signingUp && (
            <div className="space-y-1.5">
              <Label htmlFor="name">Name</Label>
              <Input id="name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete={signingUp ? "new-password" : "current-password"}
              required
              minLength={signingUp ? 10 : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {signingUp && <FieldHint>At least 10 characters.</FieldHint>}
          </div>
          {error && (
            <Callout tone="danger" className="text-sm">
              {error}
            </Callout>
          )}
          <Button type="submit" className="w-full" disabled={busy}>
            {busy && <Spinner />}
            {signingUp ? "Create account" : "Sign in"}
          </Button>
        </form>
        {(methods.signUp || signingUp) && (
          <p className="text-center text-sm text-muted-foreground">
            {signingUp ? "Already have an account? " : "New here? "}
            <Link href={`${other}${otherQuery}`} className="font-medium text-foreground underline-offset-4 hover:underline">
              {signingUp ? "Sign in" : "Create an account"}
            </Link>
          </p>
        )}
      </CardContent>
    </Card>
  );
}
