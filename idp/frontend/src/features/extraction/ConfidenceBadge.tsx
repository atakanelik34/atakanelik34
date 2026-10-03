import { Badge } from '@/components/ui/badge'
import type { FieldValue } from '@/features/extraction/types'

/** Field-level confidence judged against that field's own threshold. */
export function ConfidenceBadge({ field }: { field: Pick<FieldValue, 'confidence' | 'threshold' | 'status'> }) {
  if (field.status === 'missing') return <Badge tone="danger">missing</Badge>
  if (field.status === 'corrected') return <Badge tone="accent">corrected</Badge>
  if (field.status === 'accepted') return <Badge tone="success">accepted</Badge>
  if (field.status === 'rejected') return <Badge tone="danger">rejected</Badge>
  const pct = Math.round(field.confidence * 100)
  const tone = field.confidence >= field.threshold ? 'success' : field.confidence >= field.threshold - 0.15 ? 'warning' : 'danger'
  return (
    <Badge tone={tone} title={`threshold ${Math.round(field.threshold * 100)}%`}>
      {pct}%
    </Badge>
  )
}
