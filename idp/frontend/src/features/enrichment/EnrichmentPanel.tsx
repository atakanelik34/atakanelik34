import { Badge } from '@/components/ui/badge'
import type { EnrichmentOutcome } from '@/features/extraction/types'

const TONE = {
  matched: 'success',
  not_found: 'warning',
  ambiguous: 'warning',
  skipped: 'neutral',
  not_configured: 'neutral',
  error: 'danger',
} as const satisfies Record<EnrichmentOutcome['status'], string>

const LABEL: Record<EnrichmentOutcome['status'], string> = {
  matched: 'matched',
  not_found: 'not found',
  ambiguous: 'ambiguous',
  skipped: 'skipped',
  not_configured: 'not configured',
  error: 'error',
}

/** Lookups against master data / ERP connections and what they returned. */
export function EnrichmentPanel({ outcomes }: { outcomes: EnrichmentOutcome[] }) {
  if (outcomes.length === 0) return null
  return (
    <ul className="divide-y divide-border">
      {outcomes.map((e) => (
        <li key={e.name} className="space-y-1 px-4 py-3 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{e.name}</span>
            <span className="font-mono text-muted">{e.connection}</span>
            <Badge tone={TONE[e.status]}>{LABEL[e.status]}</Badge>
            {e.is_mock ? <Badge tone="warning">mock</Badge> : null}
            {e.score !== null ? <span className="text-muted">score {Math.round(e.score * 100)}% · {e.matched_on.join(', ')}</span> : null}
          </div>
          {e.message ? <p className="text-muted">{e.message}</p> : null}
          {e.status === 'matched' ? (
            <dl className="grid grid-cols-3 gap-x-2 gap-y-0.5">
              {Object.entries(e.outputs).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-muted">{k.replaceAll('_', ' ')}</dt>
                  <dd className="col-span-2 break-all">{v === null || v === undefined ? '—' : String(v)}</dd>
                </div>
              ))}
            </dl>
          ) : e.candidates.length > 0 ? (
            <p className="text-muted">
              candidates: {e.candidates.map((c) => `${c.name} (${Math.round(c.score * 100)}%)`).join(', ')}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  )
}
