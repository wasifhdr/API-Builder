import { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'

const SCALAR_SCRIPT_ID = 'scalar-cdn-script'

export default function ApiDocs() {
  const { slug } = useParams<{ slug: string }>()

  useEffect(() => {
    const container = document.getElementById('api-reference')
    if (container) {
      container.setAttribute('data-url', `/v1/apis/${slug}/openapi.json`)
    }

    // Scalar reads data-url once at script-load time. Loaded once per app
    // session; navigating between two different docs pages in the same SPA
    // session may need a manual refresh to pick up the new slug — an
    // accepted trade-off of the lightweight CDN-embed approach (no heavy
    // React wrapper, per BLUEPRINT.md §12).
    if (!document.getElementById(SCALAR_SCRIPT_ID)) {
      const script = document.createElement('script')
      script.id = SCALAR_SCRIPT_ID
      script.src = 'https://cdn.jsdelivr.net/npm/@scalar/api-reference'
      document.body.appendChild(script)
    }
  }, [slug])

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-gray-200 px-6 py-4">
        <Link to="/dashboard" className="text-sm text-gray-600 hover:text-gray-900">
          &larr; Dashboard
        </Link>
      </header>
      <div id="api-reference" data-url={`/v1/apis/${slug}/openapi.json`} />
    </div>
  )
}
