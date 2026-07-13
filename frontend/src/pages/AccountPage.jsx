import { useEffect, useRef, useState } from 'react';

// ── Circular progress ring — same visual as SyncManager's, kept single-phase
// here since Stage 2a only covers Drive sync, not AI tagging yet. ──────────
function ProgressRing({ pct, size = 96, stroke = 7, color }) {
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ - (Math.min(100, Math.max(0, pct)) / 100) * circ;
  return (
    <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth={stroke} />
      <circle
        cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke}
        strokeLinecap="round" strokeDasharray={circ} strokeDashoffset={offset}
        style={{ transition: 'stroke-dashoffset 0.4s ease' }}
      />
    </svg>
  );
}

let pickerScriptPromise = null;
function loadGooglePicker() {
  if (window.google?.picker) return Promise.resolve();
  if (!pickerScriptPromise) {
    pickerScriptPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = 'https://apis.google.com/js/api.js';
      script.onload = () => window.gapi.load('picker', { callback: resolve });
      script.onerror = () => { pickerScriptPromise = null; reject(new Error('Failed to load Google Picker')); };
      document.body.appendChild(script);
    });
  }
  return pickerScriptPromise;
}

export default function AccountPage() {
  const [config, setConfig] = useState(null);
  const [signedIn, setSignedIn] = useState(null); // null = still checking
  const [folder, setFolder] = useState(null); // {id, name} or null
  const [pickerBusy, setPickerBusy] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncPct, setSyncPct] = useState(0);
  const [syncMsg, setSyncMsg] = useState('');
  const [syncDone, setSyncDone] = useState(false);
  const [error, setError] = useState('');
  const pollRef = useRef(null);

  useEffect(() => {
    fetch('/api/config').then(r => r.json()).then(setConfig).catch(() => {});
    fetch('/api/auth/status').then(r => r.json()).then(d => setSignedIn(!!d.signed_in)).catch(() => setSignedIn(false));
    fetch('/api/sync/settings').then(r => r.json())
      .then(d => { if (d.folder_id) setFolder({ id: d.folder_id, name: d.folder_name }); })
      .catch(() => {});

    // Coming back from the Google sign-in redirect
    const params = new URLSearchParams(window.location.search);
    if (params.get('signed_in') === '1') {
      setSignedIn(true);
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  useEffect(() => () => clearInterval(pollRef.current), []);

  const connectGoogle = () => {
    window.location.href = '/api/auth/google/login';
  };

  const openFolderPicker = async () => {
    if (!config?.google_picker_api_key) {
      setError('The folder picker isn’t set up yet — ask Ryan to finish the Google Cloud setup step.');
      return;
    }
    setError('');
    setPickerBusy(true);
    try {
      await loadGooglePicker();
      const tokenRes = await fetch('/api/drive/picker-token');
      if (tokenRes.status === 401) {
        setSignedIn(false);
        setPickerBusy(false);
        return;
      }
      const { access_token } = await tokenRes.json();

      const view = new window.google.picker.DocsView(window.google.picker.ViewId.FOLDERS)
        .setSelectFolderEnabled(true)
        .setIncludeFolders(true);

      const picker = new window.google.picker.PickerBuilder()
        .addView(view)
        .setOAuthToken(access_token)
        .setDeveloperKey(config.google_picker_api_key)
        .setCallback(async (data) => {
          if (data.action === window.google.picker.Action.PICKED) {
            const doc = data.docs[0];
            try {
              await fetch('/api/sync/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ folder_id: doc.id, folder_name: doc.name })
              });
              setFolder({ id: doc.id, name: doc.name });
              setSyncDone(false);
            } catch {
              setError('Picked a folder, but saving it failed — try again.');
            }
          }
        })
        .build();
      picker.setVisible(true);
    } catch {
      setError('Could not open the Google folder picker — try again in a moment.');
    }
    setPickerBusy(false);
  };

  const startSync = async () => {
    if (!folder || syncing) return;
    setError('');
    setSyncing(true);
    setSyncDone(false);
    setSyncPct(0);
    setSyncMsg('Starting…');
    try {
      const res = await fetch('/api/sync/start', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        setError(data.message || (data.error === 'not_signed_in' ? 'Connect Google Drive first.' : data.error) || 'Could not start sync');
        setSyncing(false);
        return;
      }

      pollRef.current = setInterval(async () => {
        try {
          const s = await fetch('/api/sync/status').then(r => r.json());
          const processed = s.processed || 0;
          const total = s.total || 0;
          setSyncPct(total > 0 ? Math.round((processed / total) * 100) : 5);
          setSyncMsg(
            s.current_file ? `${processed} / ${total} — ${s.current_file.slice(0, 32)}…`
              : total > 0 ? `${processed} / ${total} images`
              : 'Listing files in Drive…'
          );
          if (!s.in_progress) {
            clearInterval(pollRef.current);
            setSyncing(false);
            setSyncDone(true);
            setSyncPct(100);
            setSyncMsg(`${processed} image${processed === 1 ? '' : 's'} processed`);
            if (s.errors?.length) setError(s.errors[s.errors.length - 1]);
          }
        } catch {}
      }, 800);
    } catch {
      setError('Could not reach the server.');
      setSyncing(false);
    }
  };

  return (
    <div style={{ maxWidth: '640px', margin: '0 auto', padding: '40px 24px', fontFamily: "'Hanken Grotesk', system-ui, sans-serif", color: '#efeadd' }}>
      <h1 style={{ fontSize: '28px', fontWeight: 700, margin: '0 0 6px' }}>Your Library</h1>
      <p style={{ fontSize: '13px', color: '#9c988d', margin: '0 0 32px' }}>
        Connect your own Google Drive folder to bring your photos into Frame Atlas — they're
        private to you, separate from the shared library.
      </p>

      {/* Step 1: connect Google */}
      <div style={{ background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px', marginBottom: '16px' }}>
        <div style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em', color: '#65625a', marginBottom: '10px' }}>
          STEP 1 · GOOGLE DRIVE
        </div>
        {signedIn === null ? (
          <p style={{ fontSize: '13px', color: '#65625a' }}>Checking…</p>
        ) : signedIn ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13.5px', color: '#7fb87f' }}>
            <span>✓</span> Google account connected
          </div>
        ) : (
          <button onClick={connectGoogle} style={primaryBtnStyle()}>
            Connect Google Drive
          </button>
        )}
      </div>

      {/* Step 2: pick a folder */}
      <div style={{
        background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px', marginBottom: '16px',
        opacity: signedIn ? 1 : 0.5, pointerEvents: signedIn ? 'auto' : 'none'
      }}>
        <div style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em', color: '#65625a', marginBottom: '10px' }}>
          STEP 2 · CHOOSE A FOLDER
        </div>
        {folder && (
          <div style={{
            marginBottom: '12px', padding: '10px 12px', background: 'rgba(201,162,83,0.08)',
            border: '1px solid rgba(201,162,83,0.25)', borderRadius: '8px', fontSize: '13px', color: '#9c988d'
          }}>
            Selected: <span style={{ color: '#c9a253', fontWeight: 600 }}>📁 {folder.name}</span>
          </div>
        )}
        <button onClick={openFolderPicker} disabled={pickerBusy} style={secondaryBtnStyle(pickerBusy)}>
          {pickerBusy ? 'Opening…' : folder ? 'Choose a different folder' : 'Choose folder'}
        </button>
      </div>

      {/* Step 3: sync */}
      <div style={{
        background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px',
        opacity: folder ? 1 : 0.5, pointerEvents: folder ? 'auto' : 'none'
      }}>
        <div style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em', color: '#65625a', marginBottom: '14px' }}>
          STEP 3 · SYNC
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
          <div style={{ position: 'relative', width: '96px', height: '96px', flexShrink: 0 }}>
            <ProgressRing pct={syncing || syncDone ? syncPct : 0} color={syncDone ? '#6ee7b7' : '#c9a253'} />
            <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              {(syncing || syncDone) && (
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '17px', fontWeight: 600, color: syncDone ? '#6ee7b7' : '#c9a253' }}>
                  {syncPct}%
                </span>
              )}
            </div>
          </div>
          <div style={{ flex: 1 }}>
            <button onClick={startSync} disabled={!folder || syncing} style={{ ...primaryBtnStyle(!folder || syncing), marginBottom: '8px' }}>
              {syncing ? 'Syncing…' : syncDone ? 'Sync Again' : 'Sync Now'}
            </button>
            {syncMsg && <p style={{ fontSize: '12px', color: '#9c988d', margin: 0 }}>{syncMsg}</p>}
          </div>
        </div>
      </div>

      {error && (
        <div style={{
          marginTop: '16px', padding: '10px 12px', background: 'rgba(255,180,171,0.1)',
          border: '1px solid rgba(255,180,171,0.35)', color: '#ffb4ab', borderRadius: '8px', fontSize: '12.5px'
        }}>
          {error}
        </div>
      )}

      <p style={{ fontSize: '11.5px', color: '#65625a', marginTop: '24px', lineHeight: 1.5 }}>
        Synced photos show up unlabeled for now — AI tagging with your own Gemini key is coming soon.
        Your photos are private to you; nobody else can search or see them.
      </p>
    </div>
  );
}

function primaryBtnStyle(disabled) {
  return {
    background: disabled ? 'rgba(217,164,65,0.2)' : '#d9a441',
    color: disabled ? '#8e9099' : '#3d2f00',
    border: 'none', borderRadius: '8px', padding: '10px 18px',
    fontSize: '13.5px', fontWeight: 600, cursor: disabled ? 'default' : 'pointer', fontFamily: 'inherit'
  };
}

function secondaryBtnStyle(disabled) {
  return {
    background: 'none', border: '1px solid rgba(217,164,65,0.4)', color: disabled ? '#65625a' : '#d9a441',
    borderRadius: '8px', padding: '9px 16px', fontSize: '13px',
    cursor: disabled ? 'default' : 'pointer', fontFamily: 'inherit'
  };
}
