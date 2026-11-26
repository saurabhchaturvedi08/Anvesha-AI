"use client";

import type { AccessToken, AccessTokenCreated, McpConnection } from "@anvesha/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckIcon, CopyIcon, PlugIcon, PlusIcon } from "lucide-react";
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
import { FieldHint, Input, Label, Select } from "@/components/ui/input";
import { api, describeError, unwrap } from "@/lib/api";
import { formatDateTime, timeAgo } from "@/lib/format";
import { keys, useAccessTokens, useConnections, useMcpInfo } from "@/lib/queries";

const LOCAL_MCP_URL = "http://localhost:8000/mcp";

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      size="sm"
      variant="outline"
      aria-label={label}
      onClick={() => {
        void navigator.clipboard.writeText(value).then(
          () => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          },
          () => toast.error("Couldn't copy. Select the text and copy it yourself."),
        );
      }}
    >
      {copied ? <CheckIcon /> : <CopyIcon />} {copied ? "Copied" : "Copy"}
    </Button>
  );
}

function Endpoint({ label, url }: { label: string; url: string }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="w-28 shrink-0 text-xs text-muted-foreground">{label}</span>
      <code className="min-w-0 flex-1 truncate rounded bg-muted/60 px-2 py-1 font-mono text-xs">{url}</code>
      <CopyButton value={url} label={`Copy ${label.toLowerCase()}`} />
    </div>
  );
}

function lastUsed(iso: string | null | undefined): string {
  return iso ? `used ${timeAgo(iso)}` : "never used";
}

function expired(token: AccessToken): boolean {
  return !!token.expires_at && new Date(token.expires_at).getTime() <= Date.now();
}

export function McpAccessCard() {
  const info = useMcpInfo();
  const tokens = useAccessTokens();
  const connections = useConnections();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<AccessTokenCreated | null>(null);
  const [name, setName] = useState("");
  const [expiry, setExpiry] = useState("90");

  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/access-tokens", {
          body: { name: name.trim(), expires_in_days: expiry ? Number(expiry) : null },
        }),
      ),
    onSuccess: (token) => {
      setCreated(token);
      void queryClient.invalidateQueries({ queryKey: keys.accessTokens });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/access-tokens/{token_id}", { params: { path: { token_id: id } } })),
    onSuccess: () => {
      toast.success("Token revoked");
      void queryClient.invalidateQueries({ queryKey: keys.accessTokens });
    },
    onError: (error) => toast.error(describeError(error)),
  });
  const disconnect = useMutation({
    mutationFn: (clientId: string) =>
      unwrap(
        api.DELETE("/api/v1/connections/{client_id}", { params: { path: { client_id: clientId } } }),
      ),
    onSuccess: () => {
      toast.success("Disconnected");
      void queryClient.invalidateQueries({ queryKey: keys.connections });
    },
    onError: (error) => toast.error(describeError(error)),
  });

  function closeDialog() {
    setCreating(false);
    setCreated(null);
    setName("");
    setExpiry("90");
    create.reset();
  }

  if (info.data && !info.data.enabled) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Use from AI assistants</CardTitle>
          <CardDescription>
            The MCP server is turned off on this server (ANVESHA_MCP_ENABLED=false).
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Use from AI assistants</CardTitle>
        <CardDescription>
          Anvesha is an MCP server. Claude, ChatGPT, Cursor, Gemini CLI and other assistants can start
          research, read reports, search your evidence and check quotes with it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <section className="space-y-2" aria-labelledby="mcp-endpoints">
          <h4 id="mcp-endpoints" className="text-sm font-medium">
            Server address
          </h4>
          <Endpoint label="On this computer" url={LOCAL_MCP_URL} />
          {info.data?.public_url ? (
            <Endpoint label="On the internet" url={info.data.public_url} />
          ) : (
            <FieldHint>
              To connect claude.ai or ChatGPT, expose the public MCP service (it serves only MCP and
              sign-in) and set ANVESHA_MCP_PUBLIC_URL. See the README.
            </FieldHint>
          )}
        </section>

        <section className="space-y-3" aria-labelledby="mcp-tokens">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h4 id="mcp-tokens" className="text-sm font-medium">
                Access tokens
              </h4>
              <p className="text-xs text-muted-foreground">
                For clients you configure yourself (Claude Code, Cursor, Gemini CLI). Sent as{" "}
                <code className="font-mono">Authorization: Bearer …</code>
              </p>
            </div>
            <Button size="sm" variant="outline" onClick={() => setCreating(true)}>
              <PlusIcon /> New token
            </Button>
          </div>
          {tokens.isPending ? (
            <Skeleton className="h-16" />
          ) : tokens.isError ? (
            <ErrorState error={tokens.error} retry={() => void tokens.refetch()} />
          ) : tokens.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">No tokens yet.</p>
          ) : (
            <ul className="divide-y rounded-md border">
              {tokens.data.map((token) => (
                <li key={token.id} className="flex flex-wrap items-center gap-3 px-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">{token.name}</span>
                      <span className="font-mono text-xs text-muted-foreground">{token.prefix}…</span>
                      {expired(token) && <Badge tone="danger">Expired</Badge>}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Created {formatDateTime(token.created_at)} · {lastUsed(token.last_used_at)}
                      {token.expires_at && !expired(token) && ` · expires ${timeAgo(token.expires_at)}`}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={revoke.isPending && revoke.variables === token.id}
                    onClick={() => revoke.mutate(token.id)}
                  >
                    Revoke
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {(info.data?.oauth_enabled || (connections.data?.length ?? 0) > 0) && (
          <section className="space-y-3" aria-labelledby="mcp-connections">
            <div>
              <h4 id="mcp-connections" className="text-sm font-medium">
                Connected apps
              </h4>
              <p className="text-xs text-muted-foreground">
                Apps approved through sign-in, such as claude.ai and ChatGPT connectors.
              </p>
            </div>
            {connections.isPending ? (
              <Skeleton className="h-16" />
            ) : connections.isError ? (
              <ErrorState error={connections.error} retry={() => void connections.refetch()} />
            ) : connections.data.length === 0 ? (
              <p className="text-sm text-muted-foreground">No apps connected yet.</p>
            ) : (
              <ul className="divide-y rounded-md border">
                {connections.data.map((connection: McpConnection) => (
                  <li key={connection.client_id} className="flex flex-wrap items-center gap-3 px-3 py-2.5">
                    <PlugIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium">
                          {connection.client_name ?? "Unnamed app"}
                        </span>
                        {connection.redirect_hosts.map((host) => (
                          <Badge key={host} tone="outline">
                            {host}
                          </Badge>
                        ))}
                      </div>
                      <p className="text-xs text-muted-foreground">
                        Connected {formatDateTime(connection.connected_at)} ·{" "}
                        {lastUsed(connection.last_used_at)}
                      </p>
                    </div>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={disconnect.isPending && disconnect.variables === connection.client_id}
                      onClick={() => disconnect.mutate(connection.client_id)}
                    >
                      Disconnect
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
      </CardContent>

      <Dialog open={creating} onOpenChange={(open) => !open && closeDialog()}>
        <DialogContent>
          {created ? (
            <>
              <DialogHeader>
                <DialogTitle>Copy your token now</DialogTitle>
                <DialogDescription>
                  It won&apos;t be shown again. Anyone with it can use Anvesha as you, so treat it like a
                  password.
                </DialogDescription>
              </DialogHeader>
              <div className="flex items-center gap-2">
                <code className="min-w-0 flex-1 break-all rounded bg-muted/60 px-2 py-2 font-mono text-xs">
                  {created.token}
                </code>
                <CopyButton value={created.token} label="Copy token" />
              </div>
              <div className="space-y-1">
                <p className="text-xs font-medium">Claude Code</p>
                <code className="block break-all rounded bg-muted/60 px-2 py-2 font-mono text-xs">
                  {`claude mcp add --transport http anvesha ${LOCAL_MCP_URL} --header "Authorization: Bearer ${created.token}"`}
                </code>
              </div>
              <DialogFooter>
                <Button onClick={closeDialog}>Done</Button>
              </DialogFooter>
            </>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>New access token</DialogTitle>
                <DialogDescription>Name it after the app or computer that will use it.</DialogDescription>
              </DialogHeader>
              <form
                className="space-y-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (name.trim()) create.mutate();
                }}
              >
                <div className="space-y-2">
                  <Label htmlFor="token-name">Name</Label>
                  <Input
                    id="token-name"
                    maxLength={100}
                    placeholder="Claude Code on my laptop"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    autoFocus
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="token-expiry">Expires</Label>
                  <Select id="token-expiry" value={expiry} onChange={(event) => setExpiry(event.target.value)}>
                    <option value="30">In 30 days</option>
                    <option value="90">In 90 days</option>
                    <option value="365">In a year</option>
                    <option value="">Never</option>
                  </Select>
                </div>
                {create.isError && <Callout tone="danger">{describeError(create.error)}</Callout>}
                <DialogFooter>
                  <Button variant="ghost" onClick={closeDialog}>
                    Cancel
                  </Button>
                  <Button type="submit" disabled={create.isPending || !name.trim()}>
                    {create.isPending && <Spinner />} Create token
                  </Button>
                </DialogFooter>
              </form>
            </>
          )}
        </DialogContent>
      </Dialog>
    </Card>
  );
}
