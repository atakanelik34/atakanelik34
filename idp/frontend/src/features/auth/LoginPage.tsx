import { useQueryClient } from '@tanstack/react-query'
import { ScanLine } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import { ErrorNotice, Spinner } from '@/components/ui/feedback'
import { Input, Label } from '@/components/ui/input'
import { login, useAccessToken } from '@/features/auth/api'
import { session } from '@/lib/session'

export function LoginPage() {
  const token = useAccessToken()
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)

  const from = (location.state as { from?: string } | null)?.from ?? '/'
  if (token) return <Navigate to={from} replace />

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPending(true)
    setError(null)
    try {
      const result = await login(email, password)
      queryClient.clear()
      session.setToken(result.access_token)
      void navigate(from, { replace: true })
    } catch (err) {
      setError(err)
      setPassword('')
    } finally {
      setPending(false)
    }
  }

  return (
    <main className="flex min-h-full items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center gap-2.5">
          <span className="flex size-9 items-center justify-center rounded-md bg-accent text-white">
            <ScanLine aria-hidden className="size-5" />
          </span>
          <div>
            <p className="text-sm font-semibold">IDP Platform</p>
            <p className="text-xs text-muted">Intelligent document processing</p>
          </div>
        </div>
        <form
          onSubmit={(e) => void onSubmit(e)}
          className="space-y-4 rounded-lg border border-border bg-surface p-6 shadow-xs"
          noValidate
        >
          <h1 className="text-base font-semibold">Sign in</h1>
          {error ? <ErrorNotice error={error} /> : null}
          <div className="space-y-1.5">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <Button type="submit" className="w-full" disabled={pending || !email || !password}>
            {pending ? <Spinner /> : null}
            Sign in
          </Button>
        </form>
      </div>
    </main>
  )
}
