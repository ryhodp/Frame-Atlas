import { useState } from 'react'

function Home() {
  const [images] = useState([])

  return (
    <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="mb-8">
        <h2 className="text-xl font-semibold text-gray-900 mb-4">Library</h2>
        <p className="text-sm text-gray-500">
          {images.length} images ready to explore. Sync from Google Drive in Day 2.
        </p>
      </div>

      {/* Empty state */}
      {images.length === 0 && (
        <div className="border-2 border-dashed border-gray-300 rounded-lg p-12 text-center">
          <svg
            className="mx-auto h-12 w-12 text-gray-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
            />
          </svg>
          <h3 className="mt-4 text-sm font-medium text-gray-900">No images yet</h3>
          <p className="mt-1 text-sm text-gray-500">
            Connect your Google Drive folder and sync to populate the library.
          </p>
        </div>
      )}

      {/* Image grid (will render once images exist) */}
      {images.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
          {images.map(image => (
            <div key={image.id} className="aspect-square bg-gray-200 rounded-lg">
              <img
                src={image.thumbnail_path}
                alt={image.filename}
                className="w-full h-full object-cover rounded-lg"
              />
            </div>
          ))}
        </div>
      )}
    </main>
  )
}

export default Home
