import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Skeleton } from '@/components/ui/feedback'
import { Table, TD, TH, THead, TR } from '@/components/ui/table'
import { useProviders, useProviderUsage } from '@/features/processing/api'
import { PolicyCard } from '@/features/processing/PolicyCard'

const TIERS = ['deterministic', 'layout', 'model', 'local LLM', 'cloud LLM']

const STATUS_TEXT = {
  configured: 'configured',
  not_configured: 'Provider not configured',
  host_not_allowed: 'host not allow-listed',
} as const

export function ProvidersPage() {
  const providers = useProviders()
  if (providers.isError) return <ErrorNotice error={providers.error} title="Unable to load providers" />
  if (!providers.data) return <Skeleton className="h-64" />

  return (
    <>
      <PageHeader title="Providers & models" description="Processing providers available to the router, in order of preference." />
      <div className="space-y-6">
        <PolicyCard />
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
                  <TD>{p.locality === 'unknown' ? <span className="text-xs text-muted">—</span> : <Badge tone={p.locality === 'local' ? 'neutral' : 'warning'}>{p.locality}</Badge>}</TD>
                  <TD>
                    <Badge tone={p.status === 'configured' ? 'success' : p.status === 'host_not_allowed' ? 'danger' : 'neutral'}>
                      {STATUS_TEXT[p.status]}
                    </Badge>
                  </TD>
                  <TD className="text-xs">{p.cost_per_page.toFixed(4)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
        <UsageCard />
      </div>
    </>
  )
}

function UsageCard() {
  const usage = useProviderUsage(30)
  return (
    <Card>
      <CardHeader>
        <CardTitle>Model usage · last 30 days</CardTitle>
      </CardHeader>
      {usage.isError ? (
        <CardContent>
          <ErrorNotice error={usage.error} />
        </CardContent>
      ) : !usage.data ? (
        <CardContent>
          <Skeleton className="h-10" />
        </CardContent>
      ) : usage.data.length === 0 ? (
        <CardContent className="text-sm text-muted">No model calls yet.</CardContent>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Provider</TH>
              <TH>Purpose</TH>
              <TH>Status</TH>
              <TH>Calls</TH>
              <TH>Tokens in / out</TH>
              <TH>Cost</TH>
            </tr>
          </THead>
          <tbody>
            {usage.data.map((u) => (
              <TR key={`${u.provider}:${u.model}:${u.purpose}:${u.status}`}>
                <TD className="text-xs">
                  <span className="font-mono">{u.provider}</span> <span className="text-muted">{u.model}</span>
                </TD>
                <TD className="text-xs">{u.purpose}</TD>
                <TD>
                  <Badge tone={u.status === 'ok' ? 'success' : u.status === 'refused' ? 'warning' : 'danger'}>{u.status}</Badge>
                </TD>
                <TD className="text-xs">{u.calls}</TD>
                <TD className="text-xs">
                  {u.input_tokens.toLocaleString()} / {u.output_tokens.toLocaleString()}
                </TD>
                <TD className="text-xs">{u.cost.toFixed(4)}</TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  )
}
