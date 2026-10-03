import { UserPlus } from 'lucide-react'
import { useState } from 'react'

import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Skeleton } from '@/components/ui/feedback'
import { Table, TD, TH, THead, TR } from '@/components/ui/table'
import { hasPermission, useMe } from '@/features/auth/api'
import type { Role } from '@/features/auth/types'
import { useUsers } from '@/features/users/api'
import { CreateUserForm } from '@/features/users/CreateUserForm'
import { formatDateTime } from '@/lib/format'

/** Mirrors ASSIGNABLE_ROLES in backend/src/idp/domain/identity.py; the API enforces it. */
const ASSIGNABLE: Record<Role, Role[]> = {
  owner: ['owner', 'admin', 'operator', 'reviewer', 'viewer'],
  admin: ['admin', 'operator', 'reviewer', 'viewer'],
  operator: [],
  reviewer: [],
  viewer: [],
}

export function UsersPage() {
  const { data: me } = useMe()
  const canRead = hasPermission(me, 'users:read')
  const canWrite = hasPermission(me, 'users:write')
  const users = useUsers(canRead)
  const [creating, setCreating] = useState(false)
  const assignable = me ? ASSIGNABLE[me.user.role] : []

  return (
    <>
      <PageHeader
        title="Users"
        description="People in this tenant and their roles."
        actions={
          canWrite && !creating && assignable.length > 0 ? (
            <Button size="sm" onClick={() => setCreating(true)}>
              <UserPlus />
              Add user
            </Button>
          ) : null
        }
      />

      {creating ? (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle>New user</CardTitle>
          </CardHeader>
          <CardContent>
            <CreateUserForm assignableRoles={assignable} onDone={() => setCreating(false)} />
          </CardContent>
        </Card>
      ) : null}

      {users.isError ? (
        <ErrorNotice error={users.error} title="Unable to load users" />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Name</TH>
                <TH>Email</TH>
                <TH>Role</TH>
                <TH>Status</TH>
                <TH>Last sign-in</TH>
              </tr>
            </THead>
            <tbody>
              {users.isPending
                ? [0, 1, 2].map((i) => (
                    <TR key={i}>
                      <TD colSpan={5}>
                        <Skeleton className="h-5" />
                      </TD>
                    </TR>
                  ))
                : users.data.map((user) => (
                    <TR key={user.id}>
                      <TD className="font-medium">{user.full_name}</TD>
                      <TD className="text-muted">{user.email}</TD>
                      <TD>
                        <Badge tone="accent">{user.role}</Badge>
                      </TD>
                      <TD>
                        {user.is_active ? (
                          <Badge tone="success">active</Badge>
                        ) : (
                          <Badge>inactive</Badge>
                        )}
                      </TD>
                      <TD className="text-muted">{formatDateTime(user.last_login_at)}</TD>
                    </TR>
                  ))}
            </tbody>
          </Table>
        </Card>
      )}
    </>
  )
}
