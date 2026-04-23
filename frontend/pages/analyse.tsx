import Head from 'next/head'
import Layout from '@/components/Layout'

export default function Analyse() {
  return (
    <Layout showSessionBanner>
      <Head>
        <title>Choose analysis type - Cross-check</title>
      </Head>

      <h1 className="govuk-heading-xl">Choose type of analysis</h1>

      <div className="govuk-grid-row">
        <div className="govuk-grid-column-two-thirds">
          <div
            style={{
              border: '1px solid #b1b4b6',
              padding: '20px 20px 10px',
              marginBottom: '20px',
            }}
          >
            <h2 className="govuk-heading-m">Check for inconsistencies</h2>
            <p className="govuk-body">
              Find where documents contradict each other or cover the same topic inconsistently.
            </p>
            <a href="/analyse/inconsistencies" className="govuk-button">
              Start
            </a>
          </div>
        </div>
      </div>
    </Layout>
  )
}
