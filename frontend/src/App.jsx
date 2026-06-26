import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import { useEffect, useState } from 'react'
import Header from './components/Header'
import Home from './pages/Home'
import SyncManager from './components/SyncManager'  // ← ADD THIS LINE
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
      <div className="min-h-screen bg-gray-50">
        <Header />
        {backendHealthy ? (
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/sync" element={<SyncManager />} />  {/* ← ADD THIS LINE */}
          </Routes>
        ) : (
          <div className="flex items-center justify-center h-96">
            <p className="text-center text-gray-600">Connecting to backend...</p>
          </div>
        )}
      </div>
    </Router>
  )
}

export default App
