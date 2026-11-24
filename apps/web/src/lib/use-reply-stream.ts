"use client";

import type { ChatMessage, ChatThreadDetail } from "@anvesha/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { keys } from "@/lib/queries";
import { readSse, sleep } from "@/lib/sse";

export type ReplyStep = { tool: string; detail: string; at?: string };

/**
 * Follows a pending assistant reply: what it is doing, then the finished message, which is
 * written into the chat's cache. Falls back to refetching the chat if streaming fails.
 */
export function useReplyStream(threadId: string, messageId: string | null) {
  const queryClient = useQueryClient();
  const [activity, setActivity] = useState<ReplyStep[]>([]);

  useEffect(() => {
    if (!messageId) return;
    const controller = new AbortController();
    const finish = (message: ChatMessage) => {
      queryClient.setQueryData<ChatThreadDetail>(keys.chat(threadId), (current) =>
        current
          ? { ...current, messages: current.messages.map((m) => (m.id === message.id ? message : m)) }
          : current,
      );
    };
    (async () => {
      for (let attempt = 0; attempt < 6 && !controller.signal.aborted; attempt += 1) {
        let done = false;
        try {
          await readSse(
            `/api/v1/chat-messages/${messageId}/stream`,
            (event) => {
              if (event.event === "progress") {
                setActivity((JSON.parse(event.data) as { activity: ReplyStep[] }).activity);
              } else if (event.event === "message") {
                finish(JSON.parse(event.data) as ChatMessage);
              } else if (event.event === "end") {
                done = true;
              }
            },
            controller.signal,
          );
        } catch {
          if (controller.signal.aborted) return;
        }
        if (done) break;
        await sleep(1500 * (attempt + 1), controller.signal);
      }
      if (!controller.signal.aborted) {
        void queryClient.invalidateQueries({ queryKey: keys.chat(threadId) });
        void queryClient.invalidateQueries({ queryKey: ["chats"] });
      }
    })();
    return () => controller.abort();
  }, [threadId, messageId, queryClient]);

  return activity;
}
