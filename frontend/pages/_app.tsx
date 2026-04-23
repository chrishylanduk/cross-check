import '@/styles/globals.scss'
import { useEffect, useState } from 'react'
import type { AppProps } from 'next/app'
import PrototypePasswordModal from '@/components/PrototypePasswordModal'

export default function App({ Component, pageProps }: AppProps) {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [isChecking, setIsChecking] = useState(true)

  useEffect(() => {
    // Initialise GOV.UK Frontend components
    import('govuk-frontend/dist/govuk/govuk-frontend.min.js').then((GOVUKFrontend) => {
      GOVUKFrontend.initAll()
    })

    // Check if already authenticated
    const authToken = sessionStorage.getItem('prototype-auth-token')
    if (authToken) {
      setIsAuthenticated(true)
    }
    setIsChecking(false)
  }, [])

  const handleAuthSuccess = (token: string) => {
    sessionStorage.setItem('prototype-auth-token', token)
    setIsAuthenticated(true)
  }

  if (isChecking) {
    return null
  }

  if (!isAuthenticated) {
    return <PrototypePasswordModal onSuccess={handleAuthSuccess} />
  }

  return <Component {...pageProps} />
}
