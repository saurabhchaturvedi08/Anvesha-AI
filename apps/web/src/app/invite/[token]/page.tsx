"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UsersIcon } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState, Skeleton, Spinner } from "@/components/ui/feedback";
import { api, describeError, unwrap } from "@/lib/api";
import { setWorkspaceCookie } from "@/lib/workspace";

export default function InvitationPage() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const preview = useQuery({
    queryKey: ["invitation", token],
    queryFn: () => unwrap(api.GET("/api/v1/invitations/{token}", { params: { path: { token } } })),
    retry: false,
  });
  const accept = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/invitations/{token}/accept", { params: { path: { token } } })),
    onSuccess: (workspace) => {
      toast.success(`You joined ${workspace.name}`);
      setWorkspaceCookie(workspace.id);
      queryClient.clear();
      router.replace("/");
      router.refresh();
    },
    onError: (error) => toast.error(describeError(error)),
  });

  return (
    <div className="py-10">
      <Card className="mx-auto w-full max-w-md">
        {preview.isPending ? (
          <CardContent className="py-6">
            <Skeleton className="h-24" />
          </CardContent>
        ) : preview.isError ? (
          <CardContent className="space-y-4 py-6">
            <ErrorState error={preview.error} />
            <Button asChild variant="outline">
              <Link href="/">Go to your projects</Link>
            </Button>
          </CardContent>
        ) : (
          <>
            <CardHeader>
              <span className="mb-2 grid size-10 place-items-center rounded-full bg-primary/10 text-primary">
                <UsersIcon className="size-5" />
              </span>
              <CardTitle>Join {preview.data.workspace_name}</CardTitle>
              <CardDescription>
                {preview.data.invited_by ? `${preview.data.invited_by} invited you` : "You were invited"}{" "}
                as {preview.data.role === "admin" ? "an admin" : "a member"}
                {preview.data.email ? ` (${preview.data.email})` : ""}. You&apos;ll see the workspace&apos;s
                projects and can research with its model keys.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex gap-2">
              {preview.data.already_member ? (
                <Button asChild>
                  <Link href="/">You&apos;re already a member</Link>
                </Button>
              ) : (
                <>
                  <Button disabled={accept.isPending} onClick={() => accept.mutate()}>
                    {accept.isPending && <Spinner />}
                    Join workspace
                  </Button>
                  <Button asChild variant="ghost">
                    <Link href="/">Not now</Link>
                  </Button>
                </>
              )}
            </CardContent>
          </>
        )}
      </Card>
    </div>
  );
}
