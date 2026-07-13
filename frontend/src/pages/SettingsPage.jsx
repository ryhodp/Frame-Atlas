import { useAuth } from '../AuthContext'

export default function SettingsPage() {
  const { user, isAdmin } = useAuth()

  return (
    <div style={{ maxWidth: '640px', margin: '0 auto', padding: '40px 24px', fontFamily: "'Hanken Grotesk', system-ui, sans-serif", color: '#efeadd' }}>
      <h1 style={{ fontSize: '28px', fontWeight: 700, margin: '0 0 6px' }}>Settings</h1>
      <p style={{ fontSize: '13px', color: '#9c988d', margin: '0 0 32px' }}>
        Account details for now — more settings are on the way.
      </p>

      <div style={{ background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px' }}>
        <div style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em', color: '#65625a', marginBottom: '16px' }}>
          ACCOUNT
        </div>

        <Row label="Username" value={user?.username} />
        <Row label="Email" value={user?.email || '—'} />
        <Row label="Role" value={isAdmin ? 'Admin' : 'Member'} />
      </div>
    </div>
  )
}

function Row({ label, value }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '10px 0', borderBottom: '1px solid rgba(255,255,255,0.06)'
    }}>
      <span style={{ fontSize: '13px', color: '#9c988d' }}>{label}</span>
      <span style={{ fontSize: '13.5px', color: '#efeadd' }}>{value}</span>
    </div>
  )
}
