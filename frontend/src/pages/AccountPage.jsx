import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../AuthContext';

// ── Circular progress ring — same visual as SyncManager's. ─────────────────
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

// V17: personal libraries connect via "share your folder with the robot
// email, paste the folder link" — the service account reads it directly.
// The old Google-Picker path was removed: with the narrow drive.file
// permission, picking a folder never granted access to the files inside it,
// so it could not have synced anyone's existing images. The Google sign-in
// itself survives below as an optional step, still needed for the Upload
// button (which CREATES files, which drive.file does allow).
export default function AccountPage() {
  const { isAdmin } = useAuth();
  const [setup, setSetup] = useState(null);        // /api/account/setup-status payload
  const [copied, setCopied] = useState(false);
  const [folderInput, setFolderInput] = useState('');
  const [connecting, setConnecting] = useState(false);
  const [connectMsg, setConnectMsg] = useState(null); // {ok, text}
  const [signedIn, setSignedIn] = useState(null);  // Google OAuth (uploads) — null = checking
  const [syncing, setSyncing] = useState(false);
  const [syncPct, setSyncPct] = useState(0);
  const [syncMsg, setSyncMsg] = useState('');
  const [syncDone, setSyncDone] = useState(false);
  const [error, setError] = useState('');
  const pollRef = useRef(null);

  // Gemini key (non-admin only — admin rides the shared Railway key).
  const [geminiKey, setGeminiKey] = useState('');
  const [keyStatus, setKeyStatus] = useState(null);
  const [savingKey, setSavingKey] = useState(false);
  const [keyMsg, setKeyMsg] = useState('');
  const [tagging, setTagging] = useState(false);
  const [tagMsg, setTagMsg] = useState('');
  const tagPollRef = useRef(null);

  const refreshSetup = () =>
    fetch('/api/account/setup-status').then(r => r.json()).then(setSetup).catch(() => {});

  useEffect(() => {
    refreshSetup();
    fetch('/api/auth/status').then(r => r.json()).then(d => setSignedIn(!!d.signed_in)).catch(() => setSignedIn(false));

    // Coming back from the Google sign-in redirect (uploads)
    const params = new URLSearchParams(window.location.search);
    if (params.get('signed_in') === '1') {
      setSignedIn(true);
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  useEffect(() => {
    if (isAdmin) return;
    fetch('/api/account/gemini-key').then(r => r.json()).then(setKeyStatus).catch(() => setKeyStatus({ has_key: false }));
  }, [isAdmin]);

  useEffect(() => () => { clearInterval(pollRef.current); clearInterval(tagPollRef.current); }, []);

  const copyRobotEmail = async () => {
    if (!setup?.service_account_email) return;
    try {
      await navigator.clipboard.writeText(setup.service_account_email);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard blocked — the email is visible and selectable anyway
    }
  };

  const connectFolder = async () => {
    const value = folderInput.trim();
    if (!value || connecting) return;
    setConnecting(true);
    setConnectMsg(null);
    try {
      const res = await fetch('/api/sync/connect-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder: value })
      });
      const data = await res.json();
      if (!res.ok) {
        setConnectMsg({ ok: false, text: data.error || 'Could not connect that folder.' });
      } else {
        setConnectMsg({
          ok: true,
          text: `Connected “${data.folder_name}”` +
            (typeof data.image_count === 'number' ? ` — ${data.image_count} image${data.image_count === 1 ? '' : 's'} found.` : '.')
        });
        setFolderInput('');
        setSyncDone(false);
        refreshSetup();
      }
    } catch {
      setConnectMsg({ ok: false, text: 'Could not reach the server.' });
    }
    setConnecting(false);
  };

  const startSync = async () => {
    if (!setup?.folder_connected || syncing) return;
    setError('');
    setSyncing(true);
    setSyncDone(false);
    setSyncPct(0);
    setSyncMsg('Starting…');
    try {
      const res = await fetch('/api/sync/start', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        setError(data.message || data.error || 'Could not start sync');
        setSyncing(false);
        return;
      }

      pollRef.current = setInterval(async () => {
        try {
          const s = await fetch('/api/sync/status').then(r => r.json());
          if (s.yours === false) {
            setSyncMsg('Another sync is running — yours will need a retry in a few minutes.');
            return;
          }
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
            refreshSetup();
          }
        } catch {}
      }, 800);
    } catch {
      setError('Could not reach the server.');
      setSyncing(false);
    }
  };

  const saveGeminiKey = async () => {
    const key = geminiKey.trim();
    if (!key || savingKey) return;
    setSavingKey(true);
    setKeyMsg('');
    try {
      const res = await fetch('/api/account/gemini-key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key })
      });
      const data = await res.json();
      if (!res.ok) {
        setKeyMsg(data.error || 'Could not save that key — try again.');
      } else {
        setKeyStatus({ has_key: true, key_last4: data.key_last4 });
        setGeminiKey('');
      }
    } catch {
      setKeyMsg('Could not reach the server.');
    }
    setSavingKey(false);
  };

  const startTaggingMine = async () => {
    if (tagging) return;
    setTagging(true);
    setTagMsg('Starting…');
    try {
      const res = await fetch('/api/tag/mine', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        setTagMsg(data.error || 'Could not start tagging.');
        setTagging(false);
        return;
      }

      tagPollRef.current = setInterval(async () => {
        try {
          const s = await fetch('/api/tag-progress/mine').then(r => r.json());
          setTagMsg(s.message || 'Tagging…');
          if (!s.running) {
            clearInterval(tagPollRef.current);
            setTagging(false);
          }
        } catch {}
      }, 800);
    } catch {
      setTagMsg('Could not reach the server.');
      setTagging(false);
    }
  };

  const connectGoogle = () => {
    window.location.href = '/api/auth/google/login';
  };

  const robotEmail = setup?.service_account_email;

  return (
    <div style={{ maxWidth: '640px', margin: '0 auto', padding: '40px 24px', fontFamily: "'Hanken Grotesk', system-ui, sans-serif", color: '#efeadd' }}>
      <h1 style={{ fontSize: '28px', fontWeight: 700, margin: '0 0 6px' }}>Your Library</h1>
      <p style={{ fontSize: '13px', color: '#9c988d', margin: '0 0 32px' }}>
        Bring your own Google Drive folder into Frame Atlas — your photos stay
        private to you, separate from everyone else's.
      </p>

      {/* Step 1: share the folder with the robot email */}
      <div style={{ background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px', marginBottom: '16px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
          <div style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em', color: '#65625a' }}>
            STEP 1 · SHARE YOUR FOLDER
          </div>
          <Link to="/account/connect-guide" style={{ fontSize: '12px', color: '#c9a253', textDecoration: 'none' }}>
            Need help? →
          </Link>
        </div>
        <p style={{ fontSize: '12.5px', color: '#9c988d', margin: '0 0 12px', lineHeight: 1.5 }}>
          In Google Drive, right-click your images folder → <b style={{ color: '#efeadd' }}>Share</b> →
          paste this email as a <b style={{ color: '#efeadd' }}>Viewer</b>:
        </p>
        {robotEmail ? (
          <div style={{ display: 'flex', gap: '8px', alignItems: 'stretch' }}>
            <code style={{
              flex: 1, background: '#0f1013', border: '1px solid rgba(255,255,255,0.12)',
              borderRadius: '8px', padding: '9px 12px', fontSize: '12px', color: '#dcbd76',
              fontFamily: "'JetBrains Mono', monospace", overflowWrap: 'anywhere'
            }}>
              {robotEmail}
            </code>
            <button onClick={copyRobotEmail} style={secondaryBtnStyle(false)}>
              {copied ? '✓ Copied' : 'Copy'}
            </button>
          </div>
        ) : (
          <p style={{ fontSize: '13px', color: '#65625a', margin: 0 }}>Loading…</p>
        )}
      </div>

      {/* Step 2: paste the folder link */}
      <div style={{ background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px', marginBottom: '16px' }}>
        <div style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em', color: '#65625a', marginBottom: '10px' }}>
          STEP 2 · PASTE THE FOLDER LINK
        </div>
        {setup?.folder_connected && (
          <div style={{
            marginBottom: '12px', padding: '10px 12px', background: 'rgba(201,162,83,0.08)',
            border: '1px solid rgba(201,162,83,0.25)', borderRadius: '8px', fontSize: '13px', color: '#9c988d'
          }}>
            Connected: <span style={{ color: '#c9a253', fontWeight: 600 }}>📁 {setup.folder_name}</span>
          </div>
        )}
        <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
          <input
            value={folderInput}
            onChange={e => setFolderInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') connectFolder(); }}
            placeholder={setup?.folder_connected ? 'Connect a different folder…' : 'https://drive.google.com/drive/folders/…'}
            style={{
              flex: 1, background: '#0f1013', border: '1px solid rgba(255,255,255,0.12)',
              borderRadius: '8px', padding: '9px 12px', fontSize: '13px', color: '#efeadd',
              fontFamily: 'inherit', outline: 'none'
            }}
          />
          <button onClick={connectFolder} disabled={!folderInput.trim() || connecting} style={primaryBtnStyle(!folderInput.trim() || connecting)}>
            {connecting ? 'Checking…' : 'Connect'}
          </button>
        </div>
        <p style={{ fontSize: '11px', color: '#65625a', margin: connectMsg ? '0 0 10px' : 0 }}>
          Open the folder in Drive and copy the web address from the browser bar.
        </p>
        {connectMsg && (
          <p style={{ fontSize: '12.5px', color: connectMsg.ok ? '#7fb87f' : '#ffb4ab', margin: 0, lineHeight: 1.5 }}>
            {connectMsg.text}
          </p>
        )}
      </div>

      {/* Step 3: sync */}
      <div style={{
        background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px',
        opacity: setup?.folder_connected ? 1 : 0.5, pointerEvents: setup?.folder_connected ? 'auto' : 'none'
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
            <button onClick={startSync} disabled={!setup?.folder_connected || syncing} style={{ ...primaryBtnStyle(!setup?.folder_connected || syncing), marginBottom: '8px' }}>
              {syncing ? 'Syncing…' : syncDone ? 'Sync Again' : 'Sync Now'}
            </button>
            {syncMsg && <p style={{ fontSize: '12px', color: '#9c988d', margin: 0 }}>{syncMsg}</p>}
            {setup?.image_cap && setup?.image_count > 0 && (
              <p style={{ fontSize: '11px', color: '#65625a', margin: '6px 0 0' }}>
                {setup.image_count} / {setup.image_cap} images in your library
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Step 4: AI tagging key (non-admin only) */}
      {!isAdmin && (
        <div style={{ background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px', marginTop: '16px' }}>
          <div style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em', color: '#65625a', marginBottom: '10px' }}>
            STEP 4 · YOUR GEMINI API KEY
          </div>
          <p style={{ fontSize: '12.5px', color: '#9c988d', margin: '0 0 14px', lineHeight: 1.5 }}>
            Add your own Gemini API key to AI-tag your synced photos and use natural-language search.
            Calls run on your own key, not the shared one — nothing gets tagged until you add one.
          </p>

          {keyStatus?.has_key && (
            <div style={{
              marginBottom: '12px', padding: '10px 12px', background: 'rgba(127,184,127,0.08)',
              border: '1px solid rgba(127,184,127,0.25)', borderRadius: '8px', fontSize: '13px', color: '#7fb87f'
            }}>
              ✓ Key saved (•••••••{keyStatus.key_last4})
            </div>
          )}

          <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
            <input
              type="password"
              value={geminiKey}
              onChange={e => setGeminiKey(e.target.value)}
              placeholder={keyStatus?.has_key ? 'Replace saved key…' : 'Paste your Gemini API key'}
              style={{
                flex: 1, background: '#0f1013', border: '1px solid rgba(255,255,255,0.12)',
                borderRadius: '8px', padding: '9px 12px', fontSize: '13px', color: '#efeadd',
                fontFamily: 'inherit', outline: 'none'
              }}
            />
            <button onClick={saveGeminiKey} disabled={!geminiKey.trim() || savingKey} style={primaryBtnStyle(!geminiKey.trim() || savingKey)}>
              {savingKey ? 'Saving…' : 'Save'}
            </button>
          </div>
          <p style={{ fontSize: '11px', color: '#65625a', margin: '0 0 14px' }}>
            (optional) — skip this and your photos stay untagged but still searchable by filename.
          </p>

          {keyMsg && <p style={{ fontSize: '12px', color: '#ffb4ab', margin: '0 0 14px' }}>{keyMsg}</p>}

          {keyStatus?.has_key && (
            <>
              <button onClick={startTaggingMine} disabled={tagging} style={{ ...secondaryBtnStyle(tagging), marginBottom: '8px' }}>
                {tagging ? 'Tagging…' : 'Tag my photos'}
              </button>
              {tagMsg && <p style={{ fontSize: '12px', color: '#9c988d', margin: 0 }}>{tagMsg}</p>}
            </>
          )}
        </div>
      )}

      {/* Optional: Google sign-in, only needed for the Upload button */}
      <div style={{ background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px', marginTop: '16px' }}>
        <div style={{ fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em', color: '#65625a', marginBottom: '10px' }}>
          OPTIONAL · UPLOADS
        </div>
        <p style={{ fontSize: '12.5px', color: '#9c988d', margin: '0 0 14px', lineHeight: 1.5 }}>
          To use the ⬆ Upload button (adding single images straight from this device into your
          Drive folder), connect your Google account. Not needed for syncing.
        </p>
        {signedIn === null ? (
          <p style={{ fontSize: '13px', color: '#65625a', margin: 0 }}>Checking…</p>
        ) : signedIn ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13.5px', color: '#7fb87f' }}>
            <span>✓</span> Google account connected
          </div>
        ) : (
          <button onClick={connectGoogle} style={secondaryBtnStyle(false)}>
            Connect Google Drive
          </button>
        )}
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
        Your photos are private to you; nobody else can search or see them. Sharing as Viewer means
        Frame Atlas can only ever read your folder — it can't change or delete anything in your Drive.
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
