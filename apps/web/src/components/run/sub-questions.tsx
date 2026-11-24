"use client";

import type { SubQuestion } from "@anvesha/api-client";
import { CheckCircle2Icon, CircleDashedIcon, CircleHelpIcon, Loader2Icon, XCircleIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const COVERAGE: Record<string, { label: string; tone: "success" | "warning" | "danger" }> = {
  answered: { label: "Answered", tone: "success" },
  partial: { label: "Partly answered", tone: "warning" },
  not_found: { label: "Nothing found", tone: "danger" },
};

function StatusIcon({ status }: { status: string }) {
  if (status === "researching") return <Loader2Icon className="size-4 animate-spin text-primary" />;
  if (status === "done") return <CheckCircle2Icon className="size-4 text-success" />;
  if (status === "failed") return <XCircleIcon className="size-4 text-destructive" />;
  return <CircleDashedIcon className="size-4 text-muted-foreground" />;
}

/** The plan's sub-questions with each researcher's progress and outcome. */
export function SubQuestionList({ items }: { items: SubQuestion[] }) {
  if (items.length === 0) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <CircleHelpIcon className="size-4" /> The plan appears here once the lead researcher has one.
      </p>
    );
  }
  const rounds = Array.from(new Set(items.map((item) => item.round)));
  return (
    <div className="space-y-4">
      {rounds.map((round) => (
        <div key={round} className="space-y-2">
          {rounds.length > 1 && (
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {round === 1 ? "Initial plan" : `Follow-up round ${round}`}
            </p>
          )}
          <ul className="space-y-2">
            {items
              .filter((item) => item.round === round)
              .map((item) => (
                <li key={item.id} className="rounded-md border p-3">
                  <details className="group">
                    <summary className="flex cursor-pointer list-none items-start gap-2">
                      <span className="mt-0.5">
                        <StatusIcon status={item.status} />
                      </span>
                      <span className={cn("flex-1 text-sm", item.status === "pending" && "text-muted-foreground")}>
                        {item.question}
                      </span>
                    </summary>
                    <div className="mt-2 space-y-2 pl-6 text-xs text-muted-foreground">
                      <div className="flex flex-wrap gap-2">
                        {item.coverage && COVERAGE[item.coverage] && (
                          <Badge tone={COVERAGE[item.coverage]!.tone}>{COVERAGE[item.coverage]!.label}</Badge>
                        )}
                        {item.steps > 0 && <span>{item.steps} steps</span>}
                      </div>
                      {item.summary && <p className="whitespace-pre-line">{item.summary}</p>}
                      {item.gaps.length > 0 && (
                        <ul className="list-disc space-y-0.5 pl-4">
                          {item.gaps.map((gap, i) => (
                            <li key={i}>{gap}</li>
                          ))}
                        </ul>
                      )}
                      {!item.summary && item.rationale && <p>{item.rationale}</p>}
                    </div>
                  </details>
                </li>
              ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
