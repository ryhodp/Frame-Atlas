import { Link } from 'react-router-dom'

function Header() {
  return (
    <header className="bg-white shadow-sm border-b border-gray-200">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
        <div className="flex justify-between items-center">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-gray-900">Frame Atlas</h1>
            <span className="text-sm text-gray-500">v1 — Skeleton</span>
          </div>
          <nav className="flex gap-6">
            <Link to="/" className="text-gray-700 hover:text-gray-900">
              Home
            </Link>
          </nav>
        </div>
      </div>
    </header>
  )
}

export default Header
