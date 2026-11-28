"use client";

import type { Workspace } from "@anvesha/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckIcon, ChevronsUpDownIcon, LogOutIcon, PlusIcon, UserIcon, UsersIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/feedback";
import { Input, Label } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { api, describeError, unwrap } from "@/lib/api";
import { authClient } from "@/lib/auth-client";
import { useMe } from "@/lib/queries";
import { setWorkspaceCookie } from "@/lib/workspace";

/** Everything cached belongs to the old workspace: drop it and start from the projects. */
function useSwitchWorkspace() {
  const queryClient = useQueryClient();
  const router = useRouter();
  return (workspace: Workspace | { id: string }) => {
    setWorkspaceCookie(workspace.id);
    queryClient.clear();
    router.push("/");
    router.refresh();
  };
}

export function WorkspaceSwitcher() {
  const me = useMe();
  const switchTo = useSwitchWorkspace();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  if (!me.data) return null;
  const { workspace, workspaces } = me.data;
  // Single-user installs have one workspace and nobody to share with.
  if (me.data.auth_mode !== "jwt" && workspaces.length < 2) return null;

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button variant="ghost" size="sm" className="max-w-48 gap-1.5" aria-label="Switch workspace">
            {workspace.personal ? <UserIcon /> : <UsersIcon />}
            <span className="truncate">{workspace.personal ? "Personal" : workspace.name}</span>
            <ChevronsUpDownIcon className="size-3.5 opacity-60" />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-64 p-1">
          <p className="px-2 py-1.5 text-xs font-medium text-muted-foreground">Workspaces</p>
          <ul>
            {workspaces.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted"
                  onClick={() => {
                    setOpen(false);
                    if (item.id !== workspace.id) switchTo(item);
                  }}
                >
                  {item.personal ? <UserIcon className="size-4" /> : <UsersIcon className="size-4" />}
                  <span className="min-w-0 flex-1 truncate">{item.personal ? "Personal" : item.name}</span>
                  {!item.personal && (
                    <span className="text-xs text-muted-foreground">{item.members}</span>
                  )}
                  {item.id === workspace.id && <CheckIcon className="size-4" />}
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-1 border-t pt-1">
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted"
              onClick={() => {
                setOpen(false);
                setCreating(true);
              }}
            >
              <PlusIcon className="size-4" /> New team workspace
            </button>
          </div>
        </PopoverContent>
      </Popover>
      {creating && <CreateWorkspaceDialog onClose={() => setCreating(false)} onCreated={switchTo} />}
    </>
  );
}

function CreateWorkspaceDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (workspace: Workspace) => void;
}) {
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/workspaces", { body: { name: name.trim() } })),
    onSuccess: (workspace) => {
      toast.success(`Created ${workspace.name}`, {
        description: "Invite people from Settings → Workspace.",
      });
      onClose();
      onCreated(workspace);
    },
    onError: (error) => toast.error(describeError(error)),
  });
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New team workspace</DialogTitle>
          <DialogDescription>
            A shared place for projects, model keys and research. You&apos;ll be its owner and can
            invite others.
          </DialogDescription>
        </DialogHeader>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (name.trim()) create.mutate();
          }}
          className="space-y-4"
        >
          <div className="space-y-1.5">
            <Label htmlFor="workspace-name">Name</Label>
            <Input
              id="workspace-name"
              autoFocus
              maxLength={200}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Climate lab"
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim() || create.isPending}>
              {create.isPending && <Spinner />}
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function AccountMenu() {
  const me = useMe();
  const router = useRouter();
  const queryClient = useQueryClient();
  if (me.data?.auth_mode !== "jwt") return null;
  const { user } = me.data;

  async function signOut() {
    await authClient.signOut();
    setWorkspaceCookie(null);
    queryClient.clear();
    router.replace("/sign-in");
    router.refresh();
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Account">
          <span className="grid size-7 place-items-center rounded-full bg-muted text-xs font-semibold uppercase">
            {(user.display_name || user.email).slice(0, 1)}
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-60 p-1">
        <div className="px-2 py-1.5">
          {user.display_name && <p className="truncate text-sm font-medium">{user.display_name}</p>}
          <p className="truncate text-xs text-muted-foreground">{user.email}</p>
        </div>
        <div className="mt-1 border-t pt-1">
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted"
            onClick={() => void signOut()}
          >
            <LogOutIcon className="size-4" /> Sign out
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
