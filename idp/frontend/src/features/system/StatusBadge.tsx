import { CircleCheck, CircleX, TriangleAlert } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import type { HealthStatus } from '@/features/system/api'

const CONFIG = {
  up: { tone: 'success', label: 'Operational', Icon: CircleCheck },
  degraded: { tone: 'warning', label: 'Degraded', Icon: TriangleAlert },
  down: { tone: 'danger', label: 'Down', Icon: CircleX },
} as const

export function StatusBadge({ status }: { status: HealthStatus }) {
  const { tone, label, Icon } = CONFIG[status]
  return (
    <Badge tone={tone}>
      <Icon aria-hidden />
      {label}
    </Badge>
  )
}
