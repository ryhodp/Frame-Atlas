import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../AuthContext'
import { useOfflineCache } from '../hooks/useOfflineCache'

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
  const [googleStatus, setGoogleStatus] = useState(null) // null = loading, true = connected, false = not connected
  const [disconnecting, setDisconnecting] = useState(false)
  const cache = useOfflineCache()
  const [cachedDecks, setCachedDecks] = useState([])
  const [clearing, setClearing] = useState(false)
  const [backups, setBackups] = useState(null)   // null = loading, [] = none yet
  const [keepCount, setKeepCount] = useState(2)
  const [backupError, setBackupError] = useState('')
  const [backingUp, setBackingUp] = useState(false)

  // V48: sync used to make you pick a folder from a dropdown every visit,
  // even though there's only ever been one to pick — /api/folders is
  // hardcoded to the single admin folder. This is just where that one-time
  // connection lives now that the actual "Sync Now" action moved to Home.
  const [syncSetup, setSyncSetup] = useState(null) // /api/account/setup-status payload
  const [connectingFolder, setConnectingFolder] = useState(false)
  const [connectFolderError, setConnectFolderError] = useState('')

  const loadBackupStatus = () => {
    fetch('/api/backups/status')
      .then(async r => {
        const data = await r.json()
        if (!r.ok) { setBackupError(data.error || 'Could not load backup status.'); return }
        setBackups(data.backups)
        setKeepCount(data.keep_count)
      })
      .catch(() => setBackupError('Could not reach the server.'))
  }

  useEffect(() => {
    fetch('/api/billing/spend')
      .then(async r => {
        const data = await r.json()
        if (!r.ok) { setSpendError(data.message || 'Could not load spend.'); return }
        setSpend(data)
      })
      .catch(() => setSpendError('Could not reach the server.'))

    fetch('/api/auth/status')
      .then(r => r.json())
      .then(data => setGoogleStatus(!!data.signed_in))
      .catch(() => setGoogleStatus(false))

    if (isAdmin) {
      loadBackupStatus()
      loadSyncSetup()
    }
  }, [isAdmin])

  const loadSyncSetup = () => {
    fetch('/api/account/setup-status')
      .then(r => r.json())
      .then(setSyncSetup)
      .catch(() => {})
  }

  const handleConnectDefaultFolder = async () => {
    setConnectingFolder(true)
    setConnectFolderError('')
    try {
      const foldersRes = await fetch('/api/folders')
      const { folders } = await foldersRes.json()
      const folder = folders?.[0]
      if (!folder) {
        setConnectFolderError('No folder is configured on the server.')
        return
      }
      const res = await fetch('/api/sync/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_id: folder.id, folder_name: folder.name })
      })
      if (!res.ok) {
        setConnectFolderError('Could not save the folder connection.')
        return
      }
      loadSyncSetup()
    } catch {
      setConnectFolderError('Could not reach the server.')
    }
    setConnectingFolder(false)
  }

  const handleBackupNow = async () => {
    setBackingUp(true)
    setBackupError('')
    try {
      const res = await fetch('/api/backups/run', { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) { setBackupError(data.error || 'Backup failed.'); return }
      loadBackupStatus()
    } catch (e) {
      setBackupError('Could not reach the server.')
    }
    setBackingUp(false)
  }

  useEffect(() => {
    if (cache.ready) {
      cache.getCachedDecks().then(setCachedDecks)
    }
  }, [cache, cache.ready])

  const handleClearCache = async () => {
    setClearing(true)
    await cache.clearCache()
    setCachedDecks([])
    setClearing(false)
  }

  const handleGoogleConnect = () => {
    window.location.href = '/api/auth/google/login'
  }

  const handleGoogleDisconnect = async () => {
    setDisconnecting(true)
    try {
      const res = await fetch('/api/auth/google/disconnect', { method: 'POST' })
      if (res.ok) {
        setGoogleStatus(false)
      }
    } catch (e) {
      console.error('Failed to disconnect Google', e)
    }
    setDisconnecting(false)
  }

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

      {isAdmin && (
        <div style={{ background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px', marginBottom: '16px' }}>
          <div style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em', color: '#65625a', marginBottom: '16px' }}>
            SYNC SOURCE
          </div>

          {syncSetup === null ? (
            <p style={{ fontSize: '13px', color: '#65625a', margin: 0 }}>Loading…</p>
          ) : syncSetup.folder_connected ? (
            <>
              <Row label="Folder" value={`📁 ${syncSetup.folder_name}`} />
              <Row
                label="Last synced"
                value={syncSetup.last_sync
                  ? new Date(syncSetup.last_sync + 'Z').toLocaleString('en-US', { month: 'long', day: 'numeric', hour: 'numeric', minute: '2-digit' })
                  : 'Never yet'}
              />
              <p style={{ fontSize: '11.5px', color: '#65625a', margin: '10px 0 0', lineHeight: 1.5 }}>
                To sync now, use the ⟲ button next to Duplicate Scan on Home — it runs in the
                background and lets you know when it's done.
              </p>
            </>
          ) : (
            <>
              <p style={{ fontSize: '12.5px', color: '#9c988d', margin: '0 0 12px', lineHeight: 1.5 }}>
                No folder connected yet. Frame Atlas only ever reads from the one folder
                shared with its service account — connect it once here.
              </p>
              {connectFolderError && (
                <p style={{ fontSize: '12px', color: '#ffb4ab', margin: '0 0 10px' }}>{connectFolderError}</p>
              )}
              <button
                onClick={handleConnectDefaultFolder}
                disabled={connectingFolder}
                style={{
                  background: 'rgba(184,206,161,0.18)', border: '1px solid rgba(184,206,161,0.5)',
                  color: '#b8cea1', borderRadius: '6px', padding: '7px 14px', fontSize: '12px',
                  cursor: 'pointer', fontFamily: 'inherit', opacity: connectingFolder ? 0.6 : 1
                }}
              >
                {connectingFolder ? 'Connecting…' : 'Connect Folder'}
              </button>
            </>
          )}
        </div>
      )}

      {isAdmin && (
        <div style={{ background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px', marginBottom: '16px' }}>
          <div style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em', color: '#65625a', marginBottom: '16px' }}>
            GOOGLE CONNECTION
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 0' }}>
            <div>
              <span style={{ fontSize: '13px', color: '#9c988d' }}>Upload token</span>
              <p style={{ fontSize: '11.5px', color: '#65625a', margin: '4px 0 0' }}>
                For uploading photos directly to Drive
              </p>
            </div>
            {googleStatus === null ? (
              <span style={{ fontSize: '12px', color: '#65625a' }}>Checking…</span>
            ) : googleStatus ? (
              <button
                onClick={handleGoogleDisconnect}
                disabled={disconnecting}
                style={{
                  background: 'rgba(255,180,171,0.12)',
                  border: '1px solid rgba(255,180,171,0.3)',
                  color: '#ffb4ab',
                  borderRadius: '6px',
                  padding: '7px 14px',
                  fontSize: '12px',
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                  opacity: disconnecting ? 0.6 : 1
                }}
              >
                {disconnecting ? 'Disconnecting…' : 'Disconnect'}
              </button>
            ) : (
              <button
                onClick={handleGoogleConnect}
                style={{
                  background: 'rgba(184,206,161,0.18)',
                  border: '1px solid rgba(184,206,161,0.5)',
                  color: '#b8cea1',
                  borderRadius: '6px',
                  padding: '7px 14px',
                  fontSize: '12px',
                  cursor: 'pointer',
                  fontFamily: 'inherit'
                }}
              >
                Connect
              </button>
            )}
          </div>
        </div>
      )}

      {isAdmin && (
        <div style={{ background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px', marginBottom: '16px' }}>
          <div style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em', color: '#65625a', marginBottom: '16px' }}>
            DATABASE BACKUP
          </div>

          {backupError ? (
            <p style={{ fontSize: '12.5px', color: '#9c988d', margin: '0 0 12px', lineHeight: 1.5 }}>
              {backupError}
            </p>
          ) : backups === null ? (
            <p style={{ fontSize: '13px', color: '#65625a', margin: '0 0 12px' }}>Loading…</p>
          ) : backups.length === 0 ? (
            <p style={{ fontSize: '12.5px', color: '#9c988d', margin: '0 0 12px', lineHeight: 1.5 }}>
              No backup yet — the first one runs automatically within a day, then
              once a month after that. Your tags, decks, and bookmarks live only
              in this database (the photos themselves stay safe on Drive either way).
            </p>
          ) : (
            <div style={{ marginBottom: '12px' }}>
              <p style={{ fontSize: '13px', color: '#9c988d', margin: '0 0 8px' }}>
                Last backup: <span style={{ color: '#efeadd', fontWeight: 600 }}>
                  {new Date(backups[0].created_at + 'Z').toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}
                </span>
              </p>
              <p style={{ fontSize: '12px', color: '#65625a', margin: 0, lineHeight: 1.5 }}>
                Keeping the newest {keepCount} of {backups.length}, saved to a "_Backups" folder in Drive. Runs automatically once a month.
              </p>
            </div>
          )}

          <button
            onClick={handleBackupNow}
            disabled={backingUp}
            style={{
              background: 'rgba(184,206,161,0.18)',
              border: '1px solid rgba(184,206,161,0.5)',
              color: '#b8cea1',
              borderRadius: '6px',
              padding: '7px 14px',
              fontSize: '12px',
              cursor: 'pointer',
              fontFamily: 'inherit',
              opacity: backingUp ? 0.6 : 1
            }}
          >
            {backingUp ? 'Backing up…' : 'Backup Now'}
          </button>
        </div>
      )}

      <div style={{ background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px', marginBottom: '16px' }}>
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

      <div style={{ background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px' }}>
        <div style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em', color: '#65625a', marginBottom: '16px' }}>
          OFFLINE CACHE
        </div>

        <div style={{ marginBottom: '12px' }}>
          <p style={{ fontSize: '13px', color: '#9c988d', margin: '0 0 8px' }}>
            Cached decks: <span style={{ color: '#efeadd', fontWeight: 600 }}>{cachedDecks.length}</span>
          </p>
          <p style={{ fontSize: '12px', color: '#65625a', margin: 0, lineHeight: 1.5 }}>
            {cachedDecks.length === 0
              ? 'No decks cached yet. Open a deck to cache it for offline viewing.'
              : `${cachedDecks.length} deck${cachedDecks.length !== 1 ? 's' : ''} stored locally. When offline, you can still view cached decks.`}
          </p>
        </div>

        <button
          onClick={handleClearCache}
          disabled={clearing || cachedDecks.length === 0}
          style={{
            background: cachedDecks.length > 0 ? 'rgba(255,180,171,0.12)' : 'rgba(255,180,171,0.06)',
            border: '1px solid rgba(255,180,171,0.3)',
            color: cachedDecks.length > 0 ? '#ffb4ab' : '#8e7f77',
            borderRadius: '6px',
            padding: '7px 14px',
            fontSize: '12px',
            cursor: cachedDecks.length > 0 ? 'pointer' : 'default',
            fontFamily: 'inherit',
            opacity: clearing ? 0.6 : 1
          }}
        >
          {clearing ? 'Clearing…' : 'Clear Cache'}
        </button>
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
