"use client";

import type { InvitationCreated, WorkspaceMember, WorkspaceRole } from "@anvesha/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CopyIcon, MailPlusIcon } from "lucide-react";
import { useRouter } from "next/navigation";
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
import { formatDateTime } from "@/lib/format";
import { keys, useInvitations, useMe, useMembers } from "@/lib/queries";
import { setWorkspaceCookie } from "@/lib/workspace";

const ROLE_LABELS: Record<WorkspaceRole, string> = { owner: "Owner", admin: "Admin", member: "Member" };

export function WorkspaceCard() {
  const me = useMe();
  if (!me.data) return me.isPending ? <Skeleton className="h-40" /> : null;
  // Single-user installs have only the personal workspace: nothing to manage here.
  if (me.data.auth_mode !== "jwt" && me.data.workspace.personal) return null;
  return <WorkspaceSettings />;
}

function WorkspaceSettings() {
  const me = useMe().data!;
  const { workspace } = me;
  const canManage = workspace.role !== "member";
  const isOwner = workspace.role === "owner";
  const members = useMembers(!workspace.personal);
  const invitations = useInvitations(canManage && !workspace.personal);
  const queryClient = useQueryClient();
  const router = useRouter();
  const [name, setName] = useState(workspace.name);
  const [inviting, setInviting] = useState(false);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: keys.me });
    void queryClient.invalidateQueries({ queryKey: keys.members });
    void queryClient.invalidateQueries({ queryKey: keys.invitations });
  };
  const leaveWorkspace = () => {
    setWorkspaceCookie(null);
    queryClient.clear();
    router.push("/");
    router.refresh();
  };

  const rename = useMutation({
    mutationFn: () => unwrap(api.PATCH("/api/v1/workspace", { body: { name: name.trim() } })),
    onSuccess: () => {
      toast.success("Workspace renamed");
      refresh();
    },
    onError: (error) => toast.error(describeError(error)),
  });
  const changeRole = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: WorkspaceRole }) =>
      unwrap(
        api.PATCH("/api/v1/workspace/members/{user_id}", {
          params: { path: { user_id: userId } },
          body: { role },
        }),
      ),
    onSuccess: () => refresh(),
    onError: (error) => toast.error(describeError(error)),
  });
  const removeMember = useMutation({
    mutationFn: (userId: string) =>
      unwrap(api.DELETE("/api/v1/workspace/members/{user_id}", { params: { path: { user_id: userId } } })),
    onSuccess: (_, userId) => {
      if (userId === me.user.id) {
        toast.success(`You left ${workspace.name}`);
        leaveWorkspace();
      } else {
        toast.success("Member removed");
        refresh();
      }
    },
    onError: (error) => toast.error(describeError(error)),
  });
  const revoke = useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.DELETE("/api/v1/workspace/invitations/{invitation_id}", { params: { path: { invitation_id: id } } }),
      ),
    onSuccess: () => refresh(),
    onError: (error) => toast.error(describeError(error)),
  });
  const remove = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/workspace")),
    onSuccess: () => {
      toast.success(`Deleted ${workspace.name}`);
      leaveWorkspace();
    },
    onError: (error) => toast.error(describeError(error)),
  });

  if (workspace.personal) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Workspace</CardTitle>
          <CardDescription>
            This is your personal workspace: only you can see it. To research with others, create a
            team workspace from the workspace menu at the top, then invite people.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Workspace</CardTitle>
        <CardDescription>
          Everyone here shares the projects, model keys and models. Members research; admins also
          manage keys, models and people; owners can delete the workspace.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {canManage && (
          <form
            className="flex items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (name.trim() && name.trim() !== workspace.name) rename.mutate();
            }}
          >
            <div className="flex-1 space-y-1.5">
              <Label htmlFor="ws-name">Name</Label>
              <Input id="ws-name" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <Button type="submit" variant="outline" disabled={rename.isPending || name.trim() === workspace.name}>
              Save
            </Button>
          </form>
        )}

        <section className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-sm font-medium">Members</h4>
            {canManage && (
              <Button size="sm" variant="outline" onClick={() => setInviting(true)}>
                <MailPlusIcon /> Invite
              </Button>
            )}
          </div>
          {members.isPending ? (
            <Skeleton className="h-24" />
          ) : members.isError ? (
            <ErrorState error={members.error} retry={() => void members.refetch()} />
          ) : (
            <ul className="divide-y rounded-md border">
              {members.data.map((member) => (
                <MemberRow
                  key={member.user.id}
                  member={member}
                  isSelf={member.user.id === me.user.id}
                  canManage={canManage}
                  isOwner={isOwner}
                  busy={changeRole.isPending || removeMember.isPending}
                  onRole={(role) => changeRole.mutate({ userId: member.user.id, role })}
                  onRemove={() => removeMember.mutate(member.user.id)}
                />
              ))}
            </ul>
          )}
        </section>

        {canManage && (invitations.data?.length ?? 0) > 0 && (
          <section className="space-y-2">
            <h4 className="text-sm font-medium">Pending invitations</h4>
            <ul className="divide-y rounded-md border">
              {invitations.data!.map((invitation) => (
                <li key={invitation.id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
                  <span className="min-w-0 flex-1 truncate">{invitation.email || "Anyone with the link"}</span>
                  <Badge tone="muted">{ROLE_LABELS[invitation.role]}</Badge>
                  <span className="text-xs text-muted-foreground">expires {formatDateTime(invitation.expires_at)}</span>
                  <Button size="sm" variant="ghost" disabled={revoke.isPending} onClick={() => revoke.mutate(invitation.id)}>
                    Revoke
                  </Button>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="flex flex-wrap gap-2 border-t pt-4">
          <Button
            variant="outline"
            size="sm"
            disabled={removeMember.isPending}
            onClick={() => {
              if (window.confirm(`Leave ${workspace.name}? You'll need a new invitation to come back.`)) {
                removeMember.mutate(me.user.id);
              }
            }}
          >
            Leave workspace
          </Button>
          {isOwner && (
            <Button
              variant="destructive"
              size="sm"
              disabled={remove.isPending}
              onClick={() => {
                if (
                  window.confirm(
                    `Delete ${workspace.name} with all its projects, sources, reports and keys? This can't be undone.`,
                  )
                ) {
                  remove.mutate();
                }
              }}
            >
              Delete workspace
            </Button>
          )}
        </section>
      </CardContent>
      {inviting && <InviteDialog onClose={() => setInviting(false)} onCreated={refresh} />}
    </Card>
  );
}

function MemberRow({
  member,
  isSelf,
  canManage,
  isOwner,
  busy,
  onRole,
  onRemove,
}: {
  member: WorkspaceMember;
  isSelf: boolean;
  canManage: boolean;
  isOwner: boolean;
  busy: boolean;
  onRole: (role: WorkspaceRole) => void;
  onRemove: () => void;
}) {
  // Admins manage members and admins; only owners touch owners.
  const editable = canManage && !isSelf && (isOwner || member.role !== "owner");
  return (
    <li className="flex flex-wrap items-center gap-3 px-3 py-2">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">
          {member.user.display_name || member.user.email}
          {isSelf && <span className="ml-1 font-normal text-muted-foreground">(you)</span>}
        </p>
        {member.user.display_name && (
          <p className="truncate text-xs text-muted-foreground">{member.user.email}</p>
        )}
      </div>
      {editable ? (
        <>
          <Select
            aria-label={`Role of ${member.user.email}`}
            className="h-8 w-28"
            value={member.role}
            disabled={busy}
            onChange={(event) => onRole(event.target.value as WorkspaceRole)}
          >
            <option value="member">Member</option>
            <option value="admin">Admin</option>
            {isOwner && <option value="owner">Owner</option>}
          </Select>
          <Button size="sm" variant="ghost" disabled={busy} onClick={onRemove}>
            Remove
          </Button>
        </>
      ) : (
        <Badge tone={member.role === "owner" ? "primary" : "muted"}>{ROLE_LABELS[member.role]}</Badge>
      )}
    </li>
  );
}

function InviteDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"member" | "admin">("member");
  const [created, setCreated] = useState<InvitationCreated | null>(null);
  const invite = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/workspace/invitations", { body: { email: email.trim() || null, role } })),
    onSuccess: (invitation) => {
      setCreated(invitation);
      onCreated();
    },
    onError: (error) => toast.error(describeError(error)),
  });

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invite someone</DialogTitle>
          <DialogDescription>
            You get a link to send them. Whoever opens it while signed in can join, so share it only
            with the person you mean. Links work once and expire after 7 days.
          </DialogDescription>
        </DialogHeader>
        {created ? (
          <div className="space-y-3">
            <Callout tone="info" title="Copy the link now">
              It won&apos;t be shown again.
            </Callout>
            <div className="flex gap-2">
              <Input readOnly value={created.url} onFocus={(event) => event.currentTarget.select()} />
              <Button
                variant="outline"
                size="icon"
                aria-label="Copy link"
                onClick={() =>
                  void navigator.clipboard.writeText(created.url).then(() => toast.success("Link copied"))
                }
              >
                <CopyIcon />
              </Button>
            </div>
            <DialogFooter>
              <Button onClick={onClose}>Done</Button>
            </DialogFooter>
          </div>
        ) : (
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              invite.mutate();
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="invite-email">Email (optional)</Label>
              <Input
                id="invite-email"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="colleague@example.org"
              />
              <FieldHint>Shown on the invitation so they know it&apos;s meant for them.</FieldHint>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invite-role">Role</Label>
              <Select
                id="invite-role"
                value={role}
                onChange={(event) => setRole(event.target.value as "member" | "admin")}
              >
                <option value="member">Member: researches in the workspace</option>
                <option value="admin">Admin: also manages keys, models and people</option>
              </Select>
            </div>
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" disabled={invite.isPending}>
                {invite.isPending && <Spinner />}
                Create link
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Tells members why the key and model controls below are read-only. */
export function MemberNotice() {
  const me = useMe();
  if (me.data?.workspace.role !== "member") return null;
  return (
    <Callout tone="info" title="Managed by this workspace's admins">
      You can research with the models and keys set up here. Ask an admin or owner to change them.
    </Callout>
  );
}
