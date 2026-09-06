import { useEffect, useRef, useState, forwardRef, useImperativeHandle } from 'react';
import { useIsMobile } from '../hooks/useIsMobile';
import { useToast } from '../ToastContext';
import { black, danger, onSurfaceFaint, onSurfaceMuted, onSurfaceWarm, primaryDim, success, surfaceContainerDark, surfaceContainerLowest, warning, white, withAlpha } from '../theme';

const UploadButton = forwardRef(function UploadButton({ onUploaded }, ref) {
  const isMobile = useIsMobile();
  const { showToast } = useToast();
  const [signedIn, setSignedIn] = useState(null); // null = still checking
  const [panelOpen, setPanelOpen] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0); // 0-100
  const [results, setResults] = useState([]); // array of {filename, status, ...}
  const [pendingFiles, setPendingFiles] = useState({}); // filename -> File, for "upload anyway"
  const fileInputRef = useRef(null);

  // ── V79: uploads run in the BACKGROUND ────────────────────────────────────
  // The old flow held a full-screen modal open for the whole request and
  // refused to close while `uploading` was true, so the app was unusable until
  // the server finished. Worse, the progress bar it showed only ever measured
  // the browser SENDING the bytes — it hit 100% the instant the last byte left,
  // and then sat there spinning through all the slow server-side work
  // (thumbnail, palette, duplicate check, the Drive write for every photo).
  // "100% and still spinning, can't touch anything" was that gap, not a hang.
  //
  // Now: the panel closes the moment a batch starts, a small pill reports what's
  // in flight, and the outcome arrives as a toast — the same instant-close +
  // background-toast pattern CropModal / DuplicateReview / bulk delete already
  // use (V35). `bgCount` is how many photos are in flight right now; the pill
  // hides itself at 0.
  const [bgCount, setBgCount] = useState(0);
  // Duplicates found by a background batch, parked until the toast's "Review"
  // button reopens the panel with just those. Kept in a ref as well so the
  // toast's onClick (captured at show time) always sees the latest set.
  const parkedDupesRef = useRef([]);

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

  // POST one batch. Resolves to the server's per-file result array (or an
  // array of synthetic error rows), so both the foreground and background
  // callers can decide for themselves how to present the outcome.
  const postBatch = (files, force, { onProgress } = {}) => new Promise((resolve) => {
    const errorRows = (message) => files.map(f => ({ filename: f.name, status: 'error', message }));
    const formData = new FormData();
    files.forEach(f => formData.append('files', f));

    const xhr = new XMLHttpRequest();

    // NOTE: this measures bytes leaving the browser only. The server still has
    // real work to do after it reaches 100% — never present it as "done".
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) onProgress?.(Math.round((e.loaded / e.total) * 100));
    });

    xhr.addEventListener('load', () => {
      let data;
      try {
        data = JSON.parse(xhr.responseText);
      } catch (e) {
        console.error('Upload parse error:', e);
        resolve(errorRows('Upload failed — check your connection and try again.'));
        return;
      }
      if (xhr.status === 401) {
        setSignedIn(false);
        resolve(errorRows(data.message || 'Sign in with Google first.'));
        return;
      }
      if (!xhr.status || xhr.status >= 400) {
        resolve(errorRows(data.message || data.error || 'Upload failed.'));
        return;
      }
      resolve(data.results || []);
    });

    xhr.addEventListener('error', () => {
      resolve(errorRows('Upload failed — check your connection and try again.'));
    });

    xhr.open('POST', `/api/upload${force ? '?force=true' : ''}`);
    xhr.send(formData);
  });

  // Summarise a finished background batch as one toast. Duplicates aren't
  // auto-decided — they're parked and offered behind a Review button, because
  // "upload it anyway" is a judgement only Ryan can make.
  const reportBatch = (results) => {
    const uploaded = results.filter(r => r.status === 'uploaded');
    const dupes = results.filter(r => r.status === 'duplicate');
    const errors = results.filter(r => r.status === 'error');

    const parts = [];
    if (uploaded.length) parts.push(`${uploaded.length} uploaded`);
    if (dupes.length) parts.push(`${dupes.length} possible duplicate${dupes.length === 1 ? '' : 's'}`);
    if (errors.length) parts.push(`${errors.length} failed`);
    const message = parts.length ? parts.join(' · ') : 'Nothing to upload.';

    if (dupes.length) {
      parkedDupesRef.current = dupes;
      // Held open (duration 0) until acted on — it's the only route back to a
      // decision, and a 4s auto-dismiss would silently drop those photos.
      showToast(message, errors.length ? 'error' : 'info', 0, {
        label: 'Review',
        onClick: () => {
          setResults(parkedDupesRef.current);
          setPanelOpen(true);
        },
      });
    } else if (errors.length) {
      // Errors carry their reason; show the first verbatim rather than a count
      // alone (the V45 lesson — a bare count destroys the only diagnosis).
      showToast(`${message} — ${errors[0].message || 'unknown error'}`, 'error', 12000);
    } else {
      showToast(message, 'success');
    }
  };

  // Runs a batch in the background: no modal, a pill while it's in flight, a
  // toast at the end. Never awaited by the caller — that's the whole point.
  const uploadInBackground = async (files) => {
    setBgCount(n => n + files.length);
    // Remember the Files so "Upload anyway" still works after the panel closed.
    setPendingFiles(prev => {
      const next = { ...prev };
      files.forEach(f => { next[f.name] = f; });
      return next;
    });
    try {
      const results = await postBatch(files, false);
      if (results.some(r => r.status === 'uploaded')) onUploaded?.();
      reportBatch(results);
    } finally {
      setBgCount(n => Math.max(0, n - files.length));
    }
  };

  // Foreground upload — only used from inside the open panel ("Upload anyway"),
  // where there's already a visible surface to report into.
  const doUpload = async (files, force) => {
    if (!files.length) return;
    setUploading(true);
    setUploadProgress(0);
    const results = await postBatch(files, force, { onProgress: setUploadProgress });
    setResults(prev => {
      const byName = new Map(prev.map(r => [r.filename, r]));
      results.forEach(r => byName.set(r.filename, r));
      return Array.from(byName.values());
    });
    setPendingFiles(prev => {
      const next = { ...prev };
      files.forEach(f => { next[f.name] = f; });
      return next;
    });
    if (results.some(r => r.status === 'uploaded')) onUploaded?.();
    setUploading(false);
    setUploadProgress(0);
  };

  // Exposed to the page so dropping files anywhere on Home (not just inside
  // this button's own panel) starts an upload right away — same upload path
  // either way, just a bigger drop target.
  //
  // V79: this no longer opens the panel. Dropping photos should get you a
  // library that's filling up, not a box in front of the library.
  const acceptFiles = (fileList) => {
    const all = Array.from(fileList || []);
    const imageFiles = all.filter(f => f.type.startsWith('image/'));
    if (!imageFiles.length) {
      if (all.length) showToast('Those files aren’t images — nothing was uploaded.', 'error');
      return;
    }
    const skipped = all.length - imageFiles.length;
    showToast(
      `Uploading ${imageFiles.length} photo${imageFiles.length === 1 ? '' : 's'} in the background…`
        + (skipped ? ` (${skipped} non-image file${skipped === 1 ? '' : 's'} skipped)` : ''),
      'info', 3500);
    setPanelOpen(false);
    uploadInBackground(imageFiles);
  };

  useImperativeHandle(ref, () => ({ acceptFiles }));

  const handleClick = () => {
    if (signedIn === null) return; // auth check still in flight — ignore the click
    if (signedIn === false) {
      window.location.href = '/api/auth/google/login';
      return;
    }
    setResults([]);
    setUploadProgress(0);
    setPanelOpen(true);
  };

  // Browsing from inside the panel goes through the same background path as a
  // drop — the panel closes and you get on with your day.
  const handleFilesSelected = (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = ''; // allow re-selecting the same file later
    acceptFiles(files);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    acceptFiles(e.dataTransfer.files);
  };

  const uploadAnyway = (filename) => {
    const file = pendingFiles[filename];
    if (!file) return;
    // Drop it from the parked set first, so dismissing the panel afterwards
    // can't resurrect a photo that's already been re-sent.
    parkedDupesRef.current = parkedDupesRef.current.filter(r => r.filename !== filename);
    doUpload([file], true);
  };

  const dismissResult = (filename) => {
    parkedDupesRef.current = parkedDupesRef.current.filter(r => r.filename !== filename);
    setResults(prev => prev.filter(r => r.filename !== filename));
  };

  // V79: closing is ALWAYS allowed. It used to bail out while `uploading` was
  // true, which — combined with the full-screen overlay — is what locked the
  // whole app until the server finished. Any batch still in flight keeps going
  // and reports through the pill + toast.
  const closePanel = () => {
    setPanelOpen(false);
    setResults([]);
    setUploadProgress(0);
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
        title={signedIn === false ? 'Sign in with Google to upload' : 'Upload photos'}
        style={{
          height: isMobile ? '38px' : '46px', width: isMobile ? '38px' : '46px', flexShrink: 0,
          background: surfaceContainerDark,
          border: `1px solid ${withAlpha(white,0.12)}`,
          borderRadius: '10px',
          cursor: 'pointer',
          color: onSurfaceMuted,
          fontSize: '16px'
        }}
      >
        ⬆
      </button>

      {/* V79: background-upload pill. The only on-screen sign a batch is still
          running once the panel has closed — deliberately small, bottom-left so
          it never collides with the toast stack (bottom-right), and
          pointer-events:none so it can't block anything underneath. */}
      {bgCount > 0 && (
        <div
          aria-live="polite"
          style={{
            position: 'fixed', bottom: '20px', left: '20px', zIndex: 1500,
            display: 'flex', alignItems: 'center', gap: '9px',
            padding: '9px 14px', borderRadius: '999px',
            background: surfaceContainerLowest,
            border: `1px solid ${withAlpha(primaryDim, 0.35)}`,
            boxShadow: `0 8px 24px ${withAlpha(black, 0.5)}`,
            color: onSurfaceWarm, fontSize: '12.5px', fontWeight: 500,
            fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
            pointerEvents: 'none',
          }}
        >
          <span style={{
            display: 'inline-block', width: '12px', height: '12px',
            border: `2px solid ${withAlpha(primaryDim, 0.25)}`, borderTopColor: primaryDim,
            borderRadius: '50%', animation: 'spin 0.7s linear infinite'
          }} />
          Uploading {bgCount} photo{bgCount === 1 ? '' : 's'}…
        </div>
      )}

      {panelOpen && (
        <>
          <div
            onClick={closePanel}
            style={{ position: 'fixed', inset: 0, background: withAlpha(black,0.6), zIndex: 999 }}
          />
          <div style={{
            position: 'fixed', top: '8vh', left: '50%', transform: 'translateX(-50%)',
            width: 'min(640px, 92vw)', maxHeight: '84vh', overflowY: 'auto',
            background: surfaceContainerLowest, border: `1px solid ${withAlpha(white,0.1)}`,
            borderRadius: '14px', zIndex: 1000, padding: '20px',
            color: onSurfaceWarm, fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
            boxShadow: `0 30px 80px ${withAlpha(black,0.7)}`
          }}>
            <div style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              marginBottom: '16px'
            }}>
              <div style={{ fontSize: '15px', fontWeight: 600 }}>Upload photos</div>
              <button
                onClick={closePanel}
                style={{ background: 'none', border: 'none', color: onSurfaceFaint, cursor: 'pointer', fontSize: '20px' }}
              >×</button>
            </div>

            {/* Dropzone — drag files in, or click to browse. Stays available the
                whole time the panel's open, so you can drop more after seeing results. */}
            <div
              onDragOver={e => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => !uploading && fileInputRef.current?.click()}
              style={{
                border: `2px dashed ${dragOver ? primaryDim : withAlpha(white,0.18)}`,
                borderRadius: '10px',
                padding: '32px 20px',
                textAlign: 'center',
                cursor: uploading ? 'default' : 'pointer',
                background: dragOver ? withAlpha(primaryDim,0.08) : 'transparent',
                transition: 'background 120ms ease, border-color 120ms ease',
                opacity: uploading ? 0.6 : 1
              }}
            >
              {uploading ? (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px' }}>
                  <span style={{
                    display: 'inline-block', width: '16px', height: '16px',
                    border: `2px solid ${withAlpha(primaryDim,0.25)}`, borderTopColor: primaryDim,
                    borderRadius: '50%', animation: 'spin 0.7s linear infinite'
                  }} />
                  <div style={{ fontSize: '13px', color: onSurfaceWarm }}>
                    Uploading… {uploadProgress}%
                  </div>
                  <div style={{
                    width: '200px', height: '4px', background: withAlpha(primaryDim,0.1),
                    borderRadius: '2px', overflow: 'hidden'
                  }}>
                    <div style={{
                      width: `${uploadProgress}%`, height: '100%', background: primaryDim,
                      transition: 'width 100ms linear'
                    }} />
                  </div>
                </div>
              ) : (
                <>
                  <div style={{ fontSize: '24px', marginBottom: '8px', color: onSurfaceMuted }}>⬆</div>
                  <div style={{ fontSize: '13.5px', color: onSurfaceWarm, marginBottom: '4px' }}>
                    Drag photos here
                  </div>
                  <div style={{ fontSize: '12px', color: onSurfaceFaint, marginBottom: '6px' }}>
                    or <span style={{ color: warning, textDecoration: 'underline' }}>browse files</span>
                  </div>
                  <div style={{ fontSize: '11px', color: onSurfaceFaint }}>
                    Uploads run in the background — you can keep working.
                  </div>
                </>
              )}
            </div>

            {results.length > 0 && (
              <div style={{ marginTop: '16px' }}>
                {results.map(r => (
                  <div key={r.filename} style={{
                    display: 'flex', alignItems: 'flex-start', gap: '10px',
                    padding: '10px 0', borderBottom: `1px solid ${withAlpha(white,0.06)}`
                  }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: '12.5px', color: onSurfaceWarm,
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
                      }}>{r.filename}</div>

                      {r.status === 'uploaded' && (
                        <div style={{ fontSize: '11px', color: success, marginTop: '3px' }}>
                          ✓ Uploaded — tagging will start shortly
                        </div>
                      )}
                      {r.status === 'error' && (
                        <div style={{ fontSize: '11px', color: danger, marginTop: '3px' }}>
                          {r.message || 'Upload failed'}
                        </div>
                      )}
                      {r.status === 'duplicate' && (
                        <div style={{ marginTop: '6px' }}>
                          <div style={{ fontSize: '11px', color: warning, marginBottom: '6px' }}>
                            Looks like a duplicate of an image already in your library:
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            {/* thumbnail can be null if the stored blob is
                                missing — render a placeholder rather than a
                                broken image. */}
                            {r.existing.thumbnail ? (
                              <img
                                src={r.existing.thumbnail}
                                alt={r.existing.filename}
                                style={{ width: '48px', height: '48px', objectFit: 'cover', borderRadius: '5px' }}
                              />
                            ) : (
                              <div style={{
                                width: '48px', height: '48px', borderRadius: '5px',
                                background: withAlpha(white, 0.06), flexShrink: 0
                              }} />
                            )}
                            <div style={{ fontSize: '10.5px', color: onSurfaceMuted, flex: 1 }}>{r.existing.filename}</div>
                            <button
                              onClick={() => uploadAnyway(r.filename)}
                              disabled={uploading}
                              style={{
                                background: 'none', border: `1px solid ${withAlpha(primaryDim,0.35)}`,
                                color: warning, borderRadius: '5px', padding: '5px 10px',
                                fontSize: '10.5px', cursor: 'pointer', fontFamily: 'inherit'
                              }}
                            >
                              Upload anyway
                            </button>
                            <button
                              onClick={() => dismissResult(r.filename)}
                              style={{
                                background: 'none', border: `1px solid ${withAlpha(white,0.15)}`,
                                color: onSurfaceMuted, borderRadius: '5px', padding: '5px 10px',
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
            )}
          </div>
        </>
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </>
  );
});

export default UploadButton;
