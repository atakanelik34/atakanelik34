import { Badge } from '@/components/ui/badge'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { DocumentPart } from '@/features/documents/types'

export function PartsCard({ parts, onSelect }: { parts: DocumentPart[]; onSelect: (page: number) => void }) {
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Documents in this file</CardTitle>
          <CardDescription>Classification and splitting by page.</CardDescription>
        </div>
      </CardHeader>
      {parts.length === 0 ? (
        <p className="px-5 py-4 text-sm text-muted">Not classified yet.</p>
      ) : (
        <ul className="divide-y divide-border">
          {parts.map((part) => (
            <li key={part.id}>
              <button
                type="button"
                className="w-full px-5 py-3 text-left hover:bg-canvas"
                onClick={() => onSelect(part.page_start)}
              >
                <div className="flex items-center gap-2">
                  {part.document_type_name ? (
                    <Badge tone="accent">{part.document_type_name}</Badge>
                  ) : (
                    <Badge tone="warning">unclassified</Badge>
                  )}
                  <span className="text-xs text-muted">
                    pages {part.page_start}
                    {part.page_end !== part.page_start ? `–${part.page_end}` : ''}
                    {part.schema_version ? ` · schema v${part.schema_version}` : ''}
                  </span>
                  <span className="ml-auto font-mono text-xs">
                    {Math.round(part.classification_confidence * 100)}%
                  </span>
                </div>
                <p className="mt-1 truncate text-[11px] text-muted" title={part.classification_reasons.join('\n')}>
                  {part.classification_reasons.slice(0, 4).join(' · ')}
                </p>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
