import { BrowserRouter as Router, Routes, Route, useLocation, Navigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import Sidebar from './components/Sidebar'
import MobileHeader from './components/MobileHeader'
import { useIsMobile } from './hooks/useIsMobile'
import Home from './pages/Home'
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
import { ToastProvider } from './ToastContext'
import { SyncProvider } from './SyncContext'
import { PAGE_BG, offlineAccent, onSurface, onSurfaceCool, onSurfaceWarm, outline, withAlpha } from './theme'
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

  // Share links never need the app shell or a login at all. They DO still
  // need the page background: rendering outside the shell below meant nothing
  // painted one, so the browser default (white) showed through behind text
  // colored for a dark UI — barely legible, on the one page clients see.
  // Set here rather than inside SharePage so it covers that page's loading
  // and error states too, not just a successfully-loaded deck.
  if (isSharePage) {
    return (
      <div style={{ minHeight: '100vh', background: PAGE_BG }}>
        <Routes>
          <Route path="/share/:token" element={<SharePage />} />
        </Routes>
      </div>
    )
  }

  if (backendHealthy === null || needsSetup === null || authLoading) {
    return (
      <div style={{ minHeight: '100vh', background: PAGE_BG, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <p style={{ textAlign: 'center', color: outline }}>Connecting to backend...</p>
      </div>
    )
  }

  // Server unreachable AND no session to resume — nothing useful to render, so
  // say so plainly instead of showing a login form that can't possibly work.
  if (!backendHealthy && !offline) {
    return (
      <div style={{ minHeight: '100vh', background: PAGE_BG, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}>
        <div style={{ textAlign: 'center', maxWidth: '380px' }}>
          <p style={{ color: onSurface, fontSize: '15px', marginBottom: '8px' }}>Can't reach Frame Atlas</p>
          <p style={{ color: outline, fontSize: '13px', lineHeight: 1.6 }}>
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
      background: PAGE_BG, color: onSurfaceWarm,
      height: '100vh', overflow: 'hidden',
    }}>
      {isMobile && <MobileHeader onMenuClick={() => setMobileNavOpen(true)} />}
      <Sidebar mobileOpen={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
      <div style={{ flex: 1, minWidth: 0, minHeight: 0, overflowY: 'auto' }}>
        {/* Offline: most pages need the server, so say once at the top why
            they look empty. Decks are the part that still works. */}
        {offline && (
          <div style={{
            background: withAlpha(offlineAccent,0.14),
            borderBottom: `1px solid ${withAlpha(offlineAccent,0.3)}`,
            padding: '9px 16px', fontSize: '12px', color: onSurfaceCool,
            display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap'
          }}>
            <span>⚡ Offline — showing saved decks. Search and syncing need a connection.</span>
            <button
              onClick={refresh}
              style={{
                background: 'none', border: `1px solid ${withAlpha(offlineAccent,0.5)}`,
                color: onSurfaceCool, borderRadius: '5px', padding: '3px 10px',
                fontSize: '11px', cursor: 'pointer', fontFamily: 'inherit'
              }}
            >
              Reconnect
            </button>
          </div>
        )}
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/sync" element={<Navigate to="/settings" replace />} />
          <Route path="/decks" element={<DecksPage />} />
          <Route path="/decks/:id" element={<DeckDetail />} />
          <Route path="/invite/:token" element={<InviteAcceptPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/favorites" element={<CollectionPage key="favorites" view="favorites" />} />
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
        <ToastProvider>
          <SyncProvider>
            <Shell />
          </SyncProvider>
        </ToastProvider>
      </AuthProvider>
    </Router>
  )
}

export default App
