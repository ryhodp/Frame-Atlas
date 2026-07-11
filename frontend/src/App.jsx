import { BrowserRouter as Router, Routes, Route, useLocation } from 'react-router-dom'
import { useEffect, useState } from 'react'
import Header from './components/Header'
import Home from './pages/Home'
import SyncManager from './components/SyncManager'
import DecksPage from './pages/DecksPage'
import DeckDetail from './pages/DeckDetail'
import SharePage from './pages/SharePage'
import './App.css'

// Inner shell so we can read the current route (useLocation only works inside
// Router). Share pages are public-facing — no app header, just the lookbook.
function Shell() {
  const location = useLocation()
  const isSharePage = location.pathname.startsWith('/share/')
  const [backendHealthy, setBackendHealthy] = useState(false)

  useEffect(() => {
    // Check backend health on mount
    fetch('/api/health')
      .then(res => res.json())
      .then(data => {
        if (data.status === 'ok') {
          setBackendHealthy(true)
        }
      })
      .catch(err => {
        console.error('Backend health check failed:', err)
      })
  }, [])

  return (
    <div style={{ minHeight: '100vh', background: '#0a0a0b', color: '#efeadd' }}>
      {!isSharePage && <Header />}
      {backendHealthy ? (
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/sync" element={<SyncManager />} />
          <Route path="/decks" element={<DecksPage />} />
          <Route path="/decks/:id" element={<DeckDetail />} />
          <Route path="/share/:token" element={<SharePage />} />
        </Routes>
      ) : (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center', height: '24rem'
        }}>
          <p style={{ textAlign: 'center', color: '#8e9099' }}>Connecting to backend...</p>
        </div>
      )}
    </div>
  )
}

function App() {
  return (
    <Router>
      <Shell />
    </Router>
  )
}

export default App
