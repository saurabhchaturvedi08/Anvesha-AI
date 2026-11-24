"use client";

import { useState } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState, Skeleton } from "@/components/ui/feedback";
import { Select } from "@/components/ui/input";
import { formatCompact, formatNumber, formatUsd } from "@/lib/format";
import { useUsage } from "@/lib/queries";

export function UsageCard() {
  const [days, setDays] = useState(30);
  const usage = useUsage(days);
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <CardTitle>Usage</CardTitle>
          <CardDescription>Model calls made with your keys, and what they cost.</CardDescription>
        </div>
        <Select
          aria-label="Period"
          className="w-auto"
          value={days}
          onChange={(event) => setDays(Number(event.target.value))}
        >
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </Select>
      </CardHeader>
      <CardContent className="space-y-4">
        {usage.isPending ? (
          <Skeleton className="h-32" />
        ) : usage.isError ? (
          <ErrorState error={usage.error} retry={() => void usage.refetch()} />
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              {[
                ["Cost", formatUsd(usage.data.cost_usd)],
                ["Calls", formatNumber(usage.data.calls)],
                ["Input tokens", formatCompact(usage.data.input_tokens)],
                ["Output tokens", formatCompact(usage.data.output_tokens)],
              ].map(([label, value]) => (
                <div key={label} className="rounded-md bg-muted/60 p-3">
                  <dt className="text-xs text-muted-foreground">{label}</dt>
                  <dd className="text-lg font-semibold tabular-nums">{value}</dd>
                </div>
              ))}
            </dl>
            {usage.data.unpriced_calls > 0 && (
              <p className="text-xs text-muted-foreground">
                {formatNumber(usage.data.unpriced_calls)} call(s) used models without known prices
                (such as local models) and aren&apos;t included in the cost.
              </p>
            )}
            {usage.data.by_model.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-muted-foreground">
                    <tr className="border-b">
                      <th className="py-2 pr-3 font-medium">Model</th>
                      <th className="py-2 pr-3 text-right font-medium">Calls</th>
                      <th className="py-2 pr-3 text-right font-medium">Errors</th>
                      <th className="py-2 pr-3 text-right font-medium">Tokens</th>
                      <th className="py-2 text-right font-medium">Cost</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {usage.data.by_model.map((row) => (
                      <tr key={`${row.provider}/${row.model}`}>
                        <td className="py-2 pr-3">
                          <span className="font-medium">{row.model}</span>{" "}
                          <span className="text-xs text-muted-foreground">{row.provider}</span>
                        </td>
                        <td className="py-2 pr-3 text-right tabular-nums">{formatNumber(row.calls)}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">{formatNumber(row.errors)}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">
                          {formatCompact(row.input_tokens + row.output_tokens)}
                        </td>
                        <td className="py-2 text-right tabular-nums">
                          {row.unpriced_calls === row.calls ? "—" : formatUsd(row.cost_usd)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
