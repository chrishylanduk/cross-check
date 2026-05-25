import { useCallback } from 'react'
import { useAuth } from '@clerk/nextjs'
import { AuthContext } from '@/contexts/AuthContext'

export function ClerkAuthProvider({ children }: { children: React.ReactNode }) {
  const { getToken } = useAuth()

  const getAuthHeaders = useCallback(async (): Promise<Record<string, string>> => {
    const token = await getToken()
    if (!token) return {}
    return { Authorization: `Bearer ${token}` }
  }, [getToken])

  return <AuthContext.Provider value={getAuthHeaders}>{children}</AuthContext.Provider>
}
