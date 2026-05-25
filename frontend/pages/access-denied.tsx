import Head from 'next/head'
import { useClerk } from '@clerk/nextjs'

export default function AccessDenied() {
  const { signOut } = useClerk()

  return (
    <>
      <Head>
        <title>Access denied - Cross-check</title>
      </Head>
      <div
        style={{
          minHeight: '100vh',
          backgroundColor: '#f3f2f1',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '40px 20px',
        }}
      >
        <div style={{ maxWidth: '400px', textAlign: 'center' }}>
          <h1 className="govuk-heading-l">Access denied</h1>
          <p className="govuk-body">Your email address is not authorised to use this service.</p>
          <button
            className="govuk-button govuk-button--secondary"
            onClick={() => signOut({ redirectUrl: '/sign-in' })}
          >
            Sign out
          </button>
        </div>
      </div>
    </>
  )
}
