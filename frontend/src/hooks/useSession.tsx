import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, ApiError } from '../lib/api'
import type { RegisterPayload, User } from '../lib/types'

interface SessionState {
  user: User | null
  loading: boolean
  refetch: () => Promise<void>
  logout: () => Promise<void>
  login: (email: string, password: string) => Promise<User>
  register: (payload: RegisterPayload) => Promise<User>
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

  const login = useCallback(async (email: string, password: string) => {
    const loggedIn = await api.post<User>('/auth/login-password', { email, password })
    setUser(loggedIn)
    return loggedIn
  }, [])

  const register = useCallback(async (payload: RegisterPayload) => {
    const registered = await api.post<User>('/auth/register', payload)
    setUser(registered)
    return registered
  }, [])

  return (
    <SessionContext.Provider value={{ user, loading, refetch, logout, login, register }}>
      {children}
    </SessionContext.Provider>
  )
}

export function useSession() {
  const ctx = useContext(SessionContext)
  if (!ctx) throw new Error('useSession must be used within SessionProvider')
  return ctx
}
