import { SignUp } from '@clerk/nextjs'
import Head from 'next/head'

export default function SignUpPage() {
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
          alignItems: 'center',
          justifyContent: 'center',
          padding: '40px 20px',
        }}
      >
        <SignUp />
      </div>
    </>
  )
}
