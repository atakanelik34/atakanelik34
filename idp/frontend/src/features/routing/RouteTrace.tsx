import { Badge } from '@/components/ui/badge'
import type { ProviderAttempt, RouteTrace } from '@/features/extraction/types'
import { formatDuration } from '@/lib/format'

const ATTEMPT_TONE = {
  ok: 'success',
  skipped: 'neutral',
  failed: 'danger',
  timeout: 'danger',
  circuit_open: 'warning',
} as const satisfies Record<ProviderAttempt['status'], string>

const OUTCOME_TONE = { accepted: 'success', escalated: 'accent', exhausted: 'warning' } as const

export function isRouteTrace(value: unknown): value is RouteTrace {
  return typeof value === 'object' && value !== null && 'route' in value && 'stages' in value
}

/** Why the router chose this path, what ran, and what was ruled out. */
export function RouteTraceView({ trace }: { trace: RouteTrace }) {
  return (
    <div className="space-y-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="accent">{trace.route.replaceAll('_', ' ')}</Badge>
        <span className="text-muted">
          routing v{trace.routing_version} · policy v{trace.policy_version} · est. cost {trace.estimated_cost.toFixed(4)}
        </span>
      </div>
      {trace.reasons.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-4 text-muted">
          {trace.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      ) : null}
      <ol className="space-y-2">
        {trace.stages.map((stage) => (
          <li key={stage.stage} className="rounded-md border border-border p-2">
            <div className="mb-1 flex items-center gap-2">
              <span className="font-medium">Stage {stage.stage + 1}</span>
              <Badge tone={OUTCOME_TONE[stage.outcome]}>{stage.outcome}</Badge>
              {stage.unresolved.length > 0 ? (
                <span className="truncate text-muted">unresolved: {stage.unresolved.join(', ')}</span>
              ) : null}
            </div>
            <ul className="space-y-0.5">
              {stage.attempts.map((a) => (
                <li key={a.provider} className="flex items-center gap-2">
                  <span className="font-mono">{a.provider}</span>
                  <Badge tone={ATTEMPT_TONE[a.status]}>{a.status.replace('_', ' ')}</Badge>
                  <span className="text-muted">
                    {a.status === 'ok' ? `${a.candidates ?? 0} candidates · ${formatDuration(a.duration_ms)}` : (a.error ?? a.reasons?.join('; ') ?? '')}
                  </span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
      {trace.rejected.length > 0 ? (
        <div>
          <p className="mb-1 font-medium">Not used</p>
          <ul className="space-y-0.5">
            {trace.rejected.map((r) => (
              <li key={r.provider}>
                <span className="font-mono">{r.provider}</span> <span className="text-muted">— {r.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
