import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../AuthContext'

function formatSpendPeriod() {
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), 1)
  const monthName = now.toLocaleDateString('en-US', { month: 'long' })
  return `${monthName} 1–${now.getDate()}, ${now.getFullYear()} (calculated from usage)`
}

export default function SettingsPage() {
  const { user, isAdmin } = useAuth()
  const [spend, setSpend] = useState(null)     // {cost_usd, ...} or null while loading
  const [spendError, setSpendError] = useState('')

  useEffect(() => {
    fetch('/api/billing/spend')
      .then(async r => {
        const data = await r.json()
        if (!r.ok) { setSpendError(data.message || 'Could not load spend.'); return }
        setSpend(data)
      })
      .catch(() => setSpendError('Could not reach the server.'))
  }, [])

  return (
    <div style={{ maxWidth: '640px', margin: '0 auto', padding: '40px 24px', fontFamily: "'Hanken Grotesk', system-ui, sans-serif", color: '#efeadd' }}>
      <h1 style={{ fontSize: '28px', fontWeight: 700, margin: '0 0 6px' }}>Settings</h1>
      <p style={{ fontSize: '13px', color: '#9c988d', margin: '0 0 32px' }}>
        Account details for now — more settings are on the way.
      </p>

      <div style={{ background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px', marginBottom: '16px' }}>
        <div style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em', color: '#65625a', marginBottom: '16px' }}>
          ACCOUNT
        </div>

        <Row label="Username" value={user?.username} />
        <Row label="Email" value={user?.email || '—'} />
        <Row label="Role" value={isAdmin ? 'Admin' : 'Member'} />
      </div>

      <div style={{ background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px' }}>
        <div style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em', color: '#65625a', marginBottom: '16px' }}>
          GEMINI SPEND
        </div>

        {spendError ? (
          <p style={{ fontSize: '12.5px', color: '#9c988d', margin: 0, lineHeight: 1.5 }}>
            {spendError}{!isAdmin && (
              <> <Link to="/account" style={{ color: '#c9a253' }}>Go to Account settings →</Link></>
            )}
          </p>
        ) : spend ? (
          <>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '4px' }}>
              <span style={{ fontSize: '13px', color: '#9c988d' }}>This month</span>
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '20px', fontWeight: 600, color: '#c9a253' }}>
                ${spend.cost_usd.toFixed(2)} <span style={{ fontSize: '12px', color: '#65625a', fontWeight: 400 }}>USD</span>
              </span>
            </div>
            <p style={{ fontSize: '11.5px', color: '#65625a', margin: 0 }}>
              {formatSpendPeriod()}
            </p>
          </>
        ) : (
          <p style={{ fontSize: '13px', color: '#65625a', margin: 0 }}>Loading…</p>
        )}
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
