import { useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { ErrorNotice, Spinner } from '@/components/ui/feedback'
import { Input, Label, Select } from '@/components/ui/input'
import type { Role } from '@/features/auth/types'
import { useCreateUser } from '@/features/users/api'

const MIN_PASSWORD = 12

export function CreateUserForm({
  assignableRoles,
  onDone,
}: {
  assignableRoles: Role[]
  onDone: () => void
}) {
  const createUser = useCreateUser()
  const [email, setEmail] = useState('')
  const [fullName, setFullName] = useState('')
  const [role, setRole] = useState<Role>(assignableRoles.includes('reviewer') ? 'reviewer' : assignableRoles[0] ?? 'viewer')
  const [password, setPassword] = useState('')

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    createUser.mutate(
      { email, full_name: fullName, role, password },
      { onSuccess: () => onDone() },
    )
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      {createUser.isError ? <ErrorNotice error={createUser.error} /> : null}
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="new-user-name">Full name</Label>
          <Input id="new-user-name" value={fullName} onChange={(e) => setFullName(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="new-user-email">Email</Label>
          <Input
            id="new-user-email"
            type="email"
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="new-user-role">Role</Label>
          <Select id="new-user-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {assignableRoles.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="new-user-password">Initial password</Label>
          <Input
            id="new-user-password"
            type="password"
            autoComplete="new-password"
            aria-invalid={tooShort}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <p className={tooShort ? 'text-xs text-danger' : 'text-xs text-muted'}>
            At least {MIN_PASSWORD} characters.
          </p>
        </div>
      </div>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button
          type="submit"
          disabled={createUser.isPending || !email || !fullName || password.length < MIN_PASSWORD}
        >
          {createUser.isPending ? <Spinner /> : null}
          Create user
        </Button>
      </div>
    </form>
  )
}
