"use client";

import type { Project } from "@anvesha/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { budgetProblem, type RunOptions, RunOptionsFields, runRequest } from "@/components/run-options";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Callout, Spinner } from "@/components/ui/feedback";
import { FieldHint, Input, Label, Textarea } from "@/components/ui/input";
import { api, describeError, unwrap } from "@/lib/api";
import { keys, useServerConfig } from "@/lib/queries";

type DialogProps = { project: Project; open: boolean; onOpenChange: (open: boolean) => void };

export function NewRunDialog({ project, open, onOpenChange }: DialogProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const config = useServerConfig();
  const [question, setQuestion] = useState(project.objective);
  const [options, setOptions] = useState<RunOptions>({
    depth: project.depth,
    maxCost: "",
    autopilot: false,
  });
  const start = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/projects/{project_id}/runs", {
          params: { path: { project_id: project.id } },
          body: { question: question.trim() || null, ...runRequest(options) },
        }),
      ),
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: keys.runs(project.id) });
      void queryClient.invalidateQueries({ queryKey: keys.project(project.id) });
      void queryClient.invalidateQueries({ queryKey: keys.projects });
      onOpenChange(false);
      router.push(`/projects/${project.id}/runs/${run.id}`);
    },
  });
  const budgetError = budgetProblem(options.maxCost, config.data);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Start a new research run</DialogTitle>
          <DialogDescription>
            New findings and a new report version are added to this project. Saved sources are
            reused.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="run-question">Question</Label>
          <Textarea
            id="run-question"
            rows={4}
            maxLength={10_000}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
          />
          <FieldHint>Narrow it down to dig deeper into one part of the topic.</FieldHint>
        </div>
        <RunOptionsFields value={options} onChange={setOptions} config={config.data} idPrefix="dialog" />
        {start.isError && <Callout tone="danger">{describeError(start.error)}</Callout>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => start.mutate()}
            disabled={start.isPending || !!budgetError || question.trim().length < 3}
          >
            {start.isPending && <Spinner />} Start research
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function EditProjectDialog({ project, open, onOpenChange }: DialogProps) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState(project.title);
  const [objective, setObjective] = useState(project.objective);
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PATCH("/api/v1/projects/{project_id}", {
          params: { path: { project_id: project.id } },
          body: { title: title.trim(), objective: objective.trim() },
        }),
      ),
    onSuccess: (updated) => {
      queryClient.setQueryData(keys.project(project.id), updated);
      void queryClient.invalidateQueries({ queryKey: keys.projects });
      onOpenChange(false);
      toast.success("Project updated");
    },
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit project</DialogTitle>
          <DialogDescription>The objective is the default question for new runs.</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="edit-title">Title</Label>
          <Input id="edit-title" maxLength={255} value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="edit-objective">Objective</Label>
          <Textarea
            id="edit-objective"
            rows={5}
            maxLength={10_000}
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
          />
        </div>
        {save.isError && <Callout tone="danger">{describeError(save.error)}</Callout>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => save.mutate()}
            disabled={save.isPending || !title.trim() || !objective.trim()}
          >
            {save.isPending && <Spinner />} Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function DeleteProjectDialog({ project, open, onOpenChange }: DialogProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () =>
      unwrap(
        api.DELETE("/api/v1/projects/{project_id}", { params: { path: { project_id: project.id } } }),
      ),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: keys.project(project.id) });
      void queryClient.invalidateQueries({ queryKey: keys.projects });
      toast.success(`Deleted “${project.title}”`);
      router.push("/");
    },
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete this project?</DialogTitle>
          <DialogDescription>
            “{project.title}” and all its runs, sources, findings, reports and chats will be
            permanently deleted. This can&apos;t be undone.
          </DialogDescription>
        </DialogHeader>
        {remove.isError && <Callout tone="danger">{describeError(remove.error)}</Callout>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Keep it
          </Button>
          <Button variant="destructive" onClick={() => remove.mutate()} disabled={remove.isPending}>
            {remove.isPending && <Spinner />} Delete project
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
