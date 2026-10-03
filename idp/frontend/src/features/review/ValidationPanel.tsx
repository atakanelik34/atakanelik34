import { CircleCheck, CircleX, TriangleAlert, UserCheck } from 'lucide-react'

import type { ReviewPart } from '@/features/review/api'
import { cn } from '@/lib/utils'

const ICON = {
  PASS: { Icon: CircleCheck, className: 'text-success' },
  WARNING: { Icon: TriangleAlert, className: 'text-warning' },
  FAIL: { Icon: CircleX, className: 'text-danger' },
  REQUIRES_HUMAN: { Icon: UserCheck, className: 'text-warning' },
} as const

const ORDER = { FAIL: 0, REQUIRES_HUMAN: 1, WARNING: 2, PASS: 3 } as const

export function ValidationPanel({
  part,
  onSelectPath,
}: {
  part: ReviewPart
  onSelectPath: (path: string) => void
}) {
  const items = [...part.validation].sort((a, b) => ORDER[a.outcome] - ORDER[b.outcome])
  const passed = items.filter((v) => v.outcome === 'PASS').length
  return (
    <div>
      <p className="px-4 pt-3 text-[11px] text-muted">
        {passed} of {items.length} checks passed
      </p>
      <ul className="divide-y divide-border">
        {items
          .filter((v) => v.outcome !== 'PASS')
          .map((v) => {
            const { Icon, className } = ICON[v.outcome]
            return (
              <li key={v.rule}>
                <button
                  type="button"
                  className="flex w-full gap-2 px-4 py-2 text-left text-xs hover:bg-canvas"
                  onClick={() => v.fields[0] && onSelectPath(v.fields[0])}
                >
                  <Icon aria-label={v.outcome} className={cn('mt-0.5 size-3.5 shrink-0', className)} />
                  <span>
                    <span className="font-medium">{v.rule_type}</span> — {v.message}
                  </span>
                </button>
              </li>
            )
          })}
      </ul>
    </div>
  )
}
