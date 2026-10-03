import { ChevronRight } from 'lucide-react'

import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Card, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Skeleton } from '@/components/ui/feedback'
import { useWorkflows } from '@/features/processing/api'

export function WorkflowsPage() {
  const workflows = useWorkflows()
  return (
    <>
      <PageHeader
        title="Workflows"
        description="Versioned step sequences. Every job is pinned to the version it started with; new jobs use the default."
      />
      {workflows.isError ? (
        <ErrorNotice error={workflows.error} title="Unable to load workflows" />
      ) : workflows.isPending ? (
        <Skeleton className="h-40" />
      ) : (
        <div className="space-y-3">
          {[...workflows.data].reverse().map((w) => (
            <Card key={`${w.key}@${w.version}`}>
              <CardHeader>
                <div className="flex items-center gap-2">
                  <CardTitle className="font-mono">
                    {w.key} v{w.version}
                  </CardTitle>
                  {w.is_default ? <Badge tone="accent">default</Badge> : null}
                </div>
                <span className="text-xs text-muted">ends in {w.final_status.replaceAll('_', ' ')}</span>
              </CardHeader>
              <ol className="flex flex-wrap items-center gap-1 px-4 pb-4 text-xs" aria-label={`${w.key} v${w.version} steps`}>
                {w.steps.map((step, i) => (
                  <li key={step} className="flex items-center gap-1">
                    {i > 0 ? <ChevronRight className="size-3 text-muted" aria-hidden /> : null}
                    <span className="rounded border border-border px-2 py-0.5 font-mono">{step}</span>
                  </li>
                ))}
              </ol>
            </Card>
          ))}
        </div>
      )}
    </>
  )
}
