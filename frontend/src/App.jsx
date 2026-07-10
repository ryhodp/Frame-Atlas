import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import { useEffect, useState } from 'react'
import Header from './components/Header'
import Home from './pages/Home'
import SyncManager from './components/SyncManager'  // ← ADD THIS LINE
import DecksPage from './pages/DecksPage'
import DeckDetail from './pages/DeckDetail'
import './App.css'

function App() {
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
    <Router>
      <div style={{ minHeight: '100vh', background: '#0a0a0b', color: '#efeadd' }}>
        <Header />
        {backendHealthy ? (
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/sync" element={<SyncManager />} />
            <Route path="/decks" element={<DecksPage />} />
            <Route path="/decks/:id" element={<DeckDetail />} />
          </Routes>
        ) : (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center', height: '24rem'
          }}>
            <p style={{ textAlign: 'center', color: '#8e9099' }}>Connecting to backend...</p>
          </div>
        )}
      </div>
    </Router>
  )
}

export default App
