import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../AuthContext'

// Minimal line icons, matching the app's existing inline-SVG convention
// (no icon library dependency) — kept intentionally simple/geometric.
const ICONS = {
  home: <path d="M3 11l9-8 9 8M5 10v10a1 1 0 001 1h4v-6h4v6h4a1 1 0 001-1V10" />,
  decks: <><path d="M12 3l9 5-9 5-9-5 9-5z" /><path d="M3 13l9 5 9-5" /></>,
  analytics: <><path d="M4 20V10M12 20V4M20 20v-7" /></>,
  recent: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 3" /></>,
  favorites: <path d="M12 3l2.9 6 6.6.6-5 4.4 1.5 6.5L12 17l-5.9 3.5L7.6 14l-5-4.4 6.6-.6L12 3z" />,
  flagged: <><path d="M5 3v18" /><path d="M5 4h11l-3 4 3 4H5" /></>,
  sync: <><path d="M21 2v6h-6" /><path d="M3 12a9 9 0 0115-6.7L21 8" /><path d="M3 22v-6h6" /><path d="M21 12a9 9 0 01-15 6.7L3 16" /></>,
  invite: <><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20a6.5 6.5 0 0113 0" /><path d="M18 8v6M15 11h6" /></>,
  library: <><path d="M4 19V5a1 1 0 011-1h5v16H5a1 1 0 01-1-1z" /><path d="M14 19V8a1 1 0 011-1h4a1 1 0 011 1v11a1 1 0 01-1 1h-4a1 1 0 01-1-1z" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 00.34 1.87l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.7 1.7 0 00-1.87-.34 1.7 1.7 0 00-1 1.55V21a2 2 0 01-4 0v-.09a1.7 1.7 0 00-1-1.55 1.7 1.7 0 00-1.87.34l-.06.06a2 2 0 11-2.83-2.83l.06-.06A1.7 1.7 0 004.6 15a1.7 1.7 0 00-1.55-1H3a2 2 0 010-4h.09A1.7 1.7 0 004.6 9a1.7 1.7 0 00-.34-1.87l-.06-.06a2 2 0 112.83-2.83l.06.06A1.7 1.7 0 009 4.6a1.7 1.7 0 001-1.55V3a2 2 0 014 0v.09a1.7 1.7 0 001 1.55 1.7 1.7 0 001.87-.34l.06-.06a2 2 0 112.83 2.83l-.06.06A1.7 1.7 0 0019.4 9a1.7 1.7 0 001.55 1H21a2 2 0 010 4h-.09a1.7 1.7 0 00-1.55 1z" /></>,
}

function Icon({ name }) {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {ICONS[name]}
    </svg>
  )
}

const BASE_NAV_LINKS = [
  { to: '/', label: 'Home', icon: 'home' },
  { to: '/decks', label: 'Decks', icon: 'decks' },
  { to: '/analytics', label: 'Analytics', icon: 'analytics' },
  { to: '/recent', label: 'Recent', icon: 'recent' },
  { to: '/favorites', label: 'Favorites', icon: 'favorites' },
  { to: '/flagged', label: 'Flagged', icon: 'flagged' },
]

const ADMIN_NAV_LINKS = [
  { to: '/sync', label: 'Sync', icon: 'sync' },
  { to: '/invites', label: 'Invite', icon: 'invite' },
]

const NON_ADMIN_NAV_LINKS = [
  { to: '/account', label: 'My Library', icon: 'library' },
]

export const SIDEBAR_WIDTH = 236

function Sidebar() {
  const location = useLocation()
  const navigate = useNavigate()
  const { user, isAdmin, logout } = useAuth()
  const roleLinks = isAdmin ? ADMIN_NAV_LINKS : NON_ADMIN_NAV_LINKS
  const NAV_LINKS = [...BASE_NAV_LINKS, ...roleLinks, { to: '/settings', label: 'Settings', icon: 'settings' }]

  const handleLogout = async () => {
    await logout()
    navigate('/')
  }

  return (
    <nav
      style={{
        width: `${SIDEBAR_WIDTH}px`,
        flexShrink: 0,
        height: '100vh',
        position: 'sticky',
        top: 0,
        background: '#111114',
        borderRight: '1px solid rgba(255,255,255,0.08)',
        display: 'flex',
        flexDirection: 'column',
        padding: '20px 14px',
        boxSizing: 'border-box',
      }}
    >
      {/* Logo */}
      <Link to="/" style={{ display: 'flex', alignItems: 'center', gap: '10px', textDecoration: 'none', padding: '0 8px', marginBottom: '28px' }}>
        <div style={{
          width: '26px', height: '26px', borderRadius: '6px',
          border: '1.5px solid #d9a441', display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: '#d9a441', fontSize: '13px', fontWeight: 700, flexShrink: 0,
        }}>F</div>
        <span style={{
          fontFamily: "'Hanken Grotesk', sans-serif", fontSize: '14px', fontWeight: 600,
          letterSpacing: '2.6px', color: '#efeadd',
        }}>
          FRAME ATLAS
        </span>
      </Link>

      {/* Nav links */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
        {NAV_LINKS.map(link => {
          const isActive = location.pathname === link.to
            || (link.to === '/decks' && location.pathname.startsWith('/decks'))
          return (
            <Link
              key={link.to}
              to={link.to}
              style={{
                display: 'flex', alignItems: 'center', gap: '11px',
                padding: '9px 10px', borderRadius: '8px',
                fontSize: '13.5px', fontWeight: 500,
                color: isActive ? '#efeadd' : '#8e9099',
                background: isActive ? 'rgba(217,164,65,0.12)' : 'transparent',
                textDecoration: 'none', transition: 'background 120ms ease, color 120ms ease',
              }}
              onMouseEnter={e => { if (!isActive) { e.currentTarget.style.background = 'rgba(255,255,255,0.04)'; e.currentTarget.style.color = '#c4c6d0' } }}
              onMouseLeave={e => { if (!isActive) { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = '#8e9099' } }}
            >
              <span style={{ color: isActive ? '#d9a441' : 'inherit', display: 'flex', flexShrink: 0 }}>
                <Icon name={link.icon} />
              </span>
              {link.label}
            </Link>
          )
        })}
      </div>

      <div style={{ flex: 1 }} />

      {/* Account footer */}
      {user && (
        <div style={{
          borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '14px',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', padding: '14px 8px 4px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '9px', minWidth: 0 }}>
            <div style={{
              width: '26px', height: '26px', borderRadius: '50%', flexShrink: 0,
              background: 'rgba(217,164,65,0.16)', border: '1px solid rgba(217,164,65,0.4)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: '10.5px', fontWeight: 600, color: '#d9a441',
            }}>
              {user.username?.slice(0, 2).toUpperCase()}
            </div>
            <span style={{ fontSize: '12.5px', color: '#9c988d', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {user.username}
            </span>
          </div>
          <button
            onClick={handleLogout}
            title="Log out"
            style={{
              background: 'none', border: '1px solid rgba(255,255,255,0.12)', color: '#65625a',
              borderRadius: '6px', padding: '5px 9px', fontSize: '11px', cursor: 'pointer', fontFamily: 'inherit', flexShrink: 0,
            }}
          >
            Log out
          </button>
        </div>
      )}
    </nav>
  )
}

export default Sidebar
