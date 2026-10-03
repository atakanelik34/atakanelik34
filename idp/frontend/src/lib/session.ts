/**
 * Access-token holder.
 *
 * The token lives in memory and is mirrored to sessionStorage so a reload keeps
 * the tab signed in; it is never written to localStorage or cookies readable by
 * other tabs. Moving to an httpOnly refresh cookie is a phase-12 hardening item.
 */
const STORAGE_KEY = 'idp.session'

type Listener = () => void

let token: string | null = readInitial()
const listeners = new Set<Listener>()

function readInitial(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

function emit(): void {
  for (const listener of listeners) listener()
}

export const session = {
  getToken(): string | null {
    return token
  },
  setToken(value: string): void {
    token = value
    try {
      sessionStorage.setItem(STORAGE_KEY, value)
    } catch {
      /* storage unavailable: keep the in-memory token */
    }
    emit()
  },
  clear(): void {
    token = null
    try {
      sessionStorage.removeItem(STORAGE_KEY)
    } catch {
      /* ignore */
    }
    emit()
  },
  subscribe(listener: Listener): () => void {
    listeners.add(listener)
    return () => listeners.delete(listener)
  },
}
