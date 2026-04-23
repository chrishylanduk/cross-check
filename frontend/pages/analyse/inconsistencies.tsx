import Head from 'next/head'
import Link from 'next/link'
import Layout from '@/components/Layout'

export default function Inconsistencies() {
  return (
    <Layout showSessionBanner>
      <Head>
        <title>Check for inconsistencies - Cross-check</title>
      </Head>

      <Link href="/analyse" className="govuk-back-link">
        Back
      </Link>

      <h1 className="govuk-heading-xl">Check for inconsistencies</h1>

      <div className="govuk-notification-banner" role="region" aria-labelledby="coming-soon-title">
        <div className="govuk-notification-banner__header">
          <h2 className="govuk-notification-banner__title" id="coming-soon-title">
            Coming soon
          </h2>
        </div>
        <div className="govuk-notification-banner__content">
          <p className="govuk-body">This analysis type is not yet available.</p>
        </div>
      </div>
    </Layout>
  )
}
