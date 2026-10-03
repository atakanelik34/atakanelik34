import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'

import { Card, CardContent } from '@/components/ui/card'
import { ErrorNotice, Skeleton } from '@/components/ui/feedback'
import { apiRequest } from '@/lib/api'
import { formatDuration } from '@/lib/format'

export interface Overview {
  window_days: number
  documents: Record<string, number>
  jobs: Record<string, number>
  open_reviews: number
  straight_through_rate: number | null
  avg_processing_ms: number | null
  llm: { calls: number; cost: number; tokens: number }
  actions: Record<string, number>
}

export function useOverview() {
  return useQuery({
    queryKey: ['overview'],
    queryFn: ({ signal }) => apiRequest<Overview>('/system/overview', { signal }),
    refetchInterval: 30_000,
  })
}

function Stat({ label, value, hint, to }: { label: string; value: string; hint?: string; to?: string }) {
  const body = (
    <CardContent className="space-y-1">
      <p className="text-xs text-muted">{label}</p>
      <p className="text-2xl font-semibold tabular-nums">{value}</p>
      {hint ? <p className="text-[11px] text-muted">{hint}</p> : null}
    </CardContent>
  )
  return <Card>{to ? <Link to={to} className="block hover:bg-canvas">{body}</Link> : body}</Card>
}

/** Processing at a glance (caller's tenant). */
export function OverviewCards() {
  const overview = useOverview()
  if (overview.isError) return <ErrorNotice error={overview.error} title="Unable to load the overview" />
  if (!overview.data) return <Skeleton className="h-28" />
  const o = overview.data
  const total = Object.values(o.documents).reduce((a, b) => a + b, 0)
  const failed = (o.jobs.FAILED ?? 0) + (o.jobs.DEAD_LETTERED ?? 0)
  const actionsDone = o.actions.succeeded ?? 0
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Stat label="Documents" value={String(total)} hint={`${o.documents.COMPLETED ?? 0} completed · ${o.documents.PROCESSING ?? 0} processing`} to="/documents" />
      <Stat label="Waiting for review" value={String(o.open_reviews)} hint={`${o.documents.READY_FOR_ACTION ?? 0} awaiting action approval`} to="/reviews" />
      <Stat
        label="Straight-through rate"
        value={o.straight_through_rate === null ? '—' : `${Math.round(o.straight_through_rate * 100)}%`}
        hint={`finished without a human · last ${o.window_days} days`}
      />
      <Stat label="Avg. processing time" value={formatDuration(o.avg_processing_ms)} hint="machine time per document" />
      <Stat label="Failed jobs" value={String(failed)} hint={`last ${o.window_days} days`} to="/processing" />
      <Stat label="Actions executed" value={String(actionsDone)} hint={`${o.actions.failed ?? 0} failed`} />
      <Stat label="Model calls" value={String(o.llm.calls)} hint={`${o.llm.tokens.toLocaleString()} tokens`} to="/providers" />
      <Stat label="Model cost" value={o.llm.cost.toFixed(2)} hint={`last ${o.window_days} days`} />
    </div>
  )
}
