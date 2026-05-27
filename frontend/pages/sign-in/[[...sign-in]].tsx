import { SignIn } from '@clerk/nextjs'
import Head from 'next/head'
import { useRouter } from 'next/router'

export default function SignInPage() {
  const router = useRouter()
  const redirectUrl = router.query.redirect_url as string | undefined
  const q = redirectUrl ? `?redirect_url=${encodeURIComponent(redirectUrl)}` : ''

  return (
    <>
      <Head>
        <title>Sign in - Cross-check</title>
      </Head>
      <div
        style={{
          minHeight: '100vh',
          backgroundColor: '#f3f2f1',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '40px 20px',
        }}
      >
        <div style={{ width: '100%', maxWidth: '480px', marginBottom: '8px' }}>
          <nav style={{ display: 'flex', borderBottom: '2px solid #b1b4b6' }}>
            <a
              href={`/sign-up${q}`}
              style={{
                padding: '10px 20px 12px',
                fontSize: '18px',
                fontWeight: 400,
                color: '#1d70b8',
                textDecoration: 'none',
                borderBottom: '4px solid transparent',
                marginBottom: '-2px',
              }}
            >
              Create an account
            </a>
            <a
              href={`/sign-in${q}`}
              style={{
                padding: '10px 20px 12px',
                fontSize: '18px',
                fontWeight: 700,
                color: '#0b0c0c',
                textDecoration: 'none',
                borderBottom: '4px solid #f47738',
                marginBottom: '-2px',
              }}
            >
              Sign in
            </a>
          </nav>
        </div>
        <SignIn />
      </div>
    </>
  )
}
