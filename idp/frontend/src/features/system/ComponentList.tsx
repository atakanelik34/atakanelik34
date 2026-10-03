import { Database, HardDrive, Layers, Server, type LucideIcon } from 'lucide-react'

import type { ComponentStatus } from '@/features/system/api'
import { StatusBadge } from '@/features/system/StatusBadge'
import { formatLatency } from '@/lib/format'

const META: Record<string, { label: string; description: string; icon: LucideIcon }> = {
  database: { label: 'PostgreSQL', description: 'System of record', icon: Database },
  redis: { label: 'Redis', description: 'Job queue and rate limits', icon: Layers },
  storage: { label: 'Object storage', description: 'Original documents', icon: HardDrive },
  workers: { label: 'Workers', description: 'Processing pipeline', icon: Server },
}

function describe(component: ComponentStatus): string {
  const m = component.metadata
  switch (component.name) {
    case 'database':
      return typeof m.schema_revision === 'string' ? `schema ${m.schema_revision}` : ''
    case 'storage':
      return [m.backend, m.bucket].filter((v) => typeof v === 'string').join(' · ')
    case 'workers': {
      const count = typeof m.count === 'number' ? m.count : 0
      const queued = typeof m.queued_jobs === 'number' ? m.queued_jobs : 0
      return `${count} online · ${queued} queued`
    }
    default:
      return ''
  }
}

export function ComponentList({ components }: { components: ComponentStatus[] }) {
  return (
    <ul className="divide-y divide-border">
      {components.map((component) => {
        const meta = META[component.name] ?? {
          label: component.name,
          description: '',
          icon: Server,
        }
        const Icon = meta.icon
        return (
          <li key={component.name} className="flex items-center gap-4 px-5 py-3">
            <span className="flex size-8 items-center justify-center rounded-md bg-canvas text-muted">
              <Icon aria-hidden className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">{meta.label}</p>
              <p className="truncate text-xs text-muted">
                {meta.description}
                {component.detail ? ` — ${component.detail}` : ''}
              </p>
            </div>
            <span className="hidden font-mono text-xs text-muted sm:block">{describe(component)}</span>
            <span className="w-14 text-right font-mono text-xs text-muted">
              {formatLatency(component.latency_ms)}
            </span>
            <StatusBadge status={component.status} />
          </li>
        )
      })}
    </ul>
  )
}
