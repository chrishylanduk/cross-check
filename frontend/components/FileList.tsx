import Link from 'next/link'
import { displayFilename } from '@/lib/filename'

interface FileItem {
  name: string
  size: number
}

interface FileListProps {
  files: FileItem[]
  onDelete: (filename: string) => void
  onViewFile: (filename: string) => void
  onClear: () => void
  onFinalize: () => void
  finalised: boolean
  formatBytes: (bytes: number) => string
}

export default function FileList({
  files,
  onDelete,
  onViewFile,
  onClear,
  onFinalize,
  finalised,
  formatBytes,
}: FileListProps) {
  if (files.length === 0) {
    return (
      <div className="govuk-!-margin-bottom-6">
        <h2 className="govuk-heading-l">Content to analyse</h2>
        <p className="govuk-body">No files added yet. Use the controls above to get started.</p>
      </div>
    )
  }

  return (
    <div className="govuk-!-margin-bottom-6">
      <h2 className="govuk-heading-l">
        Content to analyse ({files.length} file{files.length !== 1 ? 's' : ''})
      </h2>

      {!finalised ? (
        <>
          <div className="govuk-warning-text">
            <span className="govuk-warning-text__icon" aria-hidden="true">!</span>
            <strong className="govuk-warning-text__text">
              <span className="govuk-visually-hidden">Warning</span>
              Once you start analysis, you will not be able to add or remove files.
            </strong>
          </div>
          <div className="govuk-button-group">
            <button
              type="button"
              className="govuk-button"
              data-module="govuk-button"
              onClick={onFinalize}
            >
              Start analysis
            </button>
            <button
              type="button"
              className="govuk-button govuk-button--secondary"
              data-module="govuk-button"
              onClick={onClear}
            >
              Clear all
            </button>
          </div>
        </>
      ) : (
        <>
          <div
            className="govuk-notification-banner"
            role="region"
            aria-labelledby="finalised-title"
          >
            <div className="govuk-notification-banner__header">
              <h2 className="govuk-notification-banner__title" id="finalised-title">
                Ready for analysis
              </h2>
            </div>
            <div className="govuk-notification-banner__content">
              <p className="govuk-body">
                Your content is ready for analysis. You cannot add or remove files.
              </p>
            </div>
          </div>
          <div className="govuk-button-group">
            <Link href="/analyse" className="govuk-button">
              Continue to analysis
            </Link>
            <button
              type="button"
              className="govuk-button govuk-button--warning"
              data-module="govuk-button"
              onClick={onClear}
            >
              Start again with new files
            </button>
          </div>
        </>
      )}

      <table className="govuk-table">
        <thead className="govuk-table__head">
          <tr className="govuk-table__row">
            <th scope="col" className="govuk-table__header">
              File name
            </th>
            <th scope="col" className="govuk-table__header govuk-table__header--numeric">
              Size
            </th>
            <th scope="col" className="govuk-table__header govuk-table__header--numeric">
              Actions
            </th>
          </tr>
        </thead>
        <tbody className="govuk-table__body">
          {files.map((file, index) => (
            <tr key={index} className="govuk-table__row">
              <td className="govuk-table__cell">{displayFilename(file.name)}</td>
              <td className="govuk-table__cell govuk-table__cell--numeric">
                {formatBytes(file.size)}
              </td>
              <td className="govuk-table__cell govuk-table__cell--numeric">
                <button
                  type="button"
                  className="govuk-link"
                  onClick={() => onViewFile(file.name)}
                  style={{ border: 'none', background: 'none', cursor: 'pointer' }}
                >
                  View<span className="govuk-visually-hidden"> {displayFilename(file.name)}</span>
                </button>
                {!finalised && (
                  <>
                    {' '}
                    <button
                      type="button"
                      className="govuk-link"
                      onClick={() => onDelete(file.name)}
                      style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#d4351c' }}
                    >
                      Delete<span className="govuk-visually-hidden"> {displayFilename(file.name)}</span>
                    </button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
