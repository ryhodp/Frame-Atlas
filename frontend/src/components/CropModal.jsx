import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { detectCrop, tightenBox, isFullImageBox } from '../cropDetect';
import { useIsMobile } from '../hooks/useIsMobile';

// ── CropModal — Frame Atlas V18 ───────────────────────────────────────────────
// The CropStudio v34 review workflow, embedded as a full-screen modal:
// auto-detect letterbox/screenshot chrome on each selected image, review one
// image at a time (drag handles, Tighten, Redetect, unified undo/redo,
// filmstrip, keyboard shortcuts), then apply the approved crops for real —
// one at a time against POST /api/images/<id>/crop, which replaces the Drive
// file and carries every tag over.
//
// Full-resolution originals are loaded lazily (current image + the next two)
// so a big batch starts reviewing in seconds instead of downloading
// everything up front. Detection runs in the browser on the native-res
// pixels — same engine, same behavior as standalone CropStudio.
//
// The item list lives in a ref and is mutated CropStudio-style (that's the
// code this is ported from); `force()` re-renders after every mutation.

const PREFETCH_AHEAD = 2;
const HANDLE_CORNERS = ['tl', 'tr', 'bl', 'br', 'tc', 'bc', 'lc', 'rc'];

const CONFIDENCE_STYLES = {
  high:   { color: '#b8cea1', border: 'rgba(184,206,161,0.45)', bg: 'rgba(184,206,161,0.12)' },
  medium: { color: '#dcbd76', border: 'rgba(201,162,83,0.45)',  bg: 'rgba(201,162,83,0.12)' },
  low:    { color: '#cf7152', border: 'rgba(207,113,82,0.45)',  bg: 'rgba(207,113,82,0.12)' },
};

function snapshotItem(item) {
  return {
    cropBox: item.cropBox ? { ...item.cropBox } : null,
    confidence: item.confidence,
    isFallback: item.isFallback,
    decided: item.decided,
    redetectLevel: item.redetectLevel,
    tightenLevel: item.tightenLevel,
  };
}

function applySnapshot(item, snap) {
  item.cropBox = snap.cropBox ? { ...snap.cropBox } : null;
  item.confidence = snap.confidence;
  item.isFallback = snap.isFallback;
  item.decided = snap.decided;
  item.redetectLevel = snap.redetectLevel;
  item.tightenLevel = snap.tightenLevel;
}

export default function CropModal({ images, onClose, onImageCropped }) {
  const isMobile = useIsMobile();
  const [, force] = useReducer(x => x + 1, 0);

  // 'review' | 'detectAll' | 'summary' | 'applying' | 'done'
  const [phase, setPhase] = useState('review');
  const [current, setCurrent] = useState(0);
  const [confirmClose, setConfirmClose] = useState(false);

  const itemsRef = useRef(null);
  if (itemsRef.current === null) {
    itemsRef.current = images.map(fa => ({
      fa,
      status: 'pending',   // 'pending' | 'loading' | 'ready' | 'error'
      error: null,
      url: null,
      imgEl: null,
      cropBox: null,
      confidence: 0,
      isFallback: false,
      decided: null,       // null | 'approved' | 'skipped'
      redetectLevel: 0,
      tightenLevel: 0,
      redetectNote: null,  // null | 'exhausted'
      tightenNote: null,   // null | 'tight'
    }));
  }
  const items = itemsRef.current;

  // Unified undo/redo — every action (drag, tighten, redetect, approve, skip)
  // is one entry, in order, regardless of which image it touched. Same model
  // as CropStudio v34.
  const historyRef = useRef([]);
  const redoRef = useRef([]);

  // Apply-phase progress + results
  const [applyProgress, setApplyProgress] = useState(null); // {done, total, filename}
  const [detectAllProgress, setDetectAllProgress] = useState(null);
  const resultsRef = useRef([]); // {fa, ok, error}

  const dragRef = useRef(null);
  const origImgRef = useRef(null);
  const previewCanvasRef = useRef(null);

  // ── Lazy loading: current + next PREFETCH_AHEAD ────────────────────────────
  // Both the prefetch effect below and "Approve all remaining" can ask to
  // load the same item — the prefetch effect fires it eagerly and doesn't
  // wait, while approve-all needs to know the item is actually ready before
  // deciding it. Stashing the in-flight promise on the item lets a second
  // caller await the SAME load instead of re-checking a status flag that
  // might still read "loading" the instant it's asked.
  const loadItem = useCallback((idx) => {
    const item = itemsRef.current[idx];
    if (!item) return Promise.resolve();
    if (item.loadPromise) return item.loadPromise;
    if (item.status !== 'pending') return Promise.resolve();
    item.loadPromise = (async () => {
    item.status = 'loading';
    item.error = null;
    force();
    try {
      const res = await fetch(`/api/images/${item.fa.id}/full`);
      if (!res.ok) {
        let msg = `Could not load the original (HTTP ${res.status}).`;
        try { msg = (await res.json()).error || msg; } catch { /* non-JSON body */ }
        throw new Error(msg);
      }
      const blob = await res.blob();
      item.url = URL.createObjectURL(blob);
      const img = new Image();
      // onload/onerror, not img.decode() — decode() is known to hang under
      // some browser conditions (backgrounded tabs, some automation
      // contexts) with no rejection ever firing. CropStudio's own loadImage()
      // already used this exact pattern; no reason to have deviated from it.
      await new Promise((resolve, reject) => {
        img.onload = resolve;
        img.onerror = () => reject(new Error('Could not decode this image.'));
        img.src = item.url;
      });
      item.imgEl = img;
      let result = null;
      try {
        result = detectCrop(img);
      } catch (e) {
        console.error('Crop detection failed, falling back to manual', e);
      }
      // Same fallback as CropStudio: no detection → full-image box so the
      // drag handles still work, never a dead "no crop" state.
      item.cropBox = result
        ? result.box
        : { x: 0, y: 0, w: img.naturalWidth, h: img.naturalHeight };
      item.confidence = result ? result.confidence : 0;
      item.isFallback = !result;
      item.status = 'ready';
    } catch (e) {
      item.status = 'error';
      item.error = e.message || 'Could not load this image.';
    }
    force();
    })();
    return item.loadPromise;
  }, [force]);

  useEffect(() => {
    if (phase !== 'review') return;
    for (let i = current; i <= Math.min(items.length - 1, current + PREFETCH_AHEAD); i++) {
      if (items[i].status === 'pending') loadItem(i);
    }
  }, [phase, current, loadItem, items]);

  // Revoke object URLs when the modal unmounts
  useEffect(() => () => {
    itemsRef.current.forEach(item => { if (item.url) URL.revokeObjectURL(item.url); });
  }, []);

  // ── History helpers ────────────────────────────────────────────────────────
  const pushEdit = (index, before, after) => {
    historyRef.current.push({ type: 'edit', index, before, after });
    redoRef.current.length = 0;
    force();
  };

  const undo = () => {
    if (phase !== 'review' || !historyRef.current.length) return;
    const entry = historyRef.current.pop();
    const item = items[entry.index];
    if (entry.type === 'edit') {
      applySnapshot(item, entry.before);
    } else {
      item.decided = entry.prevDecided;
    }
    redoRef.current.push(entry);
    setCurrent(entry.index);
    force();
  };

  const redo = () => {
    if (phase !== 'review' || !redoRef.current.length) return;
    const entry = redoRef.current.pop();
    const item = items[entry.index];
    if (entry.type === 'edit') {
      applySnapshot(item, entry.after);
      setCurrent(entry.index);
    } else {
      item.decided = entry.wasApproved ? 'approved' : 'skipped';
      const next = entry.index + 1;
      if (next >= items.length) setPhase('summary');
      else setCurrent(next);
    }
    historyRef.current.push(entry);
    force();
  };

  // ── Decisions ──────────────────────────────────────────────────────────────
  const recordAndAdvance = (wasApproved) => {
    const item = items[current];
    if (!item) return;
    if (wasApproved && item.status !== 'ready') return; // can't approve an unloaded/broken image
    historyRef.current.push({
      type: 'decision',
      index: current,
      wasApproved,
      prevDecided: item.decided,
    });
    redoRef.current.length = 0;
    item.decided = wasApproved ? 'approved' : 'skipped';
    const next = current + 1;
    if (next >= items.length) setPhase('summary');
    else setCurrent(next);
    force();
  };

  const goBack = () => {
    if (current > 0) setCurrent(current - 1);
  };

  const backToReview = () => {
    setPhase('review');
    setCurrent(items.length - 1);
  };

  // "Approve all remaining" — with lazy loading, the remaining images may not
  // be loaded yet, so this walks them one at a time (load → detect → approve)
  // with a progress readout, then lands on the summary. Already-decided
  // images keep their decision.
  const approveAll = async () => {
    setPhase('detectAll');
    const list = itemsRef.current;
    for (let i = current; i < list.length; i++) {
      const item = list[i];
      if (item.decided) continue;
      setDetectAllProgress({ done: i - current + 1, total: list.length - current, filename: item.fa.filename });
      // Always await, even if the prefetch effect already kicked this same
      // load off — loadItem hands back the SAME in-flight promise rather
      // than a fresh one, so this can't race ahead of a load that's already
      // running and mistake "still loading" for "failed."
      await loadItem(i);
      // A brief yield keeps the progress text painting between detections
      await new Promise(r => setTimeout(r, 0));
      item.decided = item.status === 'ready' ? 'approved' : 'skipped';
    }
    // Bulk action — the per-step history no longer describes reality well;
    // matching CropStudio, approve-all is not undoable.
    historyRef.current.length = 0;
    redoRef.current.length = 0;
    setDetectAllProgress(null);
    setPhase('summary');
    force();
  };

  // ── Tighten / Redetect (ported semantics) ─────────────────────────────────
  const doTighten = () => {
    const item = items[current];
    if (!item || item.status !== 'ready' || !item.cropBox) return;
    const before = snapshotItem(item);
    const level = (item.tightenLevel || 0) + 1;
    const { box, changed } = tightenBox(item.imgEl, item.cropBox, level);
    item.cropBox = box;
    item.tightenLevel = level;
    item.isFallback = false;
    item.tightenNote = changed ? null : 'tight';
    pushEdit(current, before, snapshotItem(item));
  };

  const doRedetect = () => {
    const item = items[current];
    if (!item || item.status !== 'ready') return;
    const before = snapshotItem(item);
    const level = (item.redetectLevel || 0) + 1;
    let result = null;
    try { result = detectCrop(item.imgEl, level); } catch (e) { console.error('Redetect failed', e); }
    const prevBox = item.cropBox;
    let regressed = false;
    if (result) {
      item.cropBox = result.box;
      item.confidence = result.confidence;
      item.isFallback = false;
    } else if (prevBox) {
      // Stricter thresholds found nothing — keep the existing box rather
      // than discarding a real (if imperfect) crop.
      regressed = true;
    } else {
      item.cropBox = { x: 0, y: 0, w: item.imgEl.naturalWidth, h: item.imgEl.naturalHeight };
      item.isFallback = true;
    }
    item.redetectLevel = level;
    item.tightenLevel = 0;
    item.tightenNote = null;
    item.redetectNote = (regressed || level >= 6) ? 'exhausted' : null;
    pushEdit(current, before, snapshotItem(item));
  };

  // ── Handle dragging (pointer events → works for both mouse and touch) ─────
  const onHandlePointerDown = (e, corner) => {
    const item = items[current];
    if (!item || item.status !== 'ready' || !item.cropBox) return;
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    const rect = origImgRef.current.getBoundingClientRect();
    dragRef.current = {
      corner,
      startX: e.clientX,
      startY: e.clientY,
      startBox: { ...item.cropBox },
      before: snapshotItem(item),
      index: current,
      sx: item.imgEl.naturalWidth / rect.width,
      sy: item.imgEl.naturalHeight / rect.height,
    };
  };

  const onHandlePointerMove = (e) => {
    const d = dragRef.current;
    if (!d) return;
    const item = items[d.index];
    const iw = item.imgEl.naturalWidth, ih = item.imgEl.naturalHeight;
    const dx = (e.clientX - d.startX) * d.sx;
    const dy = (e.clientY - d.startY) * d.sy;
    const b = { ...d.startBox };

    if      (d.corner === 'tl') { b.x = Math.max(0, b.x + dx); b.y = Math.max(0, b.y + dy); b.w = d.startBox.w - dx; b.h = d.startBox.h - dy; }
    else if (d.corner === 'tr') { b.y = Math.max(0, b.y + dy); b.w = d.startBox.w + dx; b.h = d.startBox.h - dy; }
    else if (d.corner === 'bl') { b.x = Math.max(0, b.x + dx); b.w = d.startBox.w - dx; b.h = d.startBox.h + dy; }
    else if (d.corner === 'br') { b.w = d.startBox.w + dx; b.h = d.startBox.h + dy; }
    else if (d.corner === 'tc') { b.y = Math.max(0, b.y + dy); b.h = d.startBox.h - dy; }
    else if (d.corner === 'bc') { b.h = d.startBox.h + dy; }
    else if (d.corner === 'lc') { b.x = Math.max(0, b.x + dx); b.w = d.startBox.w - dx; }
    else if (d.corner === 'rc') { b.w = d.startBox.w + dx; }

    b.w = Math.max(20, Math.min(iw - b.x, b.w));
    b.h = Math.max(20, Math.min(ih - b.y, b.h));
    b.x = Math.min(iw - b.w, Math.max(0, b.x));
    b.y = Math.min(ih - b.h, Math.max(0, b.y));

    item.cropBox = b;
    item.isFallback = false;
    item.tightenLevel = 0;
    item.redetectLevel = 0;
    item.tightenNote = null;
    item.redetectNote = null;
    force();
  };

  const onHandlePointerUp = () => {
    const d = dragRef.current;
    if (!d) return;
    dragRef.current = null;
    const item = items[d.index];
    const b0 = d.before.cropBox, b1 = item.cropBox;
    const changed = !b0 || !b1 || b0.x !== b1.x || b0.y !== b1.y || b0.w !== b1.w || b0.h !== b1.h;
    if (changed) pushEdit(d.index, d.before, snapshotItem(item));
  };

  // ── Cropped-result preview canvas ─────────────────────────────────────────
  useEffect(() => {
    if (phase !== 'review') return;
    const item = items[current];
    const canvas = previewCanvasRef.current;
    if (!canvas || !item || item.status !== 'ready' || !item.cropBox) return;
    const b = item.cropBox;
    canvas.width = b.w;
    canvas.height = b.h;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(item.imgEl, b.x, b.y, b.w, b.h, 0, 0, b.w, b.h);
  });

  // ── Keyboard shortcuts (desktop) ──────────────────────────────────────────
  useEffect(() => {
    if (phase !== 'review') return;
    const onKey = (e) => {
      if (dragRef.current) return;
      if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); recordAndAdvance(true); }
      else if (e.key === 'Backspace' || e.key === 'Delete') { e.preventDefault(); recordAndAdvance(false); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); goBack(); }
      else if ((e.key === 'z' || e.key === 'Z') && e.shiftKey) { e.preventDefault(); redo(); }
      else if (e.key === 'z' || e.key === 'Z') { e.preventDefault(); undo(); }
      else if (e.key === 'y' || e.key === 'Y') { e.preventDefault(); redo(); }
      else if (e.key === 't' || e.key === 'T') { e.preventDefault(); doTighten(); }
      else if (e.key === 'r' || e.key === 'R') { e.preventDefault(); doRedetect(); }
      else if (e.key === 'Escape') { e.preventDefault(); tryClose(); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  });

  // ── Apply: sequential crop calls against the live endpoint ────────────────
  const cropTargets = () =>
    items.filter(i => i.decided === 'approved' && i.status === 'ready' && !isFullImageBox(i.cropBox, i.imgEl));

  const applyCrops = async () => {
    const targets = cropTargets();
    if (!targets.length) return;
    setPhase('applying');
    resultsRef.current = [];
    let done = 0;
    for (const item of targets) {
      done++;
      setApplyProgress({ done, total: targets.length, filename: item.fa.filename });
      const iw = item.imgEl.naturalWidth, ih = item.imgEl.naturalHeight;
      const b = item.cropBox;
      try {
        const res = await fetch(`/api/images/${item.fa.id}/crop`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            box: {
              x: (b.x / iw) * 100,
              y: (b.y / ih) * 100,
              w: (b.w / iw) * 100,
              h: (b.h / ih) * 100,
            },
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || `Crop failed (HTTP ${res.status}).`);
        resultsRef.current.push({ fa: item.fa, ok: true });
        onImageCropped?.(item.fa.id, {
          thumbnail: data.thumbnail,
          aspect_ratio: data.aspect_ratio,
          ar_float: data.width && data.height ? data.width / data.height : undefined,
          // ar_label is a bucketed display label computed server-side at search
          // time (e.g. "16:9") — stale after a crop changes the real ratio, and
          // there's no client-side bucketing logic to recompute it here. Clear
          // it so displays fall back to the now-current raw aspect_ratio.
          ar_label: null,
        });
      } catch (e) {
        resultsRef.current.push({ fa: item.fa, ok: false, error: e.message || 'Network error — try again.' });
      }
    }
    setApplyProgress(null);
    setPhase('done');
  };

  // ── Close guard ────────────────────────────────────────────────────────────
  const tryClose = () => {
    if (phase === 'applying') return; // don't abandon mid-apply
    if (phase === 'done') { onClose(); return; }
    const touched = historyRef.current.length > 0 || items.some(i => i.decided);
    if (!touched) { onClose(); return; }
    setConfirmClose(true);
  };

  // ── Derived state ──────────────────────────────────────────────────────────
  const item = items[current];
  const approvedCount = items.filter(i => i.decided === 'approved').length;
  const skippedCount = items.filter(i => i.decided === 'skipped').length;
  const summaryToCrop = phase === 'summary' ? cropTargets().length : 0;
  const summaryUnchanged = phase === 'summary'
    ? items.filter(i => i.decided === 'approved' && (i.status !== 'ready' || isFullImageBox(i.cropBox, i.imgEl))).length
    : 0;
  const okResults = resultsRef.current.filter(r => r.ok);
  const failedResults = resultsRef.current.filter(r => !r.ok);

  const boxPct = (b, img) => ({
    left: `${(b.x / img.naturalWidth) * 100}%`,
    top: `${(b.y / img.naturalHeight) * 100}%`,
    width: `${(b.w / img.naturalWidth) * 100}%`,
    height: `${(b.h / img.naturalHeight) * 100}%`,
  });

  const handlePos = (corner, b, img) => {
    const W = img.naturalWidth, H = img.naturalHeight;
    const cx = { tl: b.x, bl: b.x, lc: b.x, tr: b.x + b.w, br: b.x + b.w, rc: b.x + b.w, tc: b.x + b.w / 2, bc: b.x + b.w / 2 }[corner];
    const cy = { tl: b.y, tr: b.y, tc: b.y, bl: b.y + b.h, br: b.y + b.h, bc: b.y + b.h, lc: b.y + b.h / 2, rc: b.y + b.h / 2 }[corner];
    return { left: `${(cx / W) * 100}%`, top: `${(cy / H) * 100}%` };
  };

  const handleCursor = {
    tl: 'nwse-resize', br: 'nwse-resize', tr: 'nesw-resize', bl: 'nesw-resize',
    tc: 'ns-resize', bc: 'ns-resize', lc: 'ew-resize', rc: 'ew-resize',
  };

  const handleSize = isMobile ? 22 : 12;

  const confStyle = item && CONFIDENCE_STYLES[item.confidence];

  const tightenLabel = item?.tightenNote === 'tight'
    ? '✓ Already tight'
    : item?.tightenLevel > 0 ? `⇲ Tighten (${item.tightenLevel})` : '⇲ Tighten';
  const redetectLabel = item?.redetectNote === 'exhausted'
    ? '↻ No further change'
    : item?.redetectLevel > 0 ? `↻ Redetect (${item.redetectLevel})` : '↻ Redetect';

  // ──────────────────────────────────────────────────────────────────────────
  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 1200,
      background: '#4a4a52', color: '#efeadd',
      display: 'flex', flexDirection: 'column',
      fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
      animation: 'fadeIn 0.15s ease',
    }}>

      {/* ── Header ── */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '10px',
        padding: isMobile ? '10px 12px' : '12px 20px',
        borderBottom: '1px solid rgba(255,255,255,0.065)',
        flexWrap: 'wrap', rowGap: '8px', flexShrink: 0,
      }}>
        <span style={{ fontSize: '15px', fontWeight: 700, letterSpacing: '-0.01em' }}>✂ Crop</span>
        {phase === 'review' && (
          <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '12px', color: '#9c988d' }}>
            <span style={{ color: '#efeadd' }}>{Math.min(current + 1, items.length)}</span> of{' '}
            <span style={{ color: '#efeadd' }}>{items.length}</span>
          </span>
        )}
        {phase === 'review' && !isMobile && (
          <span style={{
            fontSize: '12px', color: '#65625a', flex: 1, textAlign: 'center',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0,
          }}>
            {item?.fa.filename}
          </span>
        )}
        {(phase !== 'review' || isMobile) && <div style={{ flex: 1 }} />}

        {phase === 'review' && (
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', rowGap: '6px', alignItems: 'center' }}>
            <button onClick={approveAll} style={ghostBtn()} title="Load, detect, and approve every remaining image">
              Approve all remaining
            </button>
            <button onClick={goBack} disabled={current <= 0} style={ghostBtn()} title="View previous image (←)">
              ← Back
            </button>
            <button
              onClick={doRedetect}
              disabled={item?.status !== 'ready' || item?.redetectNote === 'exhausted'}
              style={ghostBtn()}
              title="Re-run detection with stricter evidence (R)"
            >
              {redetectLabel}
            </button>
            <button
              onClick={doTighten}
              disabled={item?.status !== 'ready' || item?.tightenNote === 'tight'}
              style={ghostBtn()}
              title="Trim any remaining flat border (T)"
            >
              {tightenLabel}
            </button>
            <button onClick={undo} disabled={!historyRef.current.length} style={ghostBtn()} title="Undo last action (Z)">
              ↩
            </button>
            <button onClick={redo} disabled={!redoRef.current.length} style={ghostBtn()} title="Redo (Shift+Z)">
              ↪
            </button>
            <button
              onClick={() => recordAndAdvance(false)}
              style={ghostBtn('#cf7152', 'rgba(207,113,82,0.35)')}
              title="Skip — leave this image untouched (⌫)"
            >
              Skip
            </button>
            <button
              onClick={() => recordAndAdvance(true)}
              disabled={item?.status !== 'ready'}
              style={{
                background: '#d9a441', color: '#3d2f00', border: 'none',
                borderRadius: '8px', padding: '7px 16px', fontSize: '12.5px',
                fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
                opacity: item?.status === 'ready' ? 1 : 0.4,
              }}
              title="Approve this crop (Space)"
            >
              Approve
            </button>
          </div>
        )}

        <button
          onClick={tryClose}
          title="Close"
          style={{
            background: 'none', border: 'none', color: '#9c988d',
            fontSize: '20px', cursor: 'pointer', padding: '2px 6px', lineHeight: 1,
          }}
        >×</button>
      </div>

      {/* ── Review: main panels ── */}
      {phase === 'review' && (
        <>
          <div style={{
            flex: 1, display: 'flex', minHeight: 0,
            flexDirection: isMobile ? 'column' : 'row',
            gap: '1px', background: 'rgba(255,255,255,0.065)',
          }}>
            {/* Original + handles */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#0a0a0b', minWidth: 0, minHeight: 0 }}>
              <div style={panelLabel()}>
                ORIGINAL — DRAG HANDLES TO ADJUST
              </div>
              <div style={{
                flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
                padding: isMobile ? '10px' : '20px', overflow: 'hidden', minHeight: 0, position: 'relative',
              }}>
                {item?.status === 'ready' && (
                  <div style={{ position: 'relative', display: 'inline-block', maxWidth: '100%', maxHeight: '100%', overflow: 'hidden' }}>
                    <img
                      ref={origImgRef}
                      src={item.url}
                      alt={item.fa.filename}
                      draggable={false}
                      style={{
                        display: 'block', maxWidth: '100%',
                        maxHeight: isMobile ? 'calc(50vh - 120px)' : 'calc(100vh - 300px)',
                        objectFit: 'contain', userSelect: 'none',
                      }}
                    />
                    {item.cropBox && (
                      <div style={{ position: 'absolute', inset: 0 }}>
                        <div style={{
                          position: 'absolute',
                          ...boxPct(item.cropBox, item.imgEl),
                          border: '2px solid #d9a441',
                          boxShadow: '0 0 0 9999px rgba(0,0,0,0.55)',
                          pointerEvents: 'none',
                        }} />
                        {HANDLE_CORNERS.map(corner => (
                          <div
                            key={corner}
                            onPointerDown={e => onHandlePointerDown(e, corner)}
                            onPointerMove={onHandlePointerMove}
                            onPointerUp={onHandlePointerUp}
                            onPointerCancel={onHandlePointerUp}
                            style={{
                              position: 'absolute',
                              ...handlePos(corner, item.cropBox, item.imgEl),
                              width: `${handleSize}px`, height: `${handleSize}px`,
                              background: '#d9a441',
                              borderRadius: ['tc', 'bc', 'lc', 'rc'].includes(corner) ? '3px' : '50%',
                              transform: 'translate(-50%, -50%)',
                              cursor: handleCursor[corner],
                              touchAction: 'none',
                              zIndex: 10,
                              boxShadow: '0 1px 4px rgba(0,0,0,0.6)',
                            }}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                )}
                {item?.status === 'loading' && <PanelNotice spinner text="Loading full-resolution original…" />}
                {item?.status === 'pending' && <PanelNotice spinner text="Queued…" />}
                {item?.status === 'error' && (
                  <PanelNotice text={item.error}>
                    <div style={{ display: 'flex', gap: '8px', justifyContent: 'center' }}>
                      <button
                        onClick={() => { item.status = 'pending'; item.loadPromise = null; loadItem(current); }}
                        style={ghostBtn('#dcbd76', 'rgba(201,162,83,0.35)')}
                      >
                        Retry
                      </button>
                      <button onClick={() => recordAndAdvance(false)} style={ghostBtn()}>
                        Skip this image
                      </button>
                    </div>
                  </PanelNotice>
                )}
              </div>
            </div>

            {/* Cropped result */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#0a0a0b', minWidth: 0, minHeight: 0 }}>
              <div style={{ ...panelLabel(), color: '#d9a441', display: 'flex', alignItems: 'center', gap: '8px' }}>
                CROPPED RESULT
                {item?.status === 'ready' && !item.isFallback && confStyle && (
                  <span style={{
                    fontSize: '9px', fontFamily: "'JetBrains Mono', monospace",
                    borderRadius: '3px', padding: '1px 6px', letterSpacing: '0.06em',
                    fontWeight: 600, textTransform: 'uppercase',
                    color: confStyle.color, background: confStyle.bg, border: `1px solid ${confStyle.border}`,
                  }}>
                    {item.confidence}
                  </span>
                )}
                {item?.status === 'ready' && item.isFallback && (
                  <span style={{
                    fontSize: '9px', fontFamily: "'JetBrains Mono', monospace",
                    borderRadius: '3px', padding: '1px 6px',
                    color: '#cf7152', background: 'rgba(207,113,82,0.12)', border: '1px solid rgba(207,113,82,0.45)',
                  }}>
                    ⚠ NO AUTO-CROP — ADJUST BY HAND
                  </span>
                )}
              </div>
              <div style={{
                flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
                padding: isMobile ? '10px' : '20px', overflow: 'hidden', minHeight: 0,
              }}>
                <canvas
                  ref={previewCanvasRef}
                  style={{
                    maxWidth: '100%',
                    maxHeight: isMobile ? 'calc(50vh - 120px)' : 'calc(100vh - 300px)',
                    objectFit: 'contain',
                    display: item?.status === 'ready' ? 'block' : 'none',
                  }}
                />
              </div>
            </div>
          </div>

          {/* Footer: hints + tally */}
          <div style={{
            padding: isMobile ? '8px 12px' : '8px 20px',
            borderTop: '1px solid rgba(255,255,255,0.065)',
            display: 'flex', alignItems: 'center', gap: '16px', flexShrink: 0,
          }}>
            {!isMobile && (
              <span style={{ fontSize: '11px', color: '#65625a' }}>
                <Kbd>Space</Kbd> approve · <Kbd>⌫</Kbd> skip · <Kbd>←</Kbd> back · <Kbd>Z</Kbd> undo · <Kbd>⇧Z</Kbd> redo · <Kbd>T</Kbd> tighten · <Kbd>R</Kbd> redetect
              </span>
            )}
            <div style={{ flex: 1 }} />
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '11px', color: '#65625a' }}>
              <span style={{ color: '#b8cea1' }}>✓ {approvedCount}</span>
              {'  '}
              <span style={{ color: '#cf7152' }}>– {skippedCount}</span>
            </span>
          </div>

          {/* Filmstrip */}
          {items.length > 1 && (
            <div style={{
              height: '76px', flexShrink: 0,
              borderTop: '1px solid rgba(255,255,255,0.065)',
              background: '#111113',
              overflowX: 'auto', overflowY: 'hidden',
              display: 'flex', alignItems: 'center', gap: '6px',
              padding: '8px 12px', scrollBehavior: 'smooth',
            }}>
              {items.map((it, idx) => (
                <div
                  key={it.fa.id}
                  onClick={() => setCurrent(idx)}
                  title={it.fa.filename}
                  style={{
                    flexShrink: 0, width: '52px', height: '60px',
                    borderRadius: '4px', overflow: 'hidden', position: 'relative',
                    cursor: 'pointer',
                    border: `2px solid ${idx === current ? '#d9a441' : 'transparent'}`,
                  }}
                >
                  {it.fa.thumbnail && (
                    <img src={it.fa.thumbnail} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
                  )}
                  <span style={{
                    position: 'absolute', bottom: '3px', right: '3px',
                    width: '8px', height: '8px', borderRadius: '50%',
                    border: '1px solid rgba(0,0,0,0.4)',
                    background: it.decided === 'approved' ? '#b8cea1' : it.decided === 'skipped' ? '#cf7152' : '#65625a',
                  }} />
                  {it.status === 'ready' && !it.isFallback && (
                    <span style={{
                      position: 'absolute', top: '3px', left: '3px',
                      width: '7px', height: '7px', borderRadius: '50%',
                      border: '1px solid rgba(0,0,0,0.3)',
                      background: it.confidence === 'high' ? '#b8cea1' : it.confidence === 'medium' ? '#dcbd76' : '#cf7152',
                    }} />
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* ── Approve-all progress ── */}
      {phase === 'detectAll' && (
        <CenterScreen>
          <Spinner />
          <div style={{ fontSize: '13px', color: '#9c988d' }}>
            Detecting crops — {detectAllProgress ? `${detectAllProgress.done} of ${detectAllProgress.total}` : '…'}
          </div>
          {detectAllProgress && (
            <div style={{ fontSize: '11px', color: '#65625a', fontFamily: "'JetBrains Mono', monospace" }}>
              {detectAllProgress.filename}
            </div>
          )}
        </CenterScreen>
      )}

      {/* ── Summary ── */}
      {phase === 'summary' && (
        <CenterScreen>
          <div style={{ fontSize: '56px', fontWeight: 700, letterSpacing: '-2px', color: '#d9a441', lineHeight: 1 }}>
            {summaryToCrop}
          </div>
          <div style={{ fontSize: '14px', color: '#9c988d', marginTop: '-10px' }}>
            image{summaryToCrop === 1 ? '' : 's'} ready to crop
          </div>
          <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '12px', color: '#65625a' }}>
            {summaryUnchanged > 0 && <>{summaryUnchanged} approved with no crop needed · </>}
            {skippedCount} skipped
          </div>
          {summaryToCrop > 0 ? (
            <>
              <button
                onClick={applyCrops}
                style={{
                  background: '#d9a441', color: '#3d2f00', fontWeight: 700, fontSize: '14px',
                  padding: '12px 28px', borderRadius: '8px', border: 'none', cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
              >
                Crop {summaryToCrop} image{summaryToCrop === 1 ? '' : 's'}
              </button>
              <div style={{ fontSize: '11.5px', color: '#65625a', maxWidth: '360px', textAlign: 'center', lineHeight: 1.5 }}>
                Each original moves to your Drive's _Removed folder (recoverable) and a cropped
                copy takes its place. All tags and metadata carry over.
              </div>
            </>
          ) : (
            <div style={{ fontSize: '12.5px', color: '#65625a' }}>Nothing to crop — close, or go back and approve some.</div>
          )}
          <button onClick={backToReview} style={ghostBtn()}>← Back to review</button>
        </CenterScreen>
      )}

      {/* ── Applying ── */}
      {phase === 'applying' && (
        <CenterScreen>
          <Spinner />
          <div style={{ fontSize: '13px', color: '#9c988d' }}>
            Cropping {applyProgress ? `${applyProgress.done} of ${applyProgress.total}` : '…'}
          </div>
          {applyProgress && (
            <div style={{ fontSize: '11px', color: '#65625a', fontFamily: "'JetBrains Mono', monospace" }}>
              {applyProgress.filename}
            </div>
          )}
          <div style={{ fontSize: '11.5px', color: '#65625a' }}>
            Downloading, cropping, and swapping each file in Drive — a few seconds per image.
          </div>
        </CenterScreen>
      )}

      {/* ── Done ── */}
      {phase === 'done' && (
        <CenterScreen>
          <div style={{ fontSize: '56px', fontWeight: 700, letterSpacing: '-2px', color: okResults.length ? '#b8cea1' : '#cf7152', lineHeight: 1 }}>
            {okResults.length}
          </div>
          <div style={{ fontSize: '14px', color: '#9c988d', marginTop: '-10px' }}>
            image{okResults.length === 1 ? '' : 's'} cropped
          </div>
          <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '12px', color: '#65625a' }}>
            {summaryUnchangedText(items)}
            {skippedCount} skipped{failedResults.length > 0 && <> · <span style={{ color: '#cf7152' }}>{failedResults.length} failed</span></>}
          </div>
          {failedResults.length > 0 && (
            <div style={{
              maxWidth: 'min(480px, 90vw)', maxHeight: '30vh', overflowY: 'auto',
              background: 'rgba(207,113,82,0.06)', border: '1px solid rgba(207,113,82,0.25)',
              borderRadius: '10px', padding: '12px 14px', textAlign: 'left',
            }}>
              {failedResults.map(r => (
                <div key={r.fa.id} style={{ fontSize: '12px', color: '#efeadd', marginBottom: '8px', lineHeight: 1.5 }}>
                  <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '11px', color: '#cf7152' }}>{r.fa.filename}</div>
                  {r.error}
                </div>
              ))}
              <div style={{ fontSize: '11px', color: '#9c988d' }}>
                Nothing was lost — these images are untouched. Fix the issue above and crop them again.
              </div>
            </div>
          )}
          <button
            onClick={onClose}
            style={{
              background: '#d9a441', color: '#3d2f00', fontWeight: 700, fontSize: '14px',
              padding: '12px 28px', borderRadius: '8px', border: 'none', cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            Done
          </button>
        </CenterScreen>
      )}

      {confirmClose && (
        <div
          onClick={() => setConfirmClose(false)}
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
            zIndex: 1300, display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={{
              background: '#1a1c20', border: '1px solid rgba(255,255,255,0.12)',
              borderRadius: '12px', padding: '18px 20px', width: 'min(320px, 90vw)',
              boxShadow: '0 20px 48px rgba(0,0,0,0.6)', animation: 'fadeIn 0.12s ease',
            }}
          >
            <div style={{ fontSize: '13.5px', color: '#efeadd', lineHeight: 1.5, marginBottom: '16px' }}>
              Leave crop review? Your images have not been changed yet.
            </div>
            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => setConfirmClose(false)}
                style={{
                  background: 'none', border: '1px solid rgba(255,255,255,0.12)',
                  color: '#9c988d', borderRadius: '6px', padding: '7px 14px',
                  cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit',
                }}
              >
                Keep reviewing
              </button>
              <button
                onClick={onClose}
                style={{
                  background: 'rgba(255,180,171,0.18)', border: '1px solid rgba(255,180,171,0.6)',
                  color: '#ffb4ab', borderRadius: '6px', padding: '7px 14px',
                  cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit',
                }}
              >
                Leave
              </button>
            </div>
          </div>
        </div>
      )}

      <style>{`
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}

// One "approved but nothing to crop" fragment for the done screen — kept out
// of the JSX so the counting logic (which mirrors the summary screen) stays
// in one readable place.
function summaryUnchangedText(items) {
  const n = items.filter(i => i.decided === 'approved' && (i.status !== 'ready' || isFullImageBox(i.cropBox, i.imgEl))).length;
  return n > 0 ? `${n} approved with no crop needed · ` : '';
}

function CenterScreen({ children }) {
  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', gap: '18px', padding: '20px',
    }}>
      {children}
    </div>
  );
}

function PanelNotice({ spinner, text, children }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '14px', maxWidth: '340px', textAlign: 'center' }}>
      {spinner && <Spinner />}
      <div style={{ fontSize: '12.5px', color: '#9c988d', lineHeight: 1.5 }}>{text}</div>
      {children}
    </div>
  );
}

function Spinner() {
  return (
    <span style={{
      width: '18px', height: '18px',
      border: '2px solid rgba(201,162,83,0.2)', borderTopColor: '#c9a253',
      borderRadius: '50%', display: 'inline-block',
      animation: 'spin 0.7s linear infinite',
    }} />
  );
}

function Kbd({ children }) {
  return (
    <kbd style={{
      fontFamily: "'JetBrains Mono', monospace",
      background: '#18181b', border: '1px solid rgba(255,255,255,0.12)',
      borderRadius: '3px', padding: '1px 5px', fontSize: '10px', color: '#efeadd',
    }}>{children}</kbd>
  );
}

function ghostBtn(color = '#9c988d', borderColor = 'rgba(255,255,255,0.12)') {
  return {
    background: 'none', border: `1px solid ${borderColor}`,
    color, borderRadius: '8px', padding: '7px 12px',
    cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit',
  };
}

function panelLabel() {
  return {
    padding: '7px 14px', fontSize: '9.5px',
    fontFamily: "'JetBrains Mono', monospace",
    color: '#65625a', letterSpacing: '0.08em',
    borderBottom: '1px solid rgba(255,255,255,0.065)', flexShrink: 0,
  };
}
