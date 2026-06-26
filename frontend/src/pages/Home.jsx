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
        <p className="text-red-500
