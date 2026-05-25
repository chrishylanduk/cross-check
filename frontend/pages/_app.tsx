import '@/styles/globals.scss'
import { useEffect, useState } from 'react'
import type { AppProps } from 'next/app'
import { ClerkProvider } from '@clerk/nextjs'
import { ClerkAuthProvider } from '@/components/ClerkAuthProvider'
import PrototypePasswordModal from '@/components/PrototypePasswordModal'

const CLERK_PUBLISHABLE_KEY = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY

function PasswordGatedApp({ Component, pageProps }: AppProps) {
  const [{ isAuthenticated, isChecking }, setAuthState] = useState({
    isAuthenticated: false,
    isChecking: true,
  })

  useEffect(() => {
    // Reading from sessionStorage must happen client-side after hydration — this is the
    // correct Next.js pattern. The setState call here does not cause cascading renders.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAuthState({ isAuthenticated: !!sessionStorage.getItem('prototype-auth-token'), isChecking: false })
  }, [])

  if (isChecking) return null

  if (!isAuthenticated) {
    return (
      <PrototypePasswordModal
        onSuccess={(token) => {
          sessionStorage.setItem('prototype-auth-token', token)
          setAuthState({ isAuthenticated: true, isChecking: false })
        }}
      />
    )
  }

  return <Component {...pageProps} />
}

export default function App(props: AppProps) {
  useEffect(() => {
    import('govuk-frontend/dist/govuk/govuk-frontend.min.js').then((GOVUKFrontend) => {
      GOVUKFrontend.initAll()
    })
  }, [])

  if (CLERK_PUBLISHABLE_KEY) {
    return (
      <ClerkProvider
        publishableKey={CLERK_PUBLISHABLE_KEY}
        signInUrl="/sign-in"
        signUpUrl="/sign-up"
      >
        <ClerkAuthProvider>
          <props.Component {...props.pageProps} />
        </ClerkAuthProvider>
      </ClerkProvider>
    )
  }

  return <PasswordGatedApp {...props} />
}
