"use client";

import type { RunEvent } from "@anvesha/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { keys } from "@/lib/queries";
import { RUN_CHANGING_EVENTS } from "@/lib/run-events";
import { readSse, SseHttpError, sleep } from "@/lib/sse";

export type StreamState = "connecting" | "live" | "reconnecting" | "ended" | "error";

/**
 * A run's activity: replays everything so far, then follows new events live until the
 * run ends. Reconnects (resuming after the last event) if the connection drops.
 *
 * Render the component using it with ``key={runId}`` so a different run starts fresh.
 */
export function useRunEvents(runId: string, projectId: string) {
  const queryClient = useQueryClient();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [state, setState] = useState<StreamState>("connecting");
  const lastId = useRef(0);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    const refresh = (all = false) => {
      // Many events arrive in bursts; refetch the run at most twice a second.
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: keys.run(runId) });
        if (all) {
          void queryClient.invalidateQueries({ queryKey: keys.runs(projectId) });
          void queryClient.invalidateQueries({ queryKey: keys.project(projectId) });
          void queryClient.invalidateQueries({ queryKey: keys.projects });
          void queryClient.invalidateQueries({ queryKey: keys.reports(projectId) });
          void queryClient.invalidateQueries({ queryKey: keys.sources(projectId) });
          void queryClient.invalidateQueries({ queryKey: keys.findings(runId) });
        }
      }, 500);
    };

    (async () => {
      let ended = false;
      let failures = 0;
      while (!ended && !controller.signal.aborted) {
        const batch: RunEvent[] = [];
        let flushQueued = false;
        const flush = () => {
          flushQueued = false;
          if (batch.length === 0) return;
          const added = batch.splice(0);
          setEvents((current) => [...current, ...added]);
        };
        try {
          await readSse(
            `/api/v1/runs/${runId}/events/stream?after=${lastId.current}`,
            (message) => {
              if (message.event === "end") {
                ended = true;
                return;
              }
              const event = JSON.parse(message.data) as RunEvent;
              if (event.id <= lastId.current) return;
              lastId.current = event.id;
              failures = 0;
              batch.push(event);
              if (!flushQueued) {
                flushQueued = true;
                queueMicrotask(flush);
              }
              if (RUN_CHANGING_EVENTS.has(event.type)) {
                refresh(event.type.startsWith("run_") || event.type === "plan_ready");
              }
            },
            controller.signal,
            () => setState("live"),
          );
          flush();
          setState(ended ? "ended" : "reconnecting");
        } catch (error) {
          flush();
          if (controller.signal.aborted) return;
          if (error instanceof SseHttpError && error.status >= 400 && error.status < 500) {
            setState("error");
            return;
          }
          setState("reconnecting");
        }
        if (ended) {
          refresh(true);
          return;
        }
        failures += 1;
        await sleep(Math.min(1000 * 2 ** Math.min(failures, 4), 15000), controller.signal);
      }
    })();

    return () => {
      controller.abort();
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    };
  }, [runId, projectId, queryClient]);

  return { events, state };
}
