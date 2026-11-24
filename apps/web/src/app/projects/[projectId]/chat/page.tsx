"use client";

import type { ChatCitation, ChatMessage, ChatThreadDetail } from "@anvesha/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { MessageSquarePlusIcon, MessagesSquareIcon, SendHorizontalIcon, Trash2Icon } from "lucide-react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import type { CitationInfo } from "@/components/citation-chip";
import { Markdown } from "@/components/markdown";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Callout, ErrorState, Skeleton, Spinner } from "@/components/ui/feedback";
import { Textarea } from "@/components/ui/input";
import { api, ApiError, describeError, unwrap } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { keys, useChat, useChats, useReports } from "@/lib/queries";
import { useReplyStream } from "@/lib/use-reply-stream";
import { cn } from "@/lib/utils";

const SUGGESTIONS = [
  "Summarise the main findings in a few bullet points.",
  "Where do the sources disagree, and why?",
  "What are the biggest gaps in the evidence?",
];

export default function ChatPage() {
  return (
    <Suspense fallback={<Skeleton className="h-[60vh]" />}>
      <ChatView />
    </Suspense>
  );
}

function ChatView() {
  const { projectId } = useParams<{ projectId: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const threads = useChats(projectId);
  const reports = useReports(projectId);
  const threadId = searchParams.get("thread");
  const select = (id: string | null) =>
    router.replace(id ? `/projects/${projectId}/chat?thread=${id}` : `/projects/${projectId}/chat`);

  const remove = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/chats/{thread_id}", { params: { path: { thread_id: id } } })),
    onSuccess: (_, id) => {
      queryClient.removeQueries({ queryKey: keys.chat(id) });
      void queryClient.invalidateQueries({ queryKey: keys.chats(projectId) });
      if (id === threadId) select(null);
    },
    onError: (error) => toast.error(describeError(error)),
  });

  return (
    <div className="grid gap-4 md:grid-cols-[15rem_minmax(0,1fr)]">
      <aside className="space-y-2" aria-label="Conversations">
        <Button variant="outline" className="w-full justify-start" onClick={() => select(null)}>
          <MessageSquarePlusIcon /> New chat
        </Button>
        {threads.isPending ? (
          <Skeleton className="h-24" />
        ) : threads.isError ? (
          <ErrorState error={threads.error} retry={() => void threads.refetch()} />
        ) : (
          <ul className="space-y-0.5">
            {threads.data.map((thread) => (
              <li key={thread.id} className="group flex items-center">
                <button
                  type="button"
                  onClick={() => select(thread.id)}
                  aria-current={thread.id === threadId ? "page" : undefined}
                  className={cn(
                    "min-w-0 flex-1 rounded-md px-3 py-2 text-left text-sm hover:bg-muted",
                    thread.id === threadId && "bg-muted font-medium",
                  )}
                >
                  <span className="line-clamp-1">{thread.title}</span>
                  <span className="text-xs font-normal text-muted-foreground">{timeAgo(thread.updated_at)}</span>
                </button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                  aria-label={`Delete chat ${thread.title}`}
                  onClick={() => remove.mutate(thread.id)}
                >
                  <Trash2Icon />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <Card className="flex min-h-[60vh] flex-col">
        <Conversation
          key={threadId ?? "new"}
          projectId={projectId}
          threadId={threadId}
          onCreated={select}
          hasReport={(reports.data?.length ?? 0) > 0}
        />
      </Card>
    </div>
  );
}

function Conversation({
  projectId,
  threadId,
  onCreated,
  hasReport,
}: {
  projectId: string;
  threadId: string | null;
  onCreated: (id: string) => void;
  hasReport: boolean;
}) {
  const queryClient = useQueryClient();
  const chat = useChat(threadId);
  const [draft, setDraft] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const messages = chat.data?.messages ?? [];
  const pending = messages.find((m) => m.status === "pending") ?? null;

  const send = useMutation({
    mutationFn: async (content: string) => {
      let id = threadId;
      if (!id) {
        const thread = await unwrap(
          api.POST("/api/v1/projects/{project_id}/chats", {
            params: { path: { project_id: projectId } },
            body: {},
          }),
        );
        id = thread.id;
      }
      const exchange = await unwrap(
        api.POST("/api/v1/chats/{thread_id}/messages", {
          params: { path: { thread_id: id } },
          body: { content },
        }),
      );
      return { id, exchange };
    },
    onSuccess: ({ id, exchange }) => {
      setDraft("");
      queryClient.setQueryData<ChatThreadDetail>(keys.chat(id), (current) =>
        current
          ? { ...current, messages: [...current.messages, exchange.user_message, exchange.assistant_message] }
          : current,
      );
      void queryClient.invalidateQueries({ queryKey: keys.chats(projectId) });
      if (id !== threadId) onCreated(id);
      else void queryClient.invalidateQueries({ queryKey: keys.chat(id) });
    },
  });

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages.length, pending?.id]);

  const submit = (content: string) => {
    const text = content.trim();
    if (text && !send.isPending && !pending) send.mutate(text);
  };

  return (
    <>
      <div className="flex-1 space-y-6 overflow-y-auto p-4 sm:p-6">
        {threadId && chat.isPending ? (
          <Skeleton className="h-32" />
        ) : chat.isError ? (
          <ErrorState error={chat.error} retry={() => void chat.refetch()} />
        ) : messages.length === 0 ? (
          <div className="mx-auto flex max-w-lg flex-col items-center gap-4 py-10 text-center">
            <div className="rounded-full bg-muted p-3">
              <MessagesSquareIcon className="size-6 text-muted-foreground" />
            </div>
            <div className="space-y-1">
              <p className="font-medium">Ask about this research</p>
              <p className="text-sm text-muted-foreground">
                The assistant answers from the latest report&apos;s findings and the project&apos;s saved
                sources, and cites quotes it has checked. It doesn&apos;t browse the web: start a new
                run for that.
              </p>
            </div>
            {!hasReport && (
              <Callout tone="info" className="text-left">
                There&apos;s no report yet, so answers can only use sources you&apos;ve saved.
              </Callout>
            )}
            <div className="flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => submit(suggestion)}
                  className="rounded-full border px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((message, index) => (
            <MessageView
              key={message.id}
              message={message}
              projectId={projectId}
              threadId={threadId!}
              onRetry={() => {
                const question = messages.slice(0, index).reverse().find((m) => m.role === "user");
                if (question) submit(question.content);
              }}
            />
          ))
        )}
        <div ref={bottom} />
      </div>

      <form
        className="border-t p-3 sm:p-4"
        onSubmit={(event) => {
          event.preventDefault();
          submit(draft);
        }}
      >
        {send.isError && (
          <Callout tone="danger" className="mb-3">
            {describeError(send.error)}
            {send.error instanceof ApiError && send.error.code === "model_not_configured" &&
              " Choose a model for the lead role in Settings."}
          </Callout>
        )}
        <div className="flex items-end gap-2">
          <Textarea
            aria-label="Your question"
            rows={2}
            maxLength={4000}
            className="min-h-11 resize-none"
            placeholder={pending ? "Waiting for the answer…" : "Ask a question about this research"}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                submit(draft);
              }
            }}
          />
          <Button
            type="submit"
            size="icon"
            className="size-11"
            aria-label="Send"
            disabled={!draft.trim() || send.isPending || !!pending}
          >
            {send.isPending ? <Spinner /> : <SendHorizontalIcon />}
          </Button>
        </div>
        <p className="mt-1.5 text-xs text-muted-foreground">Enter to send, Shift+Enter for a new line.</p>
      </form>
    </>
  );
}

function toCitations(citations: ChatCitation[], projectId: string): CitationInfo[] {
  return citations.map((citation) => ({
    number: citation.number,
    title: citation.title,
    url: citation.url,
    sourceHref: `/projects/${projectId}/sources/${citation.source_id}`,
    quotes: citation.quotes.map((q) => ({ quote: q.quote, statement: q.statement, page: q.page })),
  }));
}

function MessageView({
  message,
  projectId,
  threadId,
  onRetry,
}: {
  message: ChatMessage;
  projectId: string;
  threadId: string;
  onRetry: () => void;
}) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground">
          {message.content}
        </p>
      </div>
    );
  }
  if (message.status === "pending") return <PendingReply threadId={threadId} messageId={message.id} />;
  if (message.status === "failed") {
    return (
      <Callout
        tone="danger"
        title="No answer"
        action={
          <Button size="sm" variant="outline" onClick={onRetry}>
            Try again
          </Button>
        }
      >
        {message.error_message ?? "The assistant couldn't answer."}
      </Callout>
    );
  }
  return (
    <div className="max-w-[95%]">
      <Markdown markdown={message.content} citations={toCitations(message.citations, projectId)} compact />
    </div>
  );
}

function PendingReply({ threadId, messageId }: { threadId: string; messageId: string }) {
  const activity = useReplyStream(threadId, messageId);
  const last = activity[activity.length - 1];
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
      <Spinner />
      <span>{last?.detail ?? "Thinking…"}</span>
    </div>
  );
}
