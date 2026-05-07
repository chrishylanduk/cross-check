import Head from 'next/head'
import Link from 'next/link'
import Layout from '@/components/Layout'

export default function Completeness() {
  return (
    <Layout showSessionBanner>
      <Head>
        <title>Completeness - Cross-check</title>
      </Head>

      <Link href="/analyse" className="govuk-back-link">
        Back
      </Link>

      <h1 className="govuk-heading-xl">Completeness</h1>

      <div className="govuk-inset-text">
        <p className="govuk-body" style={{ margin: 0 }}>
          This analysis type is not yet available.
        </p>
      </div>

      <p className="govuk-body">
        Completeness checking will identify content gaps in your collection based on
        questions your users are asking, such as from search queries or submitted feedback.
      </p>
    </Layout>
  )
}
