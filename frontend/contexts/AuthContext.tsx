import { createContext, useContext } from 'react'

export type GetAuthHeaders = () => Promise<Record<string, string>>

const defaultGetAuthHeaders: GetAuthHeaders = async (): Promise<Record<string, string>> => {
  if (typeof window === 'undefined') return {}
  const token = sessionStorage.getItem('prototype-auth-token')
  if (!token) return {}
  return { 'X-Prototype-Auth': token }
}

export const AuthContext = createContext<GetAuthHeaders>(defaultGetAuthHeaders)

export const useAuthHeaders = () => useContext(AuthContext)
