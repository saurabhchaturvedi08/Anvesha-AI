import type { RunStatus } from "@anvesha/api-client";
import {
  CheckCircle2Icon,
  CircleDashedIcon,
  CircleSlashIcon,
  HandIcon,
  Loader2Icon,
  XCircleIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";

const STATUS: Record<
  RunStatus,
  { label: string; tone: "muted" | "primary" | "warning" | "success" | "danger"; icon: typeof Loader2Icon }
> = {
  queued: { label: "Queued", tone: "muted", icon: CircleDashedIcon },
  running: { label: "Researching", tone: "primary", icon: Loader2Icon },
  awaiting_approval: { label: "Needs your approval", tone: "warning", icon: HandIcon },
  completed: { label: "Completed", tone: "success", icon: CheckCircle2Icon },
  failed: { label: "Failed", tone: "danger", icon: XCircleIcon },
  cancelled: { label: "Cancelled", tone: "muted", icon: CircleSlashIcon },
};

export function RunStatusBadge({ status, className }: { status: RunStatus; className?: string }) {
  const { label, tone, icon: Icon } = STATUS[status];
  return (
    <Badge tone={tone} className={className}>
      <Icon className={status === "running" ? "animate-spin" : undefined} aria-hidden />
      {label}
    </Badge>
  );
}

export const DEPTH_LABELS: Record<string, string> = {
  quick: "Quick",
  standard: "Standard",
  deep: "Deep",
};

export function DepthBadge({ depth }: { depth: string }) {
  return <Badge tone="outline">{DEPTH_LABELS[depth] ?? depth}</Badge>;
}
