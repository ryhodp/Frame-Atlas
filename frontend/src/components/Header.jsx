import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../AuthContext'

const BASE_NAV_LINKS = [
  { to: '/', label: 'Home' },
  { to: '/decks', label: 'Decks' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/recent', label: 'Recent' },
  { to: '/favorites', label: 'Favorites' },
  { to: '/flagged', label: 'Flagged' },
]

const ADMIN_NAV_LINKS = [
  { to: '/sync', label: 'Sync' },
  { to: '/invites', label: 'Invite' },
]

function Header() {
  const location = useLocation()
  const navigate = useNavigate()
  const { user, isAdmin, logout } = useAuth()
  const NAV_LINKS = isAdmin ? [...BASE_NAV_LINKS, ...ADMIN_NAV_LINKS] : BASE_NAV_LINKS

  const handleLogout = async () => {
    await logout()
    navigate('/')
  }

  return (
    <header
      style={{
        background: '#1a1c20',
        borderBottom: '1px solid #44474f',
      }}
    >
      <div
        style={{
          maxWidth: '1400px',
          margin: '0 auto',
          padding: '16px 24px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <h1
            style={{
              fontSize: '22px',
              fontWeight: 600,
              color: '#e2e2e6',
              margin: 0,
              fontFamily: 'inherit',
            }}
          >
            Frame Atlas
          </h1>
          <span style={{ fontSize: '12px', color: '#8e9099' }}>v13</span>
        </div>

        <nav style={{ display: 'flex', alignItems: 'center', gap: '28px' }}>
          {NAV_LINKS.map(link => {
            const isActive = location.pathname === link.to
              || (link.to === '/decks' && location.pathname.startsWith('/decks'))
            return (
              <Link
                key={link.to}
                to={link.to}
                style={{
                  fontSize: '14px',
                  fontWeight: 500,
                  color: isActive ? '#d9a441' : '#e2e2e6',
                  textDecoration: 'none',
                  transition: 'color 150ms ease',
                }}
                onMouseEnter={e => { if (!isActive) e.currentTarget.style.color = '#d9a441' }}
                onMouseLeave={e => { if (!isActive) e.currentTarget.style.color = '#e2e2e6' }}
              >
                {link.label}
              </Link>
            )
          })}

          {user && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', paddingLeft: '8px', borderLeft: '1px solid #44474f' }}>
              <span style={{ fontSize: '13px', color: '#8e9099' }}>{user.username}</span>
              <button
                onClick={handleLogout}
                style={{
                  background: 'none', border: '1px solid rgba(255,255,255,0.12)', color: '#9c988d',
                  borderRadius: '6px', padding: '5px 10px', fontSize: '12px', cursor: 'pointer', fontFamily: 'inherit'
                }}
              >
                Log out
              </button>
            </div>
          )}
        </nav>
      </div>
    </header>
  )
}

export default Header
