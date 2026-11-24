"use client";

import type { ModelPreference, ModelRole, Provider } from "@anvesha/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FlaskConicalIcon } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState, Skeleton, Spinner } from "@/components/ui/feedback";
import { FieldHint, Input, Label, Select } from "@/components/ui/input";
import { api, describeError, unwrap } from "@/lib/api";
import { formatUsd } from "@/lib/format";
import { keys, useModelPreferences, useProviderModels, useProviders } from "@/lib/queries";

const ROLES: Record<ModelRole, { title: string; description: string; optional?: boolean }> = {
  lead: {
    title: "Lead researcher",
    description:
      "Plans the research, reviews progress, refines questions and answers chat. Use your strongest model.",
  },
  researcher: {
    title: "Researchers",
    description:
      "Search, read and record evidence, several at once. A fast, affordable model that's good at tool use works well.",
  },
  verifier: {
    title: "Verifier",
    description: "Checks that each quote really supports its claim. A careful, inexpensive model is enough.",
  },
  writer: {
    title: "Writer",
    description: "Writes the final report from verified findings. Pick a model that writes well.",
  },
  embedding: {
    title: "Embeddings",
    description:
      "Optional. Adds semantic search over saved sources; keyword search works without it. OpenAI-compatible providers only.",
    optional: true,
  },
};

export function ModelRolesCard() {
  const preferences = useModelPreferences();
  const providers = useProviders();
  const configured = (providers.data ?? []).filter((p) => p.configured);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Models for each agent</CardTitle>
        <CardDescription>
          Each agent role can use a different model and provider, so you can balance quality and cost.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {preferences.isPending || providers.isPending ? (
          <Skeleton className="h-64" />
        ) : preferences.isError ? (
          <ErrorState error={preferences.error} retry={() => void preferences.refetch()} />
        ) : configured.length === 0 ? (
          <p className="text-sm text-muted-foreground">Connect a model provider above first.</p>
        ) : (
          <ul className="divide-y">
            {preferences.data.map((preference) => (
              <RoleRow
                key={`${preference.role}-${preference.provider}-${preference.model}`}
                preference={preference}
                providers={configured}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function RoleRow({ preference, providers }: { preference: ModelPreference; providers: Provider[] }) {
  const queryClient = useQueryClient();
  const id = useId();
  const role = ROLES[preference.role];
  const [provider, setProvider] = useState(preference.provider ?? providers[0]?.id ?? "");
  const [model, setModel] = useState(preference.model ?? "");
  const [browsing, setBrowsing] = useState(false);
  const models = useProviderModels(provider || null, browsing);
  const changed = provider !== (preference.provider ?? "") || model.trim() !== (preference.model ?? "");

  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/v1/model-preferences/{role}", {
          params: { path: { role: preference.role } },
          body: { provider, model: model.trim() },
        }),
      ),
    onSuccess: () => {
      toast.success(`${role.title}: ${model.trim()}`);
      void queryClient.invalidateQueries({ queryKey: keys.modelPreferences });
    },
    onError: (error) => toast.error(describeError(error)),
  });
  const clear = useMutation({
    mutationFn: () =>
      unwrap(api.DELETE("/api/v1/model-preferences/{role}", { params: { path: { role: preference.role } } })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.modelPreferences }),
    onError: (error) => toast.error(describeError(error)),
  });
  const test = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/llm/test", {
          body: { role: preference.role as "lead" | "researcher" | "verifier" | "writer" },
        }),
      ),
    onSuccess: (result) =>
      toast.success(`${result.model} replied`, {
        description: `“${result.text.slice(0, 160)}” · ${result.input_tokens + result.output_tokens} tokens · ${formatUsd(result.cost_usd, "price unknown")}`,
      }),
    onError: (error) => toast.error("The test failed", { description: describeError(error) }),
  });

  return (
    <li className="grid gap-3 py-4 md:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{role.title}</span>
          {role.optional ? (
            <Badge tone="outline">Optional</Badge>
          ) : !preference.model ? (
            <Badge tone="warning">Required</Badge>
          ) : null}
        </div>
        <FieldHint>{role.description}</FieldHint>
      </div>
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (changed && model.trim()) save.mutate();
        }}
      >
        <div className="space-y-1">
          <Label htmlFor={`${id}-provider`} className="text-xs text-muted-foreground">
            Provider
          </Label>
          <Select id={`${id}-provider`} value={provider} onChange={(event) => setProvider(event.target.value)}>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
        </div>
        <div className="min-w-48 flex-1 space-y-1">
          <Label htmlFor={`${id}-model`} className="text-xs text-muted-foreground">
            Model
          </Label>
          <Input
            id={`${id}-model`}
            list={`${id}-models`}
            placeholder="Type or pick a model"
            value={model}
            onFocus={() => setBrowsing(true)}
            onChange={(event) => setModel(event.target.value)}
            spellCheck={false}
          />
          <datalist id={`${id}-models`}>
            {(models.data ?? []).map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        </div>
        <div className="flex gap-2">
          <Button type="submit" size="md" disabled={!changed || !model.trim() || save.isPending}>
            {save.isPending && <Spinner />} Save
          </Button>
          {preference.model && preference.role !== "embedding" && (
            <Button
              variant="outline"
              size="icon"
              onClick={() => test.mutate()}
              disabled={test.isPending || changed}
              aria-label={`Send a test message to the ${role.title.toLowerCase()} model`}
              title="Send a short test message (costs a fraction of a cent)"
            >
              {test.isPending ? <Spinner /> : <FlaskConicalIcon />}
            </Button>
          )}
          {preference.model && (
            <Button variant="ghost" onClick={() => clear.mutate()} disabled={clear.isPending}>
              Clear
            </Button>
          )}
        </div>
        {browsing && models.isError && (
          <p className="w-full text-xs text-muted-foreground">
            Couldn&apos;t list models ({describeError(models.error)}). You can still type a model name.
          </p>
        )}
      </form>
    </li>
  );
}
