import { useEffect, useRef, useState } from 'react';

export default function UploadButton({ onUploaded }) {
  const [signedIn, setSignedIn] = useState(null); // null = still checking
  const [uploading, setUploading] = useState(false);
  const [results, setResults] = useState(null); // array of {filename, status, ...}
  const [pendingFiles, setPendingFiles] = useState({}); // filename -> File, for "upload anyway"
  const fileInputRef = useRef(null);

  useEffect(() => {
    fetch('/api/auth/status')
      .then(res => res.json())
      .then(data => setSignedIn(!!data.signed_in))
      .catch(() => setSignedIn(false));

    // Coming back from the Google sign-in redirect
    const params = new URLSearchParams(window.location.search);
    if (params.get('signed_in') === '1') {
      setSignedIn(true);
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  const doUpload = async (files, force) => {
    if (!files.length) return;
    setUploading(true);
    const formData = new FormData();
    files.forEach(f => formData.append('files', f));
    try {
      const res = await fetch(`/api/upload${force ? '?force=true' : ''}`, {
        method: 'POST',
        body: formData
      });
      if (res.status === 401) {
        setSignedIn(false);
        setUploading(false);
        return;
      }
      const data = await res.json();
      setResults(prev => {
        const byName = new Map((prev || []).map(r => [r.filename, r]));
        (data.results || []).forEach(r => byName.set(r.filename, r));
        return Array.from(byName.values());
      });
      const nextPending = { ...pendingFiles };
      files.forEach(f => { nextPending[f.name] = f; });
      setPendingFiles(nextPending);

      if ((data.results || []).some(r => r.status === 'uploaded')) {
        onUploaded?.();
      }
    } catch {
      setResults(prev => [...(prev || []), ...files.map(f => ({
        filename: f.name, status: 'error', message: 'Upload failed — check your connection.'
      }))]);
    }
    setUploading(false);
  };

  const handleClick = () => {
    if (signedIn === false) {
      window.location.href = '/api/auth/google/login';
      return;
    }
    fileInputRef.current?.click();
  };

  const handleFilesSelected = (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = ''; // allow re-selecting the same file later
    setResults([]);
    doUpload(files, false);
  };

  const uploadAnyway = (filename) => {
    const file = pendingFiles[filename];
    if (!file) return;
    doUpload([file], true);
  };

  const dismissResult = (filename) => {
    setResults(prev => prev.filter(r => r.filename !== filename));
  };

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        onChange={handleFilesSelected}
        style={{ display: 'none' }}
      />
      <button
        onClick={handleClick}
        disabled={uploading}
        title={signedIn === false ? 'Sign in with Google to upload' : 'Upload photos'}
        style={{
          height: '46px', width: '46px',
          background: '#18181b',
          border: '1px solid rgba(255,255,255,0.12)',
          borderRadius: '10px',
          cursor: uploading ? 'default' : 'pointer',
          color: '#9c988d',
          fontSize: '16px',
          opacity: uploading ? 0.6 : 1
        }}
      >
        {uploading ? (
          <span style={{
            display: 'inline-block', width: '14px', height: '14px',
            border: '2px solid rgba(201,162,83,0.25)', borderTopColor: '#c9a253',
            borderRadius: '50%', animation: 'spin 0.7s linear infinite'
          }} />
        ) : '⬆'}
      </button>

      {results && results.length > 0 && (
        <>
          <div
            onClick={() => setResults(null)}
            style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 999 }}
          />
          <div style={{
            position: 'fixed', top: '8vh', left: '50%', transform: 'translateX(-50%)',
            width: 'min(640px, 92vw)', maxHeight: '84vh', overflowY: 'auto',
            background: '#0a0a0b', border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: '14px', zIndex: 1000, padding: '20px',
            color: '#efeadd', fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
            boxShadow: '0 30px 80px rgba(0,0,0,0.7)'
          }}>
            <div style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              marginBottom: '16px'
            }}>
              <div style={{ fontSize: '15px', fontWeight: 600 }}>Upload results</div>
              <button
                onClick={() => setResults(null)}
                style={{ background: 'none', border: 'none', color: '#65625a', cursor: 'pointer', fontSize: '20px' }}
              >×</button>
            </div>

            {results.map(r => (
              <div key={r.filename} style={{
                display: 'flex', alignItems: 'flex-start', gap: '10px',
                padding: '10px 0', borderBottom: '1px solid rgba(255,255,255,0.06)'
              }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: '12.5px', color: '#efeadd',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
                  }}>{r.filename}</div>

                  {r.status === 'uploaded' && (
                    <div style={{ fontSize: '11px', color: '#7fb87f', marginTop: '3px' }}>
                      ✓ Uploaded — tagging now
                    </div>
                  )}
                  {r.status === 'error' && (
                    <div style={{ fontSize: '11px', color: '#cf7152', marginTop: '3px' }}>
                      {r.message || 'Upload failed'}
                    </div>
                  )}
                  {r.status === 'duplicate' && (
                    <div style={{ marginTop: '6px' }}>
                      <div style={{ fontSize: '11px', color: '#dcbd76', marginBottom: '6px' }}>
                        Looks like a duplicate of an image already in your library:
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <img
                          src={r.existing.thumbnail}
                          alt={r.existing.filename}
                          style={{ width: '48px', height: '48px', objectFit: 'cover', borderRadius: '5px' }}
                        />
                        <div style={{ fontSize: '10.5px', color: '#9c988d', flex: 1 }}>{r.existing.filename}</div>
                        <button
                          onClick={() => uploadAnyway(r.filename)}
                          disabled={uploading}
                          style={{
                            background: 'none', border: '1px solid rgba(201,162,83,0.35)',
                            color: '#dcbd76', borderRadius: '5px', padding: '5px 10px',
                            fontSize: '10.5px', cursor: 'pointer', fontFamily: 'inherit'
                          }}
                        >
                          Upload anyway
                        </button>
                        <button
                          onClick={() => dismissResult(r.filename)}
                          style={{
                            background: 'none', border: '1px solid rgba(255,255,255,0.15)',
                            color: '#9c988d', borderRadius: '5px', padding: '5px 10px',
                            fontSize: '10.5px', cursor: 'pointer', fontFamily: 'inherit'
                          }}
                        >
                          Skip
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </>
  );
}
