import '@/styles/globals.scss'
import { useEffect } from 'react'

export default function App({ Component, pageProps }) {
  useEffect(() => {
    // Initialize GOV.UK Frontend components
    import('govuk-frontend/dist/govuk/govuk-frontend.min.js').then((GOVUKFrontend) => {
      GOVUKFrontend.initAll()
    })
  }, [])

  return <Component {...pageProps} />
}
