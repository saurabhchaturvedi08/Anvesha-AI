"use client";

import type { ClarifyResponse } from "@anvesha/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  CheckIcon,
  FileUpIcon,
  LinkIcon,
  SparklesIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { budgetProblem, type RunOptions, RunOptionsFields, runRequest } from "@/components/run-options";
import { SetupBanner } from "@/components/setup-banner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Callout, Spinner } from "@/components/ui/feedback";
import { FieldHint, Input, Label, Textarea } from "@/components/ui/input";
import { api, ApiError, describeError, unwrap, uploadSource } from "@/lib/api";
import { formatBytes, titleFromQuestion, withClarifications } from "@/lib/format";
import { keys, useServerConfig, useSetupStatus } from "@/lib/queries";
import { cn } from "@/lib/utils";

type Step = "question" | "refine" | "options";
const STEPS: { id: Step; label: string }[] = [
  { id: "question", label: "Question" },
  { id: "refine", label: "Refine" },
  { id: "options", label: "Options" },
];
const MIN_QUESTION = 10;

export default function NewResearchPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const config = useServerConfig();
  const setup = useSetupStatus();

  const [step, setStep] = useState<Step>("question");
  const [question, setQuestion] = useState("");
  const [title, setTitle] = useState("");
  const [clarification, setClarification] = useState<ClarifyResponse | null>(null);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [options, setOptions] = useState<RunOptions>({ depth: "standard", maxCost: "", autopilot: false });
  const [urls, setUrls] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const finalQuestion = withClarifications(
    question,
    (clarification?.questions ?? []).map((q, i) => [q.question, answers[i] ?? ""]),
  );

  const clarify = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/clarify", { body: { question: question.trim() } })),
    onSuccess: (data) => {
      setClarification(data);
      setAnswers({});
    },
  });

  const start = useMutation({
    mutationFn: async () => {
      let id = projectId;
      if (!id) {
        setProgress("Creating the project…");
        const project = await unwrap(
          api.POST("/api/v1/projects", {
            body: {
              title: title.trim() || titleFromQuestion(question),
              objective: finalQuestion,
              depth: options.depth,
            },
          }),
        );
        id = project.id;
        setProjectId(id);
        const links = urls.split(/\s+/).map((u) => u.trim()).filter(Boolean);
        for (const [index, url] of links.entries()) {
          setProgress(`Saving source ${index + 1} of ${links.length + files.length}…`);
          try {
            await unwrap(
              api.POST("/api/v1/projects/{project_id}/sources", {
                params: { path: { project_id: id } },
                body: { url },
              }),
            );
          } catch (error) {
            toast.warning(`Couldn't save ${url}`, { description: describeError(error) });
          }
        }
        for (const [index, file] of files.entries()) {
          setProgress(`Saving source ${links.length + index + 1} of ${links.length + files.length}…`);
          try {
            await uploadSource(id, file);
          } catch (error) {
            toast.warning(`Couldn't add ${file.name}`, { description: describeError(error) });
          }
        }
      }
      setProgress("Starting the research agents…");
      const run = await unwrap(
        api.POST("/api/v1/projects/{project_id}/runs", {
          params: { path: { project_id: id } },
          body: { question: finalQuestion, ...runRequest(options) },
        }),
      );
      return { projectId: id, runId: run.id };
    },
    onSuccess: ({ projectId: id, runId }) => {
      void queryClient.invalidateQueries({ queryKey: keys.projects });
      router.push(`/projects/${id}/runs/${runId}`);
    },
    onSettled: () => setProgress(null),
  });

  const questionValid = question.trim().length >= MIN_QUESTION;
  const budgetError = budgetProblem(options.maxCost, config.data);
  const stepIndex = STEPS.findIndex((s) => s.id === step);

  const goRefine = () => {
    setStep("refine");
    if (setup.ready && !clarification && !clarify.isPending) clarify.mutate();
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New research</h1>
        <p className="text-sm text-muted-foreground">
          Describe what you want to find out. Agents will plan, search, read, and write a report
          where every claim cites a checked quote.
        </p>
      </div>

      <ol className="flex items-center gap-2 text-sm" aria-label="Steps">
        {STEPS.map((s, index) => (
          <li key={s.id} className="flex items-center gap-2">
            <span
              className={cn(
                "grid size-6 place-items-center rounded-full border text-xs font-medium",
                index < stepIndex && "border-primary bg-primary text-primary-foreground",
                index === stepIndex && "border-primary text-primary",
                index > stepIndex && "text-muted-foreground",
              )}
              aria-current={index === stepIndex ? "step" : undefined}
            >
              {index < stepIndex ? <CheckIcon className="size-3.5" /> : index + 1}
            </span>
            <span className={cn(index === stepIndex ? "font-medium" : "text-muted-foreground")}>
              {s.label}
            </span>
            {index < STEPS.length - 1 && <span className="mx-1 h-px w-6 bg-border sm:w-10" />}
          </li>
        ))}
      </ol>

      <SetupBanner />

      {step === "question" && (
        <Card>
          <CardContent className="space-y-5 pt-5">
            <div className="space-y-2">
              <Label htmlFor="question">Research question</Label>
              <Textarea
                id="question"
                autoFocus
                rows={5}
                maxLength={10_000}
                placeholder="e.g. What does recent evidence say about the effect of four-day work weeks on productivity and employee wellbeing?"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
              />
              <FieldHint>
                Be specific about scope, time period, or region if they matter. You can refine it in
                the next step.
              </FieldHint>
            </div>
            <div className="space-y-2">
              <Label htmlFor="title">Project title (optional)</Label>
              <Input
                id="title"
                maxLength={255}
                placeholder={question.trim() ? titleFromQuestion(question) : "Generated from your question"}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
            </div>
            <div className="flex justify-end">
              <Button onClick={goRefine} disabled={!questionValid}>
                Continue <ArrowRightIcon />
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {step === "refine" && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <SparklesIcon className="size-4 text-primary" /> Refine your question
            </CardTitle>
            <CardDescription>
              Optional. The lead researcher checks whether anything is ambiguous before planning.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {!setup.ready && !setup.isLoading ? (
              <p className="text-sm text-muted-foreground">
                Refining needs a model for the lead role. You can skip this step.
              </p>
            ) : clarify.isPending ? (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Spinner /> Reading your question…
              </p>
            ) : clarify.isError ? (
              <Callout
                tone="warning"
                title="Couldn't check the question"
                action={
                  <Button size="sm" variant="outline" onClick={() => clarify.mutate()}>
                    Try again
                  </Button>
                }
              >
                {describeError(clarify.error)} You can continue without refining.
              </Callout>
            ) : clarification?.ready || clarification?.questions.length === 0 ? (
              <Callout tone="info" title="Your question is clear">
                No clarifications needed. Continue to choose how deep to go.
              </Callout>
            ) : clarification ? (
              <div className="space-y-6">
                {clarification.questions.map((q, index) => (
                  <fieldset key={index} className="space-y-2">
                    <legend className="text-sm font-medium">{q.question}</legend>
                    {q.why && <FieldHint>{q.why}</FieldHint>}
                    <div className="flex flex-wrap gap-2 pt-1">
                      {q.options.map((option) => (
                        <button
                          key={option}
                          type="button"
                          aria-pressed={answers[index] === option}
                          onClick={() =>
                            setAnswers((current) => ({
                              ...current,
                              [index]: current[index] === option ? "" : option,
                            }))
                          }
                          className={cn(
                            "rounded-full border px-3 py-1 text-sm transition-colors hover:border-primary/50",
                            answers[index] === option && "border-primary bg-primary/10 text-primary",
                          )}
                        >
                          {option}
                        </button>
                      ))}
                    </div>
                    <Input
                      aria-label={`Your own answer to: ${q.question}`}
                      placeholder="Or type your own answer"
                      value={q.options.includes(answers[index] ?? "") ? "" : (answers[index] ?? "")}
                      onChange={(event) =>
                        setAnswers((current) => ({ ...current, [index]: event.target.value }))
                      }
                    />
                  </fieldset>
                ))}
              </div>
            ) : null}

            <div className="flex flex-wrap justify-between gap-2">
              <Button variant="ghost" onClick={() => setStep("question")}>
                <ArrowLeftIcon /> Back
              </Button>
              <Button onClick={() => setStep("options")} disabled={clarify.isPending}>
                {clarification && !clarification.ready && clarification.questions.length > 0
                  ? "Continue"
                  : "Skip"}{" "}
                <ArrowRightIcon />
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {step === "options" && (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>The question to research</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="whitespace-pre-wrap text-sm">{finalQuestion}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>How to research it</CardTitle>
            </CardHeader>
            <CardContent>
              <RunOptionsFields value={options} onChange={setOptions} config={config.data} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Your own sources (optional)</CardTitle>
              <CardDescription>
                Pages, papers or files the agents should read. They also search the web and
                scholarly databases.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="urls" className="flex items-center gap-1.5">
                  <LinkIcon className="size-3.5" /> Links, one per line
                </Label>
                <Textarea
                  id="urls"
                  rows={3}
                  placeholder="https://example.org/report.pdf"
                  value={urls}
                  onChange={(event) => setUrls(event.target.value)}
                  disabled={!!projectId}
                />
              </div>
              <div className="space-y-2">
                <input
                  ref={fileInput}
                  type="file"
                  multiple
                  accept=".pdf,.txt,.md,.markdown,.html,.htm,application/pdf,text/plain,text/markdown,text/html"
                  className="sr-only"
                  aria-label="Upload files"
                  onChange={(event) => {
                    const chosen = Array.from(event.target.files ?? []);
                    setFiles((current) => [...current, ...chosen]);
                    event.target.value = "";
                  }}
                />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => fileInput.current?.click()}
                  disabled={!!projectId}
                >
                  <FileUpIcon /> Add files
                </Button>
                <FieldHint>PDF, text, Markdown or HTML.</FieldHint>
                {files.length > 0 && (
                  <ul className="divide-y rounded-md border text-sm">
                    {files.map((file, index) => (
                      <li key={`${file.name}-${index}`} className="flex items-center gap-2 px-3 py-2">
                        <span className="min-w-0 flex-1 truncate">{file.name}</span>
                        <span className="text-xs text-muted-foreground">{formatBytes(file.size)}</span>
                        {!projectId && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="size-7"
                            aria-label={`Remove ${file.name}`}
                            onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}
                          >
                            <XIcon />
                          </Button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </CardContent>
          </Card>

          {start.isError && (
            <Callout
              tone="danger"
              title="The research couldn't start"
              action={
                start.error instanceof ApiError && start.error.code === "model_not_configured" ? (
                  <Button asChild size="sm" variant="outline">
                    <Link href="/settings">Open settings</Link>
                  </Button>
                ) : projectId ? (
                  <Button asChild size="sm" variant="outline">
                    <Link href={`/projects/${projectId}`}>Open the project</Link>
                  </Button>
                ) : undefined
              }
            >
              {describeError(start.error)}
              {projectId && " The project and its sources were saved; you can start again."}
            </Callout>
          )}

          <div className="flex flex-wrap justify-between gap-2">
            <Button variant="ghost" onClick={() => setStep("refine")} disabled={start.isPending || !!projectId}>
              <ArrowLeftIcon /> Back
            </Button>
            <Button
              size="lg"
              onClick={() => start.mutate()}
              disabled={start.isPending || !!budgetError || !setup.ready}
            >
              {start.isPending ? (
                <>
                  <Spinner /> {progress ?? "Starting…"}
                </>
              ) : (
                <>
                  Start research <ArrowRightIcon />
                </>
              )}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
