import React, { useState, useEffect, useRef, useCallback } from 'react';

// ── Circular progress ring ──────────────────────────────────────────────────
function ProgressRing({ pct, size = 120, stroke = 8, color }) {
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ - (pct / 100) * circ;

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
        stroke={color}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={circ}
        strokeDashoffset={offset}
        style={{ transition: 'stroke-dashoffset 0.4s ease, stroke 0.4s ease' }}
      />
    </svg>
  );
}

// ── Single phase card ───────────────────────────────────────────────────────
function PhaseCard({ phase, pct, label, sublabel, status }) {
  const color = status === 'complete' ? '#6ee7b7'
              : status === 'running'  ? (phase === 1 ? '#c9a253' : '#818cf8')
              : 'rgba(255,255,255,0.12)';
  const textColor = status === 'waiting' ? '#65625a' : color;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '14px', flex: 1 }}>
      <div style={{ position: 'relative', width: '110px', height: '110px' }}>
        <ProgressRing pct={status === 'waiting' ? 0 : pct} size={110} stroke={7} color={color} />
        <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
          {status === 'idle' ? (
            <span style={{ fontSize: '22px', opacity: 0.2 }}>—</span>
          ) : (
            <>
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '20px', fontWeight: 600, color: textColor, transition: 'color 0.4s' }}>
                {Math.round(pct)}%
              </span>
              <span style={{ fontSize: '9px', color: '#65625a', marginTop: '2px', letterSpacing: '0.05em' }}>
                {status === 'complete' ? 'DONE' : (phase === 1 ? 'SYNCING' : 'TAGGING')}
              </span>
            </>
          )}
        </div>
      </div>
      <div style={{ textAlign: 'center' }}>
        <p style={{ fontSize: '12px', fontWeight: 600, color: textColor, letterSpacing: '0.08em', marginBottom: '4px', transition: 'color 0.4s' }}>
          {label}
        </p>
        {sublabel && (
          <p style={{ fontSize: '11px', color: '#65625a', lineHeight: 1.4 }}>{sublabel}</p>
        )}
      </div>
      <div style={{ width: '100%', height: '3px', background: 'rgba(255,255,255,0.05)', borderRadius: '2px', overflow: 'hidden' }}>
        <div style={{
          width: `${status === 'idle' ? 0 : pct}%`,
          height: '100%',
          borderRadius: '2px',
          background: status === 'complete'
            ? 'linear-gradient(90deg, #6ee7b7, #34d399)'
            : phase === 1
              ? 'linear-gradient(90deg, #c9a253, #dcbd76)'
              : 'linear-gradient(90deg, #818cf8, #a5b4fc)',
          transition: 'width 0.4s ease'
        }} />
      </div>
    </div>
  );
}

export default function SyncManager() {
  const [folders, setFolders] = useState([]);
  const [selectedFolder, setSelectedFolder] = useState(null);
  const [selectedFolderName, setSelectedFolderName] = useState('');
  const [currentSyncFolder, setCurrentSyncFolder] = useState(null);
  const [loading, setLoading] = useState(false);

  const [syncStatus, setSyncStatus] = useState('idle');
  const [syncPct, setSyncPct] = useState(0);
  const [syncProcessed, setSyncProcessed] = useState(0);
  const [syncTotal, setSyncTotal] = useState(0);
  const [syncSubLabel, setSyncSubLabel] = useState('');

  const [tagStatus, setTagStatus] = useState('idle');
  const [tagPct, setTagPct] = useState(0);
  const [tagDone, setTagDone] = useState(0);
  const [tagTotal, setTagTotal] = useState(0);
  const [tagSubLabel, setTagSubLabel] = useState('');

  const [errorMsg, setErrorMsg] = useState('');

  const esRef = useRef(null);
  const syncPollRef = useRef(null);

  useEffect(() => {
    fetchFolders();
    checkSyncSettings();
    fetch('/api/tag-progress')
      .then(r => r.json())
      .then(d => {
        if (d.status === 'running') {
          setSyncStatus('complete'); setSyncPct(100);
          setTagStatus('running'); setTagPct(d.pct || 0);
          setTagDone(d.done || 0); setTagTotal(d.total || 0);
          setTagSubLabel(d.message || '');
          startSSE();
        } else if (d.status === 'complete') {
          setSyncStatus('complete'); setSyncPct(100);
          setTagStatus('complete'); setTagPct(100);
          setTagSubLabel(d.message || '');
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => () => { esRef.current?.close(); clearInterval(syncPollRef.current); }, []);

  const startSSE = useCallback(() => {
    if (esRef.current) return;
    const es = new EventSource('/api/tag-progress/stream');
    esRef.current = es;
    es.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data);
        setTagStatus(d.status === 'complete' ? 'complete' : 'running');
        setTagPct(d.pct || 0);
        setTagDone(d.done || 0);
        setTagTotal(d.total || 0);
        setTagSubLabel(d.message || '');
        if (d.status === 'complete' || d.status === 'error') { es.close(); esRef.current = null; }
      } catch {}
    };
    es.onerror = () => { es.close(); esRef.current = null; };
  }, []);

  const fetchFolders = async () => {
    setLoading(true);
    try { const res = await fetch('/api/folders'); const data = await res.json(); setFolders(data.folders || []); } catch {}
    setLoading(false);
  };

  const checkSyncSettings = async () => {
    try {
      const res = await fetch('/api/sync/settings');
      const data = await res.json();
      if (data.folder_id) setCurrentSyncFolder({ id: data.folder_id, name: data.folder_name });
    } catch {}
  };

  const handleSetFolder = async () => {
    if (!selectedFolder) return;
    try {
      await fetch('/api/sync/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ folder_id: selectedFolder, folder_name: selectedFolderName }) });
      setCurrentSyncFolder({ id: selectedFolder, name: selectedFolderName });
      setSelectedFolder(null); setSelectedFolderName('');
    } catch {}
  };

  const handleStartSync = async () => {
    if (!currentSyncFolder) return;
    setErrorMsg('');
    setSyncStatus('running'); setSyncPct(0); setSyncProcessed(0); setSyncTotal(0); setSyncSubLabel('Connecting to Google Drive…');
    setTagStatus('idle'); setTagPct(0); setTagDone(0); setTagTotal(0); setTagSubLabel('');

    try {
      const res = await fetch('/api/sync/start', { method: 'POST' });
      const data = await res.json();
      if (!data.success) { setErrorMsg(data.error || 'Sync failed'); setSyncStatus('idle'); return; }

      syncPollRef.current = setInterval(async () => {
        try {
          const s = await fetch('/api/sync/status').then(r => r.json());
          const processed = s.processed || 0;
          const total = s.total || 0;
          const pct = total > 0 ? Math.round((processed / total) * 100) : 5;
          setSyncProcessed(processed); setSyncTotal(total); setSyncPct(pct);
          setSyncSubLabel(
            s.current_file ? `${processed} / ${total} — ${s.current_file.substring(0, 28)}…`
            : total > 0 ? `${processed} / ${total} images`
            : 'Listing files in Drive…'
          );
          if (!s.in_progress) {
            clearInterval(syncPollRef.current);
            setSyncStatus('complete'); setSyncPct(100);
            setSyncSubLabel(`${processed} images processed`);
            setTimeout(() => { setTagStatus('running'); setTagSubLabel('Starting AI tagging…'); startSSE(); }, 600);
          }
        } catch {}
      }, 800);
    } catch { setErrorMsg('Could not reach server.'); setSyncStatus('idle'); }
  };

  const bothDone = syncStatus === 'complete' && tagStatus === 'complete';
  const isRunning = syncStatus === 'running' || tagStatus === 'running';
  const showProgress = syncStatus !== 'idle' || tagStatus !== 'idle';

  return (
    <div style={{ padding: '40px', maxWidth: '540px', margin: '0 auto', fontFamily: "'Hanken Grotesk', system-ui, sans-serif", color: '#efeadd' }}>
      <h2 style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.14em', fontSize: '17px', marginBottom: '28px', color: '#c9a253' }}>
        IMPORT / SYNC
      </h2>

      {/* Folder selector */}
      <div style={{ marginBottom: '24px' }}>
        {currentSyncFolder && (
          <div style={{ marginBottom: '14px', padding: '12px 14px', background: 'rgba(201,162,83,0.08)', border: '1px solid rgba(201,162,83,0.25)', borderRadius: '8px', fontSize: '13px', color: '#9c988d' }}>
            Syncing from: <span style={{ color: '#c9a253', fontWeight: 600 }}>📁 {currentSyncFolder.name}</span>
          </div>
        )}
        {loading ? <p style={{ fontSize: '13px', color: '#65625a' }}>Loading folders…</p> : (
          <div style={{ display: 'flex', gap: '10px' }}>
            <select value={selectedFolder || ''} onChange={e => { const id = e.target.value; const obj = folders.find(f => f.id === id); setSelectedFolder(id); setSelectedFolderName(obj?.name || ''); }}
              style={{ flex: 1, background: '#18181b', border: '1px solid rgba(255,255,255,0.12)', borderRadius: '8px', color: '#efeadd', padding: '10px 12px', fontFamily: 'inherit', fontSize: '13px' }}>
              <option value="">Choose a folder…</option>
              {folders.map(f => <option key={f.id} value={f.id}>📁 {f.name}</option>)}
            </select>
            {selectedFolder && (
              <button onClick={handleSetFolder} style={{ background: 'rgba(201,162,83,0.15)', border: '1px solid rgba(201,162,83,0.35)', borderRadius: '8px', color: '#c9a253', padding: '10px 16px', fontFamily: 'inherit', fontSize: '13px', cursor: 'pointer' }}>Set</button>
            )}
          </div>
        )}
      </div>

      {/* Sync button */}
      <button onClick={handleStartSync} disabled={isRunning || !currentSyncFolder}
        style={{ display: 'flex', alignItems: 'center', gap: '10px', background: 'rgba(201,162,83,0.16)', border: '1px solid rgba(201,162,83,0.4)', borderRadius: '10px', color: '#c9a253', padding: '13px 22px', fontFamily: 'inherit', fontSize: '14px', fontWeight: 500, cursor: (isRunning || !currentSyncFolder) ? 'not-allowed' : 'pointer', opacity: (isRunning || !currentSyncFolder) ? 0.5 : 1, marginBottom: '28px', transition: 'opacity 0.2s' }}>
        {isRunning ? (
          <span style={{ width: '14px', height: '14px', border: '2px solid rgba(201,162,83,0.3)', borderTopColor: '#c9a253', borderRadius: '50%', display: 'inline-block', animation: 'spin 0.7s linear infinite' }} />
        ) : (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 2v6h-6M3 12a9 9 0 0115-6.7L21 8M3 22v-6h6M21 12a9 9 0 01-15 6.7L3 16"/></svg>
        )}
        {isRunning ? 'Running…' : bothDone ? 'Sync Again' : 'Sync Now'}
      </button>

      {errorMsg && <p style={{ fontSize: '13px', color: '#cf7152', marginBottom: '16px' }}>{errorMsg}</p>}

      {/* Two-phase progress cards */}
      {showProgress && (
        <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: '16px', padding: '28px 24px', animation: 'fadein 0.3s ease' }}>
          {bothDone && (
            <div style={{ textAlign: 'center', marginBottom: '24px', padding: '10px', background: 'rgba(110,231,183,0.08)', borderRadius: '8px', border: '1px solid rgba(110,231,183,0.2)' }}>
              <span style={{ fontSize: '14px', color: '#6ee7b7', fontWeight: 600 }}>✓ All done! Your library is ready.</span>
            </div>
          )}
          <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start' }}>
            <PhaseCard phase={1} pct={syncPct} status={syncStatus} label="DRIVE SYNC" sublabel={syncSubLabel} />
            <div style={{ display: 'flex', alignItems: 'center', paddingTop: '45px', color: syncStatus === 'complete' ? '#c9a253' : 'rgba(255,255,255,0.1)', fontSize: '20px', transition: 'color 0.6s', flexShrink: 0 }}>→</div>
            <PhaseCard phase={2} pct={tagPct} status={tagStatus} label="AI TAGGING"
              sublabel={tagStatus === 'idle' ? 'Starts after sync' : tagTotal > 0 ? `${tagDone} / ${tagTotal} images` : tagSubLabel} />
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
