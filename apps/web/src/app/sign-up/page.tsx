import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { Suspense } from "react";

import { AuthForm } from "@/components/auth/auth-form";
import { authMode, signInMethods } from "@/lib/auth-server";

export const metadata: Metadata = { title: "Create an account" };
export const dynamic = "force-dynamic";

export default function SignUpPage() {
  if (authMode !== "jwt") redirect("/");
  const methods = signInMethods();
  // With sign-up closed, people join through invitations from existing users.
  if (!methods.signUp) redirect("/sign-in");
  return (
    <div className="py-10">
      <Suspense>
        <AuthForm mode="sign-up" methods={methods} />
      </Suspense>
    </div>
  );
}
