import { BrowserRouter as Router, Routes, Route, useLocation, Navigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import Sidebar from './components/Sidebar'
import MobileHeader from './components/MobileHeader'
import { useIsMobile } from './hooks/useIsMobile'
import Home from './pages/Home'
import SyncManager from './components/SyncManager'
import DecksPage from './pages/DecksPage'
import DeckDetail from './pages/DeckDetail'
import SharePage from './pages/SharePage'
import AnalyticsPage from './pages/AnalyticsPage'
import CollectionPage from './pages/CollectionPage'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import ForgotPasswordPage from './pages/ForgotPasswordPage'
import ResetPasswordPage from './pages/ResetPasswordPage'
import SetupPage from './pages/SetupPage'
import AdminInvitesPage from './pages/AdminInvitesPage'
import AccountPage from './pages/AccountPage'
import ConnectGuidePage from './pages/ConnectGuidePage'
import SettingsPage from './pages/SettingsPage'
import InviteAcceptPage from './pages/InviteAcceptPage'
import { AuthProvider, useAuth } from './AuthContext'
import './App.css'

// Inner shell so we can read the current route (useLocation only works inside
// Router). Share pages are public-facing — no app header, no login required,
// just the lookbook itself.
function Shell() {
  const location = useLocation()
  const isSharePage = location.pathname.startsWith('/share/')
  const [backendHealthy, setBackendHealthy] = useState(null) // null = still checking
  const [needsSetup, setNeedsSetup] = useState(null) // null = still checking
  const { user, loading: authLoading, offline, refresh } = useAuth()
  const isMobile = useIsMobile()
  const [mobileNavOpen, setMobileNavOpen] = useState(false)

  // Route changes (including ones not triggered via a Sidebar link, e.g.
  // browser back/forward) should always leave the drawer closed.
  useEffect(() => { setMobileNavOpen(false) }, [location.pathname])

  useEffect(() => {
    fetch('/api/health')
      .then(res => res.json())
      .then(data => setBackendHealthy(data.status === 'ok'))
      .catch(err => {
        // Unreachable, not unhealthy. Recorded as false rather than left
        // pending so the app can fall through to offline mode instead of
        // sitting on "Connecting to backend..." forever.
        console.warn('Backend unreachable:', err)
        setBackendHealthy(false)
      })
  }, [])

  useEffect(() => {
    if (isSharePage) return
    fetch('/api/setup/status')
      .then(res => res.json())
      .then(data => setNeedsSetup(!!data.needs_setup))
      .catch(() => setNeedsSetup(false))
  }, [isSharePage])

  // Share links never need the app shell or a login at all.
  if (isSharePage) {
    return (
      <Routes>
        <Route path="/share/:token" element={<SharePage />} />
      </Routes>
    )
  }

  if (backendHealthy === null || needsSetup === null || authLoading) {
    return (
      <div style={{ minHeight: '100vh', background: '#0a0a0b', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <p style={{ textAlign: 'center', color: '#8e9099' }}>Connecting to backend...</p>
      </div>
    )
  }

  // Server unreachable AND no session to resume — nothing useful to render, so
  // say so plainly instead of showing a login form that can't possibly work.
  if (!backendHealthy && !offline) {
    return (
      <div style={{ minHeight: '100vh', background: '#0a0a0b', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}>
        <div style={{ textAlign: 'center', maxWidth: '380px' }}>
          <p style={{ color: '#e2e2e6', fontSize: '15px', marginBottom: '8px' }}>Can't reach Frame Atlas</p>
          <p style={{ color: '#8e9099', fontSize: '13px', lineHeight: 1.6 }}>
            You appear to be offline. Decks you've opened before are available
            once you've signed in on this device at least once.
          </p>
        </div>
      </div>
    )
  }

  if (needsSetup) {
    return <SetupPage onDone={() => setNeedsSetup(false)} />
  }

  if (!user) {
    return (
      <Routes>
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="*" element={<LoginPage />} />
      </Routes>
    )
  }

  return (
    <div style={{
      display: 'flex', flexDirection: isMobile ? 'column' : 'row',
      background: '#0a0a0b', color: '#efeadd',
      height: '100vh', overflow: 'hidden',
    }}>
      {isMobile && <MobileHeader onMenuClick={() => setMobileNavOpen(true)} />}
      <Sidebar mobileOpen={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
      <div style={{ flex: 1, minWidth: 0, minHeight: 0, overflowY: 'auto' }}>
        {/* Offline: most pages need the server, so say once at the top why
            they look empty. Decks are the part that still works. */}
        {offline && (
          <div style={{
            background: 'rgba(140,150,170,0.14)',
            borderBottom: '1px solid rgba(140,150,170,0.3)',
            padding: '9px 16px', fontSize: '12px', color: '#aab2c0',
            display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap'
          }}>
            <span>⚡ Offline — showing saved decks. Search and syncing need a connection.</span>
            <button
              onClick={refresh}
              style={{
                background: 'none', border: '1px solid rgba(140,150,170,0.5)',
                color: '#aab2c0', borderRadius: '5px', padding: '3px 10px',
                fontSize: '11px', cursor: 'pointer', fontFamily: 'inherit'
              }}
            >
              Reconnect
            </button>
          </div>
        )}
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/sync" element={user.role === 'admin' ? <SyncManager /> : <Navigate to="/" replace />} />
          <Route path="/decks" element={<DecksPage />} />
          <Route path="/decks/:id" element={<DeckDetail />} />
          <Route path="/invite/:token" element={<InviteAcceptPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/favorites" element={<CollectionPage key="favorites" view="favorites" />} />
          <Route path="/flagged" element={<CollectionPage key="flagged" view="flagged" />} />
          <Route path="/recent" element={<CollectionPage key="recent" view="recent" />} />
          <Route path="/invites" element={user.role === 'admin' ? <AdminInvitesPage /> : <Navigate to="/" replace />} />
          <Route path="/account" element={<AccountPage />} />
          <Route path="/account/connect-guide" element={<ConnectGuidePage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/login" element={<Navigate to="/" replace />} />
          <Route path="/register" element={<Navigate to="/" replace />} />
          <Route path="/forgot-password" element={<Navigate to="/" replace />} />
          <Route path="/reset-password" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </div>
  )
}

function App() {
  return (
    <Router>
      <AuthProvider>
        <Shell />
      </AuthProvider>
    </Router>
  )
}

export default App
