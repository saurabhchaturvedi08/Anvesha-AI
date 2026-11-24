import type { RunEvent } from "@anvesha/api-client";

import { hostname } from "@/lib/format";

export type Tone = "default" | "success" | "warning" | "danger" | "muted" | "primary";
export type EventIcon =
  | "queue"
  | "play"
  | "plan"
  | "wait"
  | "check"
  | "search"
  | "paper"
  | "page"
  | "read"
  | "note"
  | "finding"
  | "reject"
  | "stop"
  | "verify"
  | "review"
  | "write"
  | "retry"
  | "error"
  | "done"
  | "cancel";

export type EventView = {
  id: number;
  agent: string;
  title: string;
  detail?: string;
  tone: Tone;
  icon: EventIcon;
  at: string;
};

type Data = Record<string, unknown>;

const str = (value: unknown): string => (typeof value === "string" ? value : "");
const num = (value: unknown): number => (typeof value === "number" ? value : 0);
const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

export function agentLabel(agent: string | null | undefined): string {
  if (!agent) return "Anvesha";
  if (agent === "lead") return "Lead researcher";
  if (agent === "verifier") return "Verifier";
  if (agent === "writer") return "Writer";
  if (agent === "user") return "You";
  const researcher = /^researcher:(.+)$/.exec(agent);
  if (researcher) return `Researcher ${researcher[1]}`;
  return agent;
}

const STAGES: Record<string, string> = {
  planning: "Planning the research",
  verifying: "Checking that each quote supports its claim",
  writing: "Writing the report",
  budget_reached: "Budget reached, wrapping up with what was found",
};

function toolCall(data: Data): Pick<EventView, "title" | "icon"> {
  const args = (data.arguments ?? {}) as Data;
  const query = str(args.query);
  switch (data.tool) {
    case "web_search":
      return { title: `Searching the web for “${query}”`, icon: "search" };
    case "scholar_search":
      return { title: `Searching scholarly papers for “${query}”`, icon: "paper" };
    case "fetch_page":
      return { title: `Reading ${hostname(str(args.url)) ?? "a page"}`, icon: "page" };
    case "read_source":
      return { title: "Reading further in a saved source", icon: "read" };
    case "search_sources":
      return { title: `Searching saved sources for “${query}”`, icon: "search" };
    default:
      return { title: `Using ${str(data.tool)}`, icon: "note" };
  }
}

/** How an activity event reads in the UI, or null for events that are only noise. */
export function describeEvent(event: RunEvent): EventView | null {
  const data = (event.data ?? {}) as Data;
  const base = { id: event.id, agent: agentLabel(event.agent), at: event.created_at };
  const view = (
    title: string,
    icon: EventIcon,
    tone: Tone = "default",
    detail?: string,
  ): EventView => ({ ...base, title, icon, tone, detail: detail || undefined });

  switch (event.type) {
    case "run_queued":
      return view(data.retry ? "Queued to try again" : "Queued", "queue", "muted");
    case "run_started":
      return view(
        data.resuming
          ? "Continuing the run"
          : num(data.attempt) > 1
            ? `Started (attempt ${num(data.attempt)})`
            : "Started",
        "play",
        "muted",
      );
    case "stage": {
      const stage = str(data.stage);
      if (stage === "reviewing") return view(`Reviewing round ${num(data.round)}`, "review");
      if (stage === "writing" && data.findings !== undefined) {
        return view(`Writing the report from ${plural(num(data.findings), "finding")}`, "write");
      }
      return view(STAGES[stage] ?? stage, stage === "verifying" ? "verify" : "plan");
    }
    case "plan_created": {
      const questions = Array.isArray(data.sub_questions) ? data.sub_questions : [];
      return view(
        `Proposed a plan with ${plural(questions.length, "sub-question")}`,
        "plan",
        "primary",
        str(data.strategy),
      );
    }
    case "plan_ready":
      return view("Waiting for you to approve the plan", "wait", "warning");
    case "plan_approval_received":
      return view(data.edited ? "You approved an edited plan" : "You approved the plan", "check");
    case "plan_approved":
      return view(
        `Researching ${plural(num(data.sub_questions), "sub-question")} in parallel`,
        "play",
        "primary",
      );
    case "researcher_started":
      return view("Started on a sub-question", "play", "default", str(data.question));
    case "tool_call": {
      const { title, icon } = toolCall(data);
      return view(title, icon, "muted");
    }
    case "tool_result":
      if (data.ok) return null;
      return view(`${str(data.tool)} didn't work`, "error", "warning", str(data.summary));
    case "agent_message":
      return view("Note", "note", "muted", str(data.text));
    case "finding_recorded":
      return view(
        "Recorded a finding",
        "finding",
        "success",
        [str(data.statement), str(data.source_title) && `Source: ${str(data.source_title)}`]
          .filter(Boolean)
          .join("\n"),
      );
    case "finding_rejected":
      if (data.reason !== undefined) {
        return view("Rejected a finding", "reject", "warning", str(data.reason));
      }
      return view(
        "A quote didn't match its source word for word; trying again",
        "reject",
        "muted",
        str(data.statement),
      );
    case "researcher_stopped":
      return view(
        data.reason === "budget" ? "Stopped: the budget ran out" : "Stopped early",
        "stop",
        "warning",
        str(data.detail),
      );
    case "researcher_finished": {
      const coverage = str(data.coverage);
      const label =
        coverage === "answered"
          ? "answered"
          : coverage === "partial"
            ? "partly answered"
            : "nothing found";
      return view(
        `Finished: ${label}, ${plural(num(data.findings), "finding")}`,
        "done",
        coverage === "not_found" ? "warning" : "success",
        str(data.summary),
      );
    }
    case "verification_finished":
      return view(
        `Checked ${plural(num(data.checked), "finding")}: ${num(data.verified)} supported, ${num(data.rejected)} rejected`,
        "verify",
        "success",
        num(data.not_reviewed) ? `${num(data.not_reviewed)} couldn't be reviewed.` : undefined,
      );
    case "review": {
      const followUps = Array.isArray(data.follow_up_questions) ? data.follow_up_questions : [];
      return view(
        followUps.length
          ? `Found gaps: ${plural(followUps.length, "follow-up question")}`
          : "The evidence covers the question",
        "review",
        "primary",
        [str(data.assessment), ...followUps.map((q) => `• ${str(q)}`)].filter(Boolean).join("\n"),
      );
    }
    case "cancel_requested":
      return view("Cancelling…", "cancel", "warning");
    case "run_retrying":
      return view("Hit a temporary problem; will try again shortly", "retry", "warning", str(data.message));
    case "run_failed":
      return view("The run failed", "error", "danger", str(data.message));
    case "run_completed":
      return view(
        data.report_status === "incomplete" ? "Finished with a partial report" : "Research complete",
        "done",
        data.report_status === "incomplete" ? "warning" : "success",
      );
    case "run_cancelled":
      return view("Cancelled", "cancel", "muted");
    default:
      return view(event.type.replaceAll("_", " "), "note", "muted");
  }
}

/** Events that change what the run's detail endpoint returns. */
export const RUN_CHANGING_EVENTS = new Set([
  "run_started",
  "plan_created",
  "plan_ready",
  "plan_approved",
  "researcher_started",
  "researcher_finished",
  "verification_finished",
  "review",
  "run_retrying",
  "run_failed",
  "run_completed",
  "run_cancelled",
  "cancel_requested",
]);
