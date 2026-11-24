"use client";

import type { Depth, ServerConfig } from "@anvesha/api-client";

import { FieldHint, Input, Label } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { formatUsd, toNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

export type RunOptions = {
  depth: Depth;
  /** Dollars as typed; empty means the server's default. */
  maxCost: string;
  autopilot: boolean;
};

const DEPTHS: { id: Depth; title: string; blurb: string }[] = [
  { id: "quick", title: "Quick", blurb: "A fast scan for a first answer." },
  { id: "standard", title: "Standard", blurb: "Balanced, with a follow-up round for gaps." },
  { id: "deep", title: "Deep", blurb: "Thorough, for literature reviews and hard questions." },
];

function depthDetail(config: ServerConfig | undefined, depth: Depth): string | null {
  const info = config?.depths?.[depth];
  if (!info) return null;
  const rounds = info.max_rounds === 1 ? "1 round" : `up to ${info.max_rounds} rounds`;
  return `${rounds}, ${info.max_sub_questions} parallel researchers`;
}

/** Returns a problem with the typed budget, or null if it's fine. */
export function budgetProblem(value: string, config: ServerConfig | undefined): string | null {
  if (!value.trim()) return null;
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount <= 0) return "Enter an amount in dollars, like 2.50.";
  const ceiling = toNumber(config?.run_max_cost_ceiling_usd);
  if (ceiling !== null && amount > ceiling) {
    return `This server allows at most ${formatUsd(ceiling)} per run.`;
  }
  return null;
}

export function RunOptionsFields({
  value,
  onChange,
  config,
  idPrefix = "run",
}: {
  value: RunOptions;
  onChange: (value: RunOptions) => void;
  config: ServerConfig | undefined;
  idPrefix?: string;
}) {
  const defaultCost = config?.run_default_max_cost_usd;
  const problem = budgetProblem(value.maxCost, config);
  return (
    <div className="space-y-6">
      <fieldset className="space-y-2">
        <legend className="mb-2 text-sm font-medium">Depth</legend>
        <div className="grid gap-2 sm:grid-cols-3">
          {DEPTHS.map((depth) => (
            <label
              key={depth.id}
              className={cn(
                "flex cursor-pointer flex-col gap-1 rounded-lg border p-3 transition-colors hover:border-primary/50 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ring",
                value.depth === depth.id && "border-primary bg-primary/5",
              )}
            >
              <input
                type="radio"
                name={`${idPrefix}-depth`}
                value={depth.id}
                checked={value.depth === depth.id}
                onChange={() => onChange({ ...value, depth: depth.id })}
                className="sr-only"
              />
              <span className="text-sm font-medium">{depth.title}</span>
              <span className="text-xs text-muted-foreground">{depth.blurb}</span>
              {depthDetail(config, depth.id) && (
                <span className="text-xs text-muted-foreground/80">{depthDetail(config, depth.id)}</span>
              )}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="grid gap-6 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor={`${idPrefix}-budget`}>Spending limit (USD)</Label>
          <div className="relative">
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground">
              $
            </span>
            <Input
              id={`${idPrefix}-budget`}
              inputMode="decimal"
              className="pl-6"
              placeholder={defaultCost ? String(toNumber(defaultCost)?.toFixed(2)) : "No limit"}
              value={value.maxCost}
              aria-invalid={!!problem}
              aria-describedby={`${idPrefix}-budget-hint`}
              onChange={(event) => onChange({ ...value, maxCost: event.target.value })}
            />
          </div>
          <FieldHint id={`${idPrefix}-budget-hint`} className={problem ? "text-destructive" : undefined}>
            {problem ??
              "When the limit is reached, research stops and a report is written from what was found. Models with unknown prices aren't counted."}
          </FieldHint>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor={`${idPrefix}-review`}>Review the plan first</Label>
            <Switch
              id={`${idPrefix}-review`}
              checked={!value.autopilot}
              onCheckedChange={(checked) => onChange({ ...value, autopilot: !checked })}
            />
          </div>
          <FieldHint>
            {value.autopilot
              ? "Research starts as soon as the plan is ready."
              : "You'll see the sub-questions and can edit them before research starts."}
          </FieldHint>
        </div>
      </div>
    </div>
  );
}

/** The request fields for these options. */
export function runRequest(options: RunOptions) {
  return {
    depth: options.depth,
    autopilot: options.autopilot,
    max_cost_usd: options.maxCost.trim() ? options.maxCost.trim() : null,
  };
}
