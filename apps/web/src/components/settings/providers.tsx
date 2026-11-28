"use client";

import type { Provider, SearchProvider } from "@anvesha/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ExternalLinkIcon, KeyRoundIcon, LinkIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Callout, ErrorState, Skeleton, Spinner } from "@/components/ui/feedback";
import { FieldHint, Input, Label } from "@/components/ui/input";
import { api, describeError, unwrap } from "@/lib/api";
import { connectOpenRouter } from "@/lib/openrouter";
import { keys, useCanManage, useProviders, useSearchProviders } from "@/lib/queries";

function AccessBadge({
  source,
  status,
}: {
  source: "workspace" | "deployment" | null;
  status: "valid" | "invalid" | "unverified" | null;
}) {
  if (!source) return <Badge tone="muted">Not set up</Badge>;
  if (source === "deployment") return <Badge tone="primary">Server key</Badge>;
  if (status === "invalid") return <Badge tone="danger">Key rejected</Badge>;
  if (status === "unverified") return <Badge tone="warning">Saved, not verified</Badge>;
  return <Badge tone="success">Connected</Badge>;
}

export function ModelProvidersCard() {
  const providers = useProviders();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Provider | null>(null);
  const canManage = useCanManage();
  const [connecting, setConnecting] = useState(false);
  const connect = () => {
    setConnecting(true);
    connectOpenRouter().catch((error: unknown) => {
      setConnecting(false);
      toast.error(describeError(error));
    });
  };
  const remove = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/providers/{provider}/credential", { params: { path: { provider: id } } })),
    onSuccess: () => {
      toast.success("Key removed");
      void queryClient.invalidateQueries({ queryKey: keys.providers });
    },
    onError: (error) => toast.error(describeError(error)),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Model providers</CardTitle>
        <CardDescription>
          Bring your own API keys. Keys are encrypted on the server, checked with the provider before
          saving, and never shown again. Ollama runs models on your own computer for free.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {providers.isPending ? (
          <Skeleton className="h-48" />
        ) : providers.isError ? (
          <ErrorState error={providers.error} retry={() => void providers.refetch()} />
        ) : (
          <ul className="divide-y">
            {providers.data.map((provider) => (
              <li key={provider.id} className="flex flex-wrap items-center gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{provider.name}</span>
                    <AccessBadge source={provider.source} status={provider.status} />
                    {provider.key_hint && (
                      <span className="font-mono text-xs text-muted-foreground">{provider.key_hint}</span>
                    )}
                  </div>
                  {provider.last_error && provider.status !== "valid" && (
                    <p className="mt-1 text-xs text-muted-foreground">{provider.last_error}</p>
                  )}
                  {provider.api_base && <p className="mt-1 text-xs text-muted-foreground">{provider.api_base}</p>}
                </div>
                <a
                  href={provider.docs_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  {provider.requires_api_key ? "Get a key" : "Docs"} <ExternalLinkIcon className="size-3" />
                </a>
                <div className="flex gap-2">
                  {provider.id === "openrouter" && provider.source !== "workspace" && (
                    <Button size="sm" disabled={!canManage || connecting} onClick={connect}>
                      {connecting ? <Spinner /> : <LinkIcon />}
                      Connect account
                    </Button>
                  )}
                  <Button size="sm" variant="outline" disabled={!canManage} onClick={() => setEditing(provider)}>
                    <KeyRoundIcon />
                    {provider.source === "workspace" ? "Replace" : provider.requires_api_key ? "Add key" : "Set up"}
                  </Button>
                  {provider.source === "workspace" && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={!canManage || remove.isPending}
                      onClick={() => remove.mutate(provider.id)}
                    >
                      Remove
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
      {editing && <ProviderKeyDialog provider={editing} onClose={() => setEditing(null)} />}
    </Card>
  );
}

function ProviderKeyDialog({ provider, onClose }: { provider: Provider; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [apiKey, setApiKey] = useState("");
  const [apiBase, setApiBase] = useState(provider.api_base ?? "");
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/v1/providers/{provider}/credential", {
          params: { path: { provider: provider.id } },
          body: {
            api_key: apiKey.trim() || null,
            api_base: provider.custom_api_base && apiBase.trim() ? apiBase.trim() : null,
          },
        }),
      ),
    onSuccess: (saved) => {
      toast.success(`${provider.name} connected`, {
        description:
          saved.status === "valid"
            ? `${saved.models.length} models available.`
            : "Saved, but the provider couldn't be reached to verify it.",
      });
      void queryClient.invalidateQueries({ queryKey: keys.providers });
      void queryClient.invalidateQueries({ queryKey: keys.models(provider.id) });
      onClose();
    },
  });
  const needsKey = provider.requires_api_key && !apiKey.trim();
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Connect {provider.name}</DialogTitle>
          <DialogDescription>
            The key is checked with {provider.name} (listing models is free) and then stored
            encrypted.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!needsKey) save.mutate();
          }}
        >
          <div className="space-y-2">
            <Label htmlFor="api-key">API key{provider.requires_api_key ? "" : " (optional)"}</Label>
            <Input
              id="api-key"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
            />
          </div>
          {provider.custom_api_base && (
            <div className="space-y-2">
              <Label htmlFor="api-base">API base URL</Label>
              <Input
                id="api-base"
                type="url"
                placeholder={provider.default_api_base ?? "http://localhost:11434/v1"}
                value={apiBase}
                onChange={(event) => setApiBase(event.target.value)}
              />
              <FieldHint>
                With Docker, a server on your computer is at http://host.docker.internal:PORT.
              </FieldHint>
            </div>
          )}
          {save.isError && <Callout tone="danger">{describeError(save.error)}</Callout>}
          <DialogFooter>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending || needsKey}>
              {save.isPending && <Spinner />} Check and save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function SearchProvidersCard() {
  const providers = useSearchProviders();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<SearchProvider | null>(null);
  const [apiKey, setApiKey] = useState("");
  const close = () => {
    setEditing(null);
    setApiKey("");
  };
  const save = useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.PUT("/api/v1/search/providers/{provider}/credential", {
          params: { path: { provider: id } },
          body: { api_key: apiKey.trim() },
        }),
      ),
    onSuccess: (saved) => {
      toast.success(`${saved.name} connected`);
      void queryClient.invalidateQueries({ queryKey: keys.searchProviders });
      close();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.DELETE("/api/v1/search/providers/{provider}/credential", { params: { path: { provider: id } } }),
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.searchProviders }),
    onError: (error) => toast.error(describeError(error)),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Web search</CardTitle>
        <CardDescription>
          The bundled SearXNG is free. Paid search APIs can give better results; scholarly search
          (OpenAlex, Semantic Scholar, arXiv, Crossref, PubMed) is always free.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {providers.isPending ? (
          <Skeleton className="h-32" />
        ) : providers.isError ? (
          <ErrorState error={providers.error} retry={() => void providers.refetch()} />
        ) : (
          <ul className="divide-y">
            {providers.data.map((provider) => (
              <li key={provider.id} className="flex flex-wrap items-center gap-3 py-3">
                <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{provider.name}</span>
                  {provider.free && <Badge tone="outline">Free</Badge>}
                  {provider.is_default && <Badge tone="primary">Used by default</Badge>}
                  {provider.configured ? (
                    <AccessBadge source={provider.source} status={provider.status} />
                  ) : (
                    <Badge tone="muted">Not set up</Badge>
                  )}
                  {provider.key_hint && (
                    <span className="font-mono text-xs text-muted-foreground">{provider.key_hint}</span>
                  )}
                </div>
                {provider.workspace_configurable ? (
                  <div className="flex gap-2">
                    <Button size="sm" variant="outline" onClick={() => setEditing(provider)}>
                      <KeyRoundIcon /> {provider.source === "workspace" ? "Replace" : "Add key"}
                    </Button>
                    {provider.source === "workspace" && (
                      <Button size="sm" variant="ghost" onClick={() => remove.mutate(provider.id)}>
                        Remove
                      </Button>
                    )}
                  </div>
                ) : (
                  <span className="text-xs text-muted-foreground">Set by the server operator</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
      <Dialog open={!!editing} onOpenChange={(open) => !open && close()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Connect {editing?.name}</DialogTitle>
            <DialogDescription>The key is tested with one search, then stored encrypted.</DialogDescription>
          </DialogHeader>
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (editing && apiKey.trim()) save.mutate(editing.id);
            }}
          >
            <div className="space-y-2">
              <Label htmlFor="search-key">API key</Label>
              <Input
                id="search-key"
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
              />
              {editing && (
                <a
                  href={editing.docs_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  Get a key <ExternalLinkIcon className="size-3" />
                </a>
              )}
            </div>
            {save.isError && <Callout tone="danger">{describeError(save.error)}</Callout>}
            <DialogFooter>
              <Button variant="ghost" onClick={close}>
                Cancel
              </Button>
              <Button type="submit" disabled={save.isPending || !apiKey.trim()}>
                {save.isPending && <Spinner />} Test and save
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
