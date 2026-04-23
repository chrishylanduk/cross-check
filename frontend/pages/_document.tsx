import { Html, Head, Main, NextScript } from 'next/document'

export default function Document() {
  return (
    <Html lang="en" className="govuk-template">
      <Head />
      <body className="govuk-template__body">
        <script
          dangerouslySetInnerHTML={{
            __html: `document.body.className += ' js-enabled' + ('noModule' in HTMLScriptElement.prototype ? ' govuk-frontend-supported' : '');`,
          }}
        />
        <Main />
        <NextScript />
      </body>
    </Html>
  )
}
