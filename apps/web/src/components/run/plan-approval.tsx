"use client";

import type { RunDetail } from "@anvesha/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { HandIcon, PlusIcon, RotateCcwIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Callout, Spinner } from "@/components/ui/feedback";
import { FieldHint, Label, Textarea } from "@/components/ui/input";
import { api, describeError, unwrap } from "@/lib/api";
import { keys, useServerConfig } from "@/lib/queries";

type Item = { question: string; rationale: string };
const MIN_LENGTH = 8;
const MAX_LENGTH = 600;

function planItems(run: RunDetail): Item[] {
  const plan = (run.plan ?? {}) as { sub_questions?: { question?: string; rationale?: string }[] };
  return (plan.sub_questions ?? []).map((q) => ({
    question: q.question ?? "",
    rationale: q.rationale ?? "",
  }));
}

/** Review the lead researcher's plan: approve it as is, or edit the sub-questions first. */
export function PlanApproval({ run, onCancel }: { run: RunDetail; onCancel: () => void }) {
  const queryClient = useQueryClient();
  const config = useServerConfig();
  const original = planItems(run);
  const strategy = String((run.plan as { strategy?: string } | null)?.strategy ?? "");
  const [items, setItems] = useState<Item[]>(original);
  const edited = JSON.stringify(items) !== JSON.stringify(original);
  const limit = Math.max(config.data?.depths?.[run.depth]?.max_sub_questions ?? 5, 1) * 2;
  const invalid = items.some((i) => i.question.trim().length < MIN_LENGTH) || items.length === 0;

  const approve = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/runs/{run_id}/approve", {
          params: { path: { run_id: run.id } },
          body: edited
            ? {
                sub_questions: items.map((i) => ({
                  question: i.question.trim(),
                  rationale: i.rationale.trim(),
                })),
                strategy,
              }
            : {},
        }),
      ),
    onSuccess: () => {
      toast.success(edited ? "Edited plan approved" : "Plan approved", {
        description: "The researchers are starting.",
      });
      void queryClient.invalidateQueries({ queryKey: keys.run(run.id) });
      void queryClient.invalidateQueries({ queryKey: keys.project(run.project_id) });
    },
  });

  const update = (index: number, patch: Partial<Item>) =>
    setItems((current) => current.map((item, i) => (i === index ? { ...item, ...patch } : item)));

  return (
    <Card className="border-warning/50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <HandIcon className="size-4 text-warning" /> Review the research plan
        </CardTitle>
        <CardDescription>
          Each sub-question goes to its own researcher, working in parallel. Edit, remove or add
          questions, then approve to start.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {strategy && (
          <div className="rounded-md bg-muted/60 p-3 text-sm">
            <span className="font-medium">Strategy: </span>
            <span className="text-muted-foreground">{strategy}</span>
          </div>
        )}
        <ol className="space-y-3">
          {items.map((item, index) => (
            <li key={index} className="flex gap-3">
              <span className="mt-2 grid size-6 shrink-0 place-items-center rounded-full bg-muted text-xs font-medium">
                {index + 1}
              </span>
              <div className="min-w-0 flex-1 space-y-1">
                <Label htmlFor={`sq-${index}`} className="sr-only">
                  Sub-question {index + 1}
                </Label>
                <Textarea
                  id={`sq-${index}`}
                  rows={2}
                  className="min-h-0"
                  maxLength={MAX_LENGTH}
                  value={item.question}
                  aria-invalid={item.question.trim().length < MIN_LENGTH}
                  onChange={(event) => update(index, { question: event.target.value })}
                />
                {item.rationale && <FieldHint>{item.rationale}</FieldHint>}
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="mt-1"
                aria-label={`Remove sub-question ${index + 1}`}
                disabled={items.length <= 1}
                onClick={() => setItems((current) => current.filter((_, i) => i !== index))}
              >
                <Trash2Icon />
              </Button>
            </li>
          ))}
        </ol>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={items.length >= limit}
            onClick={() => setItems((current) => [...current, { question: "", rationale: "" }])}
          >
            <PlusIcon /> Add a sub-question
          </Button>
          {edited && (
            <Button variant="ghost" size="sm" onClick={() => setItems(original)}>
              <RotateCcwIcon /> Undo edits
            </Button>
          )}
        </div>
        {approve.isError && <Callout tone="danger">{describeError(approve.error)}</Callout>}
        <div className="flex flex-wrap justify-end gap-2 border-t pt-4">
          <Button variant="ghost" onClick={onCancel} disabled={approve.isPending}>
            Cancel run
          </Button>
          <Button onClick={() => approve.mutate()} disabled={approve.isPending || invalid}>
            {approve.isPending && <Spinner />}
            {edited ? "Approve edited plan" : "Approve and start"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
