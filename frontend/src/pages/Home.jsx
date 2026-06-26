import { useState, useEffect } from 'react'

function Home() {
  const [images, setImages] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch('/api/images')
      .then(res => res.json())
      .then(data => {
        setImages(data.images || [])
        setLoading(false)
      })
      .catch(err => {
        setError('Failed to load images')
        setLoading(false)
      })
  }, [])

  return (
    <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="mb-8">
        <h2 className="text-xl font-semibold text-gray-900 mb-1">Library</h2>
        <p className="text-sm text-gray-500">
          {loading ? 'Loading...' : `${images.length} images`}
        </p>
      </div>

      {error && (
        <p className="text-red-500 text-sm">{error}</p>
      )}

      {!loading && images.length === 0 && (
        <div className="border-2 border-dashed border-gray-300 rounded-lg p-12 text-center">
          <svg className="mx-auto h-12 w-12 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
          <h3 className="mt-4 text-sm font-medium text-gray-900">No images yet</h3>
          <p className="mt-1 text-sm text-gray-500">Go to /sync to pull images from Google Drive.</p>
        </div>
      )}

      {!loading && images.length > 0 && (
        <div style={{ columns: '5 160px', columnGap: '8px' }}>
          {images.map(image => (
            <div
              key={image.id}
              style={{
                breakInside: 'avoid',
                marginBottom: '8px',
                borderRadius: '6px',
                overflow: 'hidden',
                background: '#e5e7eb',
              }}
            >
              <img
                src={image.thumbnail}
                alt={image.filename}
                style={{ width: '100%', display: 'block' }}
              />
            </div>
          ))}
        </div>
      )}
    </main>
  )
}

export default Home
