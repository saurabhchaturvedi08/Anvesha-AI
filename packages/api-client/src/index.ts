import createClient, { type Client, type ClientOptions } from "openapi-fetch";

import type { components, paths } from "./schema";

export type { components, paths };
export type Schemas = components["schemas"];

export type ServerConfig = Schemas["ServerConfig"];
export type Depth = Schemas["ProjectCreate"]["depth"] & string;
export type Project = Schemas["ProjectRead"];
export type ProjectRunBrief = Schemas["ProjectRunBrief"];
export type Run = Schemas["RunRead"];
export type RunDetail = Schemas["RunDetail"];
export type RunStatus = Run["status"];
export type RunEvent = Schemas["RunEventRead"];
export type SubQuestion = Schemas["SubQuestionRead"];
export type Finding = Schemas["FindingRead"];
export type Report = Schemas["ReportRead"];
export type ReportSummary = Schemas["ReportSummary"];
export type Source = Schemas["SourceRead"];
export type SourceText = Schemas["SourceText"];
export type Provider = Schemas["ProviderRead"];
export type SearchProvider = Schemas["SearchProviderRead"];
export type ModelPreference = Schemas["ModelPreferenceRead"];
export type ModelRole = ModelPreference["role"];
export type UsageSummary = Schemas["UsageSummary"];
export type ChatThread = Schemas["ChatThreadRead"];
export type ChatThreadDetail = Schemas["ChatThreadDetail"];
export type ChatMessage = Schemas["ChatMessageRead"];
export type ChatCitation = Schemas["ChatCitation"];
export type ClarifyResponse = Schemas["ClarifyResponse"];
export type AccessToken = Schemas["AccessTokenRead"];
export type AccessTokenCreated = Schemas["AccessTokenCreated"];
export type McpConnection = Schemas["ConnectionRead"];
export type McpInfo = Schemas["McpInfo"];
export type Me = Schemas["MeRead"];
export type Workspace = Schemas["WorkspaceRead"];
export type WorkspaceRole = Workspace["role"];
export type WorkspaceMember = Schemas["MemberRead"];
export type Invitation = Schemas["InvitationRead"];
export type InvitationCreated = Schemas["InvitationCreated"];
export type InvitationPreview = Schemas["InvitationPreview"];
export type ConsentDetails = Schemas["ConsentDetails"];

export type ApiClient = Client<paths>;

/** A client for the Anvesha API. An empty base URL calls the same origin. */
export function createApiClient(options: ClientOptions = {}): ApiClient {
  return createClient<paths>({ baseUrl: "", ...options });
}
