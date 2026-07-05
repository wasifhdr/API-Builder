import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, ApiError } from '../lib/api'
import type { User } from '../lib/types'

interface SessionState {
  user: User | null
  loading: boolean
  refetch: () => Promise<void>
  logout: () => Promise<void>
}

const SessionContext = createContext<SessionState | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  const refetch = useCallback(async () => {
    setLoading(true)
    try {
      setUser(await api.get<User>('/me'))
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setUser(null)
      } else {
        throw err
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refetch()
  }, [refetch])

  const logout = useCallback(async () => {
    await api.post('/auth/logout')
    setUser(null)
  }, [])

  return (
    <SessionContext.Provider value={{ user, loading, refetch, logout }}>
      {children}
    </SessionContext.Provider>
  )
}

export function useSession() {
  const ctx = useContext(SessionContext)
  if (!ctx) throw new Error('useSession must be used within SessionProvider')
  return ctx
}
