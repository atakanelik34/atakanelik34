import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Skeleton } from '@/components/ui/feedback'
import { Table, TD, TH, THead, TR } from '@/components/ui/table'
import { useProviders } from '@/features/processing/api'

const TIERS = ['deterministic', 'layout', 'model', 'local LLM', 'cloud LLM']

const MODE_TEXT = {
  LOCAL_ONLY: 'Document content never leaves this deployment. Cloud providers are refused by the router.',
  HYBRID: 'Local providers first; cloud providers only as a later fallback.',
  CLOUD_ALLOWED: 'Cloud providers may be used whenever the router selects them.',
}

export function ProvidersPage() {
  const providers = useProviders()
  if (providers.isError) return <ErrorNotice error={providers.error} title="Unable to load providers" />
  if (!providers.data) return <Skeleton className="h-64" />
  const { policy } = providers.data

  return (
    <>
      <PageHeader title="Providers & models" description="Processing providers available to the router, in order of preference." />
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>Processing policy</CardTitle>
            <Badge tone={policy.mode === 'LOCAL_ONLY' ? 'success' : 'warning'}>{policy.mode.replace('_', ' ')}</Badge>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <p>{MODE_TEXT[policy.mode]}</p>
            <p className="text-xs text-muted">
              LLM extraction {policy.allow_llm ? 'allowed' : 'disabled'} · mock providers {policy.allow_mock_providers ? 'allowed' : 'refused'} ·
              cost limit {policy.max_cost_per_document ?? 'none'} · policy v{policy.version} · routing v{policy.routing_version}
            </p>
          </CardContent>
        </Card>
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Provider</TH>
                <TH>Kind</TH>
                <TH>Tier</TH>
                <TH>Locality</TH>
                <TH>Status</TH>
                <TH>Cost / page</TH>
              </tr>
            </THead>
            <tbody>
              {providers.data.providers.map((p) => (
                <TR key={`${p.kind}:${p.name}`}>
                  <TD>
                    <span className="font-mono text-xs">{p.name}</span>
                    {p.version ? <span className="ml-1 text-[11px] text-muted">v{p.version}</span> : null}
                    {p.is_mock ? (
                      <Badge tone="warning" className="ml-2">
                        mock
                      </Badge>
                    ) : null}
                  </TD>
                  <TD className="text-xs">
                    {p.kind} · {p.method}
                  </TD>
                  <TD className="text-xs">{p.tier === null ? '—' : TIERS[p.tier]}</TD>
                  <TD>
                    <Badge tone={p.locality === 'local' ? 'neutral' : 'warning'}>{p.locality}</Badge>
                  </TD>
                  <TD>
                    <Badge tone={p.status === 'configured' ? 'success' : 'neutral'}>
                      {p.status === 'configured' ? 'configured' : 'Provider not configured'}
                    </Badge>
                  </TD>
                  <TD className="text-xs">{p.cost_per_page.toFixed(4)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      </div>
    </>
  )
}
