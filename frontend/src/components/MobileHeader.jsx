import { Link } from 'react-router-dom'

// Sticky top bar shown only below the mobile breakpoint — replaces the
// fixed sidebar with a hamburger button that opens it as a drawer.
export default function MobileHeader({ onMenuClick }) {
  return (
    <header style={{
      height: '52px',
      flexShrink: 0,
      display: 'flex',
      alignItems: 'center',
      gap: '12px',
      padding: '0 14px',
      background: '#111114',
      borderBottom: '1px solid rgba(255,255,255,0.08)',
      position: 'sticky',
      top: 0,
      zIndex: 150,
    }}>
      <button
        onClick={onMenuClick}
        aria-label="Open menu"
        style={{
          width: '38px', height: '38px', flexShrink: 0,
          background: 'transparent', border: '1px solid rgba(255,255,255,0.12)',
          borderRadius: '8px', color: '#efeadd', cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M3 6h18M3 12h18M3 18h18" />
        </svg>
      </button>

      <Link to="/" style={{ display: 'flex', alignItems: 'center', gap: '9px', textDecoration: 'none' }}>
        <div style={{
          width: '22px', height: '22px', borderRadius: '5px',
          border: '1.5px solid #d9a441', display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: '#d9a441', fontSize: '11px', fontWeight: 700, flexShrink: 0,
        }}>F</div>
        <span style={{
          fontFamily: "'Hanken Grotesk', sans-serif", fontSize: '12.5px', fontWeight: 600,
          letterSpacing: '2.2px', color: '#efeadd',
        }}>
          FRAME ATLAS
        </span>
      </Link>
    </header>
  )
}
