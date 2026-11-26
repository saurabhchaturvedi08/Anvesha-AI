"use client";

import type { ModelRole, RunStatus } from "@anvesha/api-client";
import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api";

export const ACTIVE_RUN_STATUSES: RunStatus[] = ["queued", "running", "awaiting_approval"];
export const AGENT_ROLES: ModelRole[] = ["lead", "researcher", "verifier", "writer"];

export function isActive(status: RunStatus | null | undefined): boolean {
  return !!status && ACTIVE_RUN_STATUSES.includes(status);
}

export const keys = {
  config: ["config"] as const,
  projects: ["projects"] as const,
  project: (id: string) => ["project", id] as const,
  runs: (projectId: string) => ["runs", projectId] as const,
  run: (runId: string) => ["run", runId] as const,
  findings: (runId: string) => ["findings", runId] as const,
  reports: (projectId: string) => ["reports", projectId] as const,
  report: (reportId: string) => ["report", reportId] as const,
  sources: (projectId: string) => ["sources", projectId] as const,
  source: (projectId: string, sourceId: string) => ["source", projectId, sourceId] as const,
  providers: ["providers"] as const,
  models: (provider: string) => ["models", provider] as const,
  modelPreferences: ["model-preferences"] as const,
  searchProviders: ["search-providers"] as const,
  usage: (days: number) => ["usage", days] as const,
  chats: (projectId: string) => ["chats", projectId] as const,
  chat: (threadId: string) => ["chat", threadId] as const,
  accessTokens: ["access-tokens"] as const,
  connections: ["connections"] as const,
  mcpInfo: ["mcp-info"] as const,
};

export function useServerConfig() {
  return useQuery({
    queryKey: keys.config,
    queryFn: () => unwrap(api.GET("/api/v1/config")),
    staleTime: 10 * 60 * 1000,
  });
}

export function useProjects() {
  return useQuery({
    queryKey: keys.projects,
    queryFn: () => unwrap(api.GET("/api/v1/projects", { params: { query: { limit: 100 } } })),
    refetchInterval: (query) =>
      query.state.data?.some((project) => isActive(project.latest_run?.status)) ? 5000 : false,
  });
}

export function useProject(projectId: string) {
  return useQuery({
    queryKey: keys.project(projectId),
    queryFn: () =>
      unwrap(api.GET("/api/v1/projects/{project_id}", { params: { path: { project_id: projectId } } })),
    refetchInterval: (query) => (isActive(query.state.data?.latest_run?.status) ? 10_000 : false),
  });
}

export function useRuns(projectId: string) {
  return useQuery({
    queryKey: keys.runs(projectId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/runs", {
          params: { path: { project_id: projectId }, query: { limit: 50 } },
        }),
      ),
  });
}

export function useRun(runId: string | null | undefined) {
  return useQuery({
    queryKey: keys.run(runId ?? ""),
    queryFn: () => unwrap(api.GET("/api/v1/runs/{run_id}", { params: { path: { run_id: runId! } } })),
    enabled: !!runId,
    // Live events trigger refetches; polling is only a safety net.
    refetchInterval: (query) => (isActive(query.state.data?.status) ? 15_000 : false),
  });
}

export function useFindings(runId: string | null | undefined) {
  return useQuery({
    queryKey: keys.findings(runId ?? ""),
    queryFn: () =>
      unwrap(api.GET("/api/v1/runs/{run_id}/findings", { params: { path: { run_id: runId! } } })),
    enabled: !!runId,
  });
}

export function useReports(projectId: string) {
  return useQuery({
    queryKey: keys.reports(projectId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/reports", {
          params: { path: { project_id: projectId } },
        }),
      ),
  });
}

export function useReport(reportId: string | null | undefined) {
  return useQuery({
    queryKey: keys.report(reportId ?? ""),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/reports/{report_id}", { params: { path: { report_id: reportId! } } }),
      ),
    enabled: !!reportId,
    staleTime: Infinity, // reports don't change once written
  });
}

export function useSources(projectId: string) {
  return useQuery({
    queryKey: keys.sources(projectId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/sources", {
          params: { path: { project_id: projectId }, query: { limit: 200 } },
        }),
      ),
  });
}

export function useSource(projectId: string, sourceId: string) {
  return useQuery({
    queryKey: keys.source(projectId, sourceId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/sources/{source_id}", {
          params: { path: { project_id: projectId, source_id: sourceId } },
        }),
      ),
  });
}

export function useProviders() {
  return useQuery({
    queryKey: keys.providers,
    queryFn: () => unwrap(api.GET("/api/v1/providers")),
  });
}

export function useProviderModels(provider: string | null, enabled = true) {
  return useQuery({
    queryKey: keys.models(provider ?? ""),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/providers/{provider}/models", {
          params: { path: { provider: provider! } },
        }),
      ),
    enabled: !!provider && enabled,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

export function useModelPreferences() {
  return useQuery({
    queryKey: keys.modelPreferences,
    queryFn: () => unwrap(api.GET("/api/v1/model-preferences")),
  });
}

export function useSearchProviders() {
  return useQuery({
    queryKey: keys.searchProviders,
    queryFn: () => unwrap(api.GET("/api/v1/search/providers")),
  });
}

export function useUsage(days: number) {
  return useQuery({
    queryKey: keys.usage(days),
    queryFn: () => unwrap(api.GET("/api/v1/usage", { params: { query: { days } } })),
  });
}

export function useAccessTokens() {
  return useQuery({
    queryKey: keys.accessTokens,
    queryFn: () => unwrap(api.GET("/api/v1/access-tokens")),
  });
}

export function useConnections() {
  return useQuery({
    queryKey: keys.connections,
    queryFn: () => unwrap(api.GET("/api/v1/connections")),
  });
}

export function useMcpInfo() {
  return useQuery({
    queryKey: keys.mcpInfo,
    queryFn: () => unwrap(api.GET("/api/v1/connections/mcp")),
    staleTime: 10 * 60 * 1000,
  });
}

export function useChats(projectId: string) {
  return useQuery({
    queryKey: keys.chats(projectId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/chats", {
          params: { path: { project_id: projectId } },
        }),
      ),
  });
}

export function useChat(threadId: string | null) {
  return useQuery({
    queryKey: keys.chat(threadId ?? ""),
    queryFn: () =>
      unwrap(api.GET("/api/v1/chats/{thread_id}", { params: { path: { thread_id: threadId! } } })),
    enabled: !!threadId,
  });
}

/** Whether every agent role has a model, i.e. research can start. */
export function useSetupStatus() {
  const preferences = useModelPreferences();
  const missing = (preferences.data ?? [])
    .filter((item) => AGENT_ROLES.includes(item.role) && !item.model)
    .map((item) => item.role);
  return {
    isLoading: preferences.isLoading,
    ready: !!preferences.data && missing.length === 0,
    missing,
  };
}
