import Head from 'next/head'
import Layout from '@/components/Layout'

export default function Home() {
  return (
    <Layout>
      <Head>
        <title>Cross-check - AI-assisted content audit</title>
        <meta
          name="description"
          content="Automatically recommend improvements to a large collection of written content"
        />
      </Head>

      <div className="govuk-grid-row">
        <div className="govuk-grid-column-two-thirds">
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
        </div>
      </div>
    </Layout>
  )
}
