import type { Metadata } from "next";

import { McpAccessCard } from "@/components/settings/mcp";
import { ModelRolesCard } from "@/components/settings/models";
import { ModelProvidersCard, SearchProvidersCard } from "@/components/settings/providers";
import { UsageCard } from "@/components/settings/usage";
import { MemberNotice, WorkspaceCard } from "@/components/settings/workspace";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Manage your workspace, connect models and search, choose a model for each agent, use Anvesha
          from AI assistants, and see what research costs.
        </p>
      </div>
      <WorkspaceCard />
      <MemberNotice />
      <ModelProvidersCard />
      <ModelRolesCard />
      <SearchProvidersCard />
      <McpAccessCard />
      <UsageCard />
    </div>
  );
}
