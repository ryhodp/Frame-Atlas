import { Link, useLocation } from 'react-router-dom'

const NAV_LINKS = [
  { to: '/', label: 'Home' },
  { to: '/decks', label: 'Decks' },
  { to: '/sync', label: 'Sync' },
]

function Header() {
  const location = useLocation()

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
          <span style={{ fontSize: '12px', color: '#8e9099' }}>v11</span>
        </div>

        <nav style={{ display: 'flex', gap: '28px' }}>
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
        </nav>
      </div>
    </header>
  )
}

export default Header
