import React, { useState, useEffect, useRef, useCallback } from 'react';

// ── Circular progress ring ──────────────────────────────────────────────────
function ProgressRing({ pct, size = 120, stroke = 8 }) {
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ - (pct / 100) * circ;
  const isDone = pct >= 100;

  return (
    <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
      <circle
        cx={size / 2} cy={size / 2} r={r}
        fill="none"
        stroke="rgba(255,255,255,0.07)"
        strokeWidth={stroke}
      />
      <circle
        cx={size / 2} cy={size / 2} r={r}
        fill="none"
        stroke={isDone ? '#6ee7b7' : '#c9a253'}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={circ}
        strokeDashoffset={offset}
        style={{ transition: 'stroke-dashoffset 0.4s ease, stroke 0.4s ease' }}
      />
    </svg>
  );
}

export default function SyncManager() {
  const [folders, setFolders] = useState([]);
  const [selectedFolder, setSelectedFolder] = useState(null);
  const [selectedFolderName, setSelectedFolderName] = useState('');
  const [currentSyncFolder, setCurrentSyncFolder] = useState(null);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState('');
  const [syncError, setSyncError] = useState('');

  const [tagStatus, setTagStatus] = useState('idle');
  const [tagPct, setTagPct] = useState(0);
  const [tagDone, setTagDone] = useState(0);
  const [tagTotal, setTagTotal] = useState(0);
  const [tagMsg, setTagMsg] = useState('');

  const esRef = useRef(null);

  // ── On mount: load folders, check settings, check if tagging already running
  useEffect(() => {
    fetchFolders();
    checkSyncSettings();
    fetch('/api/tag-progress')
      .then(r => r.json())
      .then(d => {
        setTagStatus(d.status);
        setTagPct(d.pct || 0);
        setTagDone(d.done || 0);
        setTagTotal(d.total || 0);
        setTagMsg(d.message || '');
        if (d.status === 'running') startSSE();
      })
      .catch(() => {});
  }, []);

  useEffect(() => () => { esRef.current?.close(); }, []);

  const startSSE = useCallback(() => {
    if (esRef.current) return;
    const es = new EventSource('/api/tag-progress/stream');
    esRef.current = es;
    es.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data);
        setTagStatus(d.status);
        setTagPct(d.pct || 0);
        setTagDone(d.done || 0);
        setTagTotal(d.total || 0);
        setTagMsg(d.message || '');
        if (d.status === 'complete' || d.status === 'error') {
          es.close();
          esRef.current = null;
        }
      } catch {}
    };
    es.onerror = () => { es.close(); esRef.current = null; };
  }, []);

  const fetchFolders = async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/folders');
      const data = await res.json();
      setFolders(data.folders || []);
    } catch {}
    setLoading(false);
  };

  const checkSyncSettings = async () => {
    try {
      const res = await fetch('/api/sync/settings');
      const data = await res.json();
      if (data.folder_id) {
        setCurrentSyncFolder({ id: data.folder_id, name: data.folder_name });
      }
    } catch {}
  };

  const handleSetFolder = async () => {
    if (!selectedFolder) return;
    try {
      await fetch('/api/sync/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_id: selectedFolder, folder_name: selectedFolderName })
      });
      setCurrentSyncFolder({ id: selectedFolder, name: selectedFolderName });
      setSelectedFolder(null);
      setSelectedFolderName('');
    } catch {}
  };

  const handleStartSync = async () => {
    if (!currentSyncFolder) return;
    setSyncing(true);
    setSyncMsg('Syncing with Google Drive…');
    setSyncError('');
    setTagStatus('idle');
    setTagPct(0);
    setTagDone(0);
    setTagTotal(0);
    setTagMsg('');

    try {
      const res = await fetch('/api/sync/start', { method: 'POST' });
      const data = await res.json();
      if (!data.success) {
        setSyncError(data.error || 'Sync failed');
        setSyncing(false);
        return;
      }

      // Poll until sync finishes, then start SSE for tagging
      const poll = setInterval(async () => {
        try {
          const s = await fetch('/api/sync/status').then(r => r.json());
          if (!s.in_progress) {
            clearInterval(poll);
            setSyncing(false);
            const newCount = s.processed || 0;
            setSyncMsg(`Drive sync done — ${newCount} images processed.`);
            // Tagging auto-starts on the server; connect SSE to watch it
            setTimeout(() => {
              setTagStatus('running');
              setTagMsg('Starting tagging queue…');
              startSSE();
            }, 800);
          }
        } catch {}
      }, 1000);
    } catch (err) {
      setSyncError('Could not reach server.');
      setSyncing(false);
    }
  };

  const showRing = tagStatus === 'running' || tagStatus === 'complete';

  return (
    <div style={{
      padding: '40px',
      maxWidth: '520px',
      margin: '0 auto',
      fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
      color: '#efeadd'
    }}>
      <h2 style={{
        fontFamily: "'Cinzel', serif",
        letterSpacing: '0.14em',
        fontSize: '17px',
        marginBottom: '28px',
        color: '#c9a253'
      }}>
        IMPORT / SYNC
      </h2>

      {/* Folder selector */}
      <div style={{ marginBottom: '24px' }}>
        {currentSyncFolder && (
          <div style={{
            marginBottom: '14px',
            padding: '12px 14px',
            background: 'rgba(201,162,83,0.08)',
            border: '1px solid rgba(201,162,83,0.25)',
            borderRadius: '8px',
            fontSize: '13px',
            color: '#9c988d'
          }}>
            Syncing from: <span style={{ color: '#c9a253', fontWeight: 600 }}>📁 {currentSyncFolder.name}</span>
          </div>
        )}

        {loading ? (
          <p style={{ fontSize: '13px', color: '#65625a' }}>Loading folders…</p>
        ) : (
          <div style={{ display: 'flex', gap: '10px' }}>
            <select
              value={selectedFolder || ''}
              onChange={e => {
                const id = e.target.value;
                const obj = folders.find(f => f.id === id);
                setSelectedFolder(id);
                setSelectedFolderName(obj?.name || '');
              }}
              style={{
                flex: 1,
                background: '#18181b',
                border: '1px solid rgba(255,255,255,0.12)',
                borderRadius: '8px',
                color: '#efeadd',
                padding: '10px 12px',
                fontFamily: 'inherit',
                fontSize: '13px'
              }}
            >
              <option value="">Choose a folder…</option>
              {folders.map(f => (
                <option key={f.id} value={f.id}>📁 {f.name}</option>
              ))}
            </select>
            {selectedFolder && (
              <button
                onClick={handleSetFolder}
                style={{
                  background: 'rgba(201,162,83,0.15)',
                  border: '1px solid rgba(201,162,83,0.35)',
                  borderRadius: '8px',
                  color: '#c9a253',
                  padding: '10px 16px',
                  fontFamily: 'inherit',
                  fontSize: '13px',
                  cursor: 'pointer'
                }}
              >
                Set
              </button>
            )}
          </div>
        )}
      </div>

      {/* Sync Now button */}
      <button
        onClick={handleStartSync}
        disabled={syncing || !currentSyncFolder || tagStatus === 'running'}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          background: 'rgba(201,162,83,0.16)',
          border: '1px solid rgba(201,162,83,0.4)',
          borderRadius: '10px',
          color: '#c9a253',
          padding: '13px 22px',
          fontFamily: 'inherit',
          fontSize: '14px',
          fontWeight: 500,
          cursor: (syncing || !currentSyncFolder || tagStatus === 'running') ? 'not-allowed' : 'pointer',
          opacity: (syncing || !currentSyncFolder || tagStatus === 'running') ? 0.5 : 1,
          marginBottom: '20px',
          transition: 'opacity 0.2s'
        }}
      >
        {syncing ? (
          <span style={{
            width: '14px', height: '14px',
            border: '2px solid rgba(201,162,83,0.3)',
            borderTopColor: '#c9a253',
            borderRadius: '50%',
            display: 'inline-block',
            animation: 'spin 0.7s linear infinite'
          }} />
        ) : (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 2v6h-6M3 12a9 9 0 0115-6.7L21 8M3 22v-6h6M21 12a9 9 0 01-15 6.7L3 16"/>
          </svg>
        )}
        {syncing ? 'Syncing Drive…' : 'Sync Now'}
      </button>

      {syncMsg && <p style={{ fontSize: '13px', color: '#9c988d', marginBottom: '8px' }}>{syncMsg}</p>}
      {syncError && <p style={{ fontSize: '13px', color: '#cf7152', marginBottom: '8px' }}>{syncError}</p>}

      {/* Tagging progress ring */}
      {showRing && (
        <div style={{
          marginTop: '28px',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: '16px',
          background: 'rgba(255,255,255,0.03)',
          border: '1px solid rgba(255,255,255,0.07)',
          borderRadius: '14px',
          padding: '32px 24px',
          animation: 'fadein 0.3s ease'
        }}>
          {/* Ring */}
          <div style={{ position: 'relative', width: '120px', height: '120px' }}>
            <ProgressRing pct={tagPct} />
            <div style={{
              position: 'absolute', inset: 0,
              display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center'
            }}>
              <span style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: '22px',
                fontWeight: 600,
                color: tagStatus === 'complete' ? '#6ee7b7' : '#c9a253',
                transition: 'color 0.4s'
              }}>
                {tagPct}%
              </span>
              <span style={{ fontSize: '10px', color: '#65625a', marginTop: '2px', letterSpacing: '0.05em' }}>
                {tagStatus === 'complete' ? 'DONE' : 'TAGGING'}
              </span>
            </div>
          </div>

          {/* Status text */}
          <div style={{ textAlign: 'center' }}>
            <p style={{
              fontSize: '14px',
              fontWeight: 500,
              color: tagStatus === 'complete' ? '#6ee7b7' : '#efeadd',
              marginBottom: '6px',
              transition: 'color 0.4s'
            }}>
              {tagStatus === 'complete' ? 'Sync Complete!' : 'Syncing…'}
            </p>
            <p style={{ fontSize: '12px', color: '#65625a', lineHeight: 1.5 }}>{tagMsg}</p>
            {tagTotal > 0 && tagStatus === 'running' && (
              <p style={{
                fontSize: '11px', color: '#65625a', marginTop: '6px',
                fontFamily: "'JetBrains Mono', monospace"
              }}>
                {tagDone} / {tagTotal}
              </p>
            )}
          </div>

          {/* Progress bar */}
          <div style={{ width: '100%', height: '4px', background: 'rgba(255,255,255,0.06)', borderRadius: '2px', overflow: 'hidden' }}>
            <div style={{
              width: `${tagPct}%`,
              height: '100%',
              borderRadius: '2px',
              background: tagStatus === 'complete'
                ? 'linear-gradient(90deg, #6ee7b7, #34d399)'
                : 'linear-gradient(90deg, #c9a253, #dcbd76)',
              transition: 'width 0.4s ease, background 0.4s ease'
            }} />
          </div>
        </div>
      )}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes fadein { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
      `}</style>
    </div>
  );
}
