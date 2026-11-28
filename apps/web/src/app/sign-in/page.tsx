import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { Suspense } from "react";

import { AuthForm } from "@/components/auth/auth-form";
import { authMode, signInMethods } from "@/lib/auth-server";

export const metadata: Metadata = { title: "Sign in" };
// Reads the sign-in setup from the environment at request time, not at build time.
export const dynamic = "force-dynamic";

export default function SignInPage() {
  if (authMode !== "jwt") redirect("/");
  return (
    <div className="py-10">
      <Suspense>
        <AuthForm mode="sign-in" methods={signInMethods()} />
      </Suspense>
    </div>
  );
}
