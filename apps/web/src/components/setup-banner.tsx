"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/feedback";
import { useSetupStatus } from "@/lib/queries";

/** Shown until every agent role has a model, since research can't start before that. */
export function SetupBanner() {
  const { isLoading, ready, missing } = useSetupStatus();
  if (isLoading || ready) return null;
  return (
    <Callout
      tone="warning"
      title="Choose your models to start researching"
      action={
        <Button asChild size="sm">
          <Link href="/settings">Open settings</Link>
        </Button>
      }
    >
      Add an API key (or a local Ollama server) and pick a model for each agent. Still missing:{" "}
      {missing.join(", ")}.
    </Callout>
  );
}
