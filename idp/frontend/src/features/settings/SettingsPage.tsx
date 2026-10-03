import type { ReactNode } from 'react'

import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/feedback'
import { useMe } from '@/features/auth/api'

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-4 py-2 text-sm">
      <dt className="text-muted">{label}</dt>
      <dd className="col-span-2 min-w-0 break-all">{value}</dd>
    </div>
  )
}

export function SettingsPage() {
  const { data: me } = useMe()

  return (
    <>
      <PageHeader title="Settings" description="Your account and tenant." />
      {!me ? (
        <Skeleton className="h-48" />
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Profile</CardTitle>
            </CardHeader>
            <CardContent>
              <dl className="divide-y divide-border">
                <Row label="Name" value={me.user.full_name} />
                <Row label="Email" value={me.user.email} />
                <Row label="Role" value={<Badge tone="accent">{me.user.role}</Badge>} />
              </dl>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Tenant</CardTitle>
            </CardHeader>
            <CardContent>
              <dl className="divide-y divide-border">
                <Row label="Name" value={me.tenant.name} />
                <Row label="Slug" value={<span className="font-mono text-xs">{me.tenant.slug}</span>} />
                <Row label="ID" value={<span className="font-mono text-xs">{me.tenant.id}</span>} />
              </dl>
            </CardContent>
          </Card>
          <Card className="lg:col-span-2">
            <CardHeader>
              <div>
                <CardTitle>Effective permissions</CardTitle>
                <CardDescription>Granted by your role; enforced by the API on every request.</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-1.5">
              {me.permissions.map((p) => (
                <Badge key={p} className="font-mono">
                  {p}
                </Badge>
              ))}
            </CardContent>
          </Card>
        </div>
      )}
    </>
  )
}
