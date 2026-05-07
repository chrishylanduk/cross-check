import { useEffect } from 'react'
import Head from 'next/head'
import { useRouter } from 'next/router'
import Layout from '@/components/Layout'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000'

function clearSession() {
  sessionStorage.removeItem('cross-check-session-id')
  sessionStorage.removeItem('cross-check-expires-at')
  sessionStorage.removeItem('cross-check-has-files')
  sessionStorage.removeItem('cross-check-finalised')
}

export default function Home() {
  const router = useRouter()

  useEffect(() => {
    const sessionId = sessionStorage.getItem('cross-check-session-id')
    const hasFiles = sessionStorage.getItem('cross-check-has-files') === 'true'
    if (!sessionId || !hasFiles) return

    const authToken = sessionStorage.getItem('prototype-auth-token')
    fetch(`${API_BASE}/api/collection`, {
      headers: {
        'X-Session-ID': sessionId,
        ...(authToken ? { 'X-Prototype-Auth': authToken } : {}),
      },
    }).then(async (res) => {
      if (!res.ok) {
        clearSession()
        return
      }
      const data = await res.json()
      if (!data.file_count) {
        clearSession()
        return
      }
      const finalised = sessionStorage.getItem('cross-check-finalised') === 'true'
      router.replace(finalised ? '/analyse' : '/upload')
    }).catch(() => {
      // Network error — don't redirect, leave user on landing page
    })
  }, [router])

  return (
    <Layout>
      <Head>
        <title>Cross-check - AI-assisted content audit</title>
        <meta
          name="description"
          content="Automatically recommend improvements to a large collection of written content"
        />
      </Head>

      <h1 className="govuk-heading-xl">Cross-check</h1>
          <p className="govuk-body-l">
            Automatically recommend improvements to a large collection of written content (such as a
            website or intranet) to improve its consistency, clarity, compliance and completeness.
          </p>
          <p className="govuk-body">Save hours or days compared to a manual content audit.</p>

          <a
            href="/upload"
            role="button"
            draggable="false"
            className="govuk-button govuk-button--start"
            data-module="govuk-button"
          >
            Upload content
            <svg
              className="govuk-button__start-icon"
              xmlns="http://www.w3.org/2000/svg"
              width="17.5"
              height="19"
              viewBox="0 0 33 40"
              aria-hidden="true"
              focusable="false"
            >
              <path fill="currentColor" d="M0 0h13l20 20-20 20H0l20-20z" />
            </svg>
          </a>
    </Layout>
  )
}
