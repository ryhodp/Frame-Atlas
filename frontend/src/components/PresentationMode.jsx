import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { buildSlides } from '../presentationOrder';

// ── Fullscreen deck presentation (Day 23 / V41) ──────────────────────────────
//
// Run a pitch off the laptop with nothing on screen but the work: arrows/space
// to advance, Esc to exit, scene names as title cards, storyboard notes under
// the frame with a toggle.
//
// Entirely client-side. Everything it needs — scenes in sort_order, images in
// storyboard order, notes, thumbnails — is already in the deck payload the page
// fetched, which is also why this works from the offline cached copy.
//
// Three rules that came from the product decision and shouldn't be "improved"
// away later:
//
// 1. FIT WHOLE, NEVER CROP. Same rule as the PDF exporter (V40). The frame is
//    letterboxed on black; a screen whose shape doesn't match the photo shows
//    black, it does not lose picture. Re-framing a cinematographer's shots to
//    fill a 16:9 laptop would make the feature worse than useless.
// 2. HOLD ON THE LAST FRAME. Advancing past the end does nothing at all — no
//    loop, no auto-exit, no end card. In a live pitch you can never accidentally
//    reveal that you've run out of material or drop the client back into the
//    app UI.
// 3. TITLE CARDS ONLY WHEN THERE'S SOMETHING TO DIVIDE (2+ non-empty sections).
//    A single-scene deck opens straight on its first frame instead of making
//    you click past a card. Empty scenes emit nothing — same no-stranded-header
//    rule as the PDF.

// ─────────────────────────────────────────────────────────────────────────────
// THE IMAGE SOURCE. This one line is the whole resolution decision.
//
// V41 ships on the ~800px thumbnails already loaded in the deck payload, so
// advancing a frame is instant with no loading pause — Ryan's explicit call.
// If that ever looks soft on a real projector, THIS FUNCTION is what changes:
// return a full-res URL here and the preloader below (which already warms the
// next and previous frames) turns from a no-op into the thing that hides the
// fetch. Nothing else in this file knows or cares where pixels come from.
// ─────────────────────────────────────────────────────────────────────────────
const slideImageSrc = (img) => img.thumbnail;

// Remembered across presentations, per browser (Ryan's call over a fixed
// default): whether the storyboard note shows under the frame.
const NOTES_PREF_KEY = 'fa.presentation.showNotes';

function loadNotesPref() {
  try {
    const saved = localStorage.getItem(NOTES_PREF_KEY);
    // First run ever: notes ON, so the toggle is discoverable rather than a
    // hidden feature you'd have to be told about.
    return saved === null ? true : saved === '1';
  } catch {
    return true;
  }
}

const IDLE_MS = 2500;      // mouse still this long → controls and cursor fade
const HINT_MS = 4200;      // opening keyboard-hint overlay lifetime

export default function PresentationMode({ deckName, scenes, images, onClose }) {
  const [index, setIndex] = useState(0);
  const [showNotes, setShowNotes] = useState(loadNotesPref);
  const [uiVisible, setUiVisible] = useState(true);
  const [hintVisible, setHintVisible] = useState(true);

  const rootRef = useRef(null);
  const idleTimer = useRef(null);
  const enteredFullscreen = useRef(false);
  const closing = useRef(false);
  const touchStartX = useRef(null);

  // ── The running order ──────────────────────────────────────────────────────
  // Lives in presentationOrder.js as a plain function so it can be tested
  // without driving a browser (see scripts/test_presentation_order.mjs).
  const slides = useMemo(() => buildSlides(scenes, images), [scenes, images]);

  const totalPhotos = useMemo(
    () => slides.filter(s => s.type === 'photo').length,
    [slides]
  );

  const slide = slides[Math.min(index, Math.max(slides.length - 1, 0))];

  // ── Closing ────────────────────────────────────────────────────────────────
  // Guarded because exiting fullscreen fires `fullscreenchange`, which would
  // otherwise call this a second time on the way out.
  const close = useCallback(() => {
    if (closing.current) return;
    closing.current = true;
    if (document.fullscreenElement) {
      const exited = document.exitFullscreen();
      if (exited && exited.catch) exited.catch(() => {});
    }
    onClose();
  }, [onClose]);

  const goNext = useCallback(() => {
    // Rule 2: hold on the last frame. No wrap, no exit.
    setIndex(i => Math.min(i + 1, slides.length - 1));
  }, [slides.length]);

  const goPrev = useCallback(() => {
    setIndex(i => Math.max(i - 1, 0));
  }, []);

  const toggleNotes = useCallback(() => {
    setShowNotes(v => {
      const next = !v;
      try { localStorage.setItem(NOTES_PREF_KEY, next ? '1' : '0'); } catch { /* private mode */ }
      return next;
    });
  }, []);

  // ── Enter real fullscreen ──────────────────────────────────────────────────
  // If the browser refuses (it can, outside a user gesture), the overlay is
  // position:fixed anyway — the presentation still runs, just with the browser
  // header still on screen, and our own Escape handler still exits.
  useEffect(() => {
    const el = rootRef.current;
    if (!el || !el.requestFullscreen) return;
    const req = el.requestFullscreen();
    if (req && req.then) {
      req.then(() => { enteredFullscreen.current = true; }).catch(() => {});
    } else {
      enteredFullscreen.current = true;
    }
  }, []);

  // Esc inside native fullscreen is swallowed by the browser — it exits
  // fullscreen without ever delivering a keydown. Watching for the exit is what
  // actually makes "Esc to leave the presentation" work.
  useEffect(() => {
    const onFsChange = () => {
      if (!document.fullscreenElement && enteredFullscreen.current) close();
    };
    document.addEventListener('fullscreenchange', onFsChange);
    return () => document.removeEventListener('fullscreenchange', onFsChange);
  }, [close]);

  // ── Keyboard ───────────────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      switch (e.key) {
        case 'ArrowRight': case 'ArrowDown': case 'PageDown': case ' ': case 'Enter':
          e.preventDefault(); goNext(); break;
        case 'ArrowLeft': case 'ArrowUp': case 'PageUp': case 'Backspace':
          e.preventDefault(); goPrev(); break;
        case 'Home':
          e.preventDefault(); setIndex(0); break;
        case 'End':
          e.preventDefault(); setIndex(slides.length - 1); break;
        case 'n': case 'N':
          e.preventDefault(); toggleNotes(); break;
        case 'Escape':
          e.preventDefault(); close(); break;
        default:
          break;
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [goNext, goPrev, toggleNotes, close, slides.length]);

  // ── Idle: hide the controls and the cursor so only the work is on screen ───
  useEffect(() => {
    const wake = () => {
      setUiVisible(true);
      if (idleTimer.current) clearTimeout(idleTimer.current);
      idleTimer.current = setTimeout(() => setUiVisible(false), IDLE_MS);
    };
    wake();
    window.addEventListener('mousemove', wake);
    return () => {
      window.removeEventListener('mousemove', wake);
      if (idleTimer.current) clearTimeout(idleTimer.current);
    };
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setHintVisible(false), HINT_MS);
    return () => clearTimeout(t);
  }, []);

  // Lock page scroll behind the overlay (same as StoryboardView).
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, []);

  // Warm the neighbours. A no-op today (thumbnails are data URIs already in
  // memory) — this exists so swapping slideImageSrc to full-res is a one-line
  // change rather than also needing a preloader designed from scratch.
  useEffect(() => {
    for (const offset of [1, 2, -1]) {
      const neighbour = slides[index + offset];
      if (neighbour && neighbour.type === 'photo') {
        const src = slideImageSrc(neighbour.img);
        if (src) { const pre = new Image(); pre.src = src; }
      }
    }
  }, [index, slides]);

  if (!slides.length) return null;

  const note = slide.type === 'photo' ? (slide.img.storyboard_note || '').trim() : '';
  const noteShowing = Boolean(note) && showNotes;
  const atStart = index === 0;
  const atEnd = index === slides.length - 1;

  const edgeBtn = {
    background: 'rgba(20,20,22,0.55)',
    border: '1px solid rgba(255,255,255,0.14)',
    color: '#e2e2e6',
    borderRadius: '50%',
    width: '44px', height: '44px',
    fontSize: '18px', lineHeight: 1,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontFamily: 'inherit'
  };

  const pill = {
    background: 'rgba(20,20,22,0.62)',
    border: '1px solid rgba(255,255,255,0.14)',
    color: '#e2e2e6',
    borderRadius: '999px',
    padding: '7px 14px',
    fontSize: '12.5px',
    fontFamily: 'inherit',
    cursor: 'pointer',
    whiteSpace: 'nowrap'
  };

  return (
    <div
      ref={rootRef}
      onClick={goNext}
      onContextMenu={(e) => { e.preventDefault(); goPrev(); }}
      onTouchStart={(e) => { touchStartX.current = e.touches[0].clientX; }}
      onTouchEnd={(e) => {
        if (touchStartX.current === null) return;
        const dx = e.changedTouches[0].clientX - touchStartX.current;
        if (Math.abs(dx) > 45) { dx < 0 ? goNext() : goPrev(); }
        touchStartX.current = null;
      }}
      style={{
        position: 'fixed', inset: 0, zIndex: 3000,
        background: '#000',
        display: 'flex', flexDirection: 'column',
        cursor: uiVisible ? 'default' : 'none',
        userSelect: 'none'
      }}
    >
      {/* ── The frame (or the scene card) ─────────────────────────────────── */}
      <div style={{
        flex: 1, minHeight: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: slide.type === 'title' ? '48px' : '0'
      }}>
        {slide.type === 'title' ? (
          <div style={{ textAlign: 'center', maxWidth: '80vw' }}>
            <div style={{
              fontSize: 'clamp(30px, 5.5vw, 68px)', fontWeight: 700,
              color: '#d9a441', lineHeight: 1.15, letterSpacing: '-0.5px',
              overflowWrap: 'break-word'
            }}>
              {slide.label}
            </div>
            <div style={{
              width: '120px', height: '2px', background: '#d9a441',
              margin: '22px auto 0', opacity: 0.75
            }} />
            <div style={{ fontSize: '14px', color: '#9c988d', marginTop: '16px' }}>
              {slide.count} {slide.count === 1 ? 'photo' : 'photos'}
            </div>
          </div>
        ) : slideImageSrc(slide.img) ? (
          <img
            src={slideImageSrc(slide.img)}
            alt={slide.img.filename || ''}
            draggable={false}
            style={{
              // Rule 1: fit whole, never crop.
              maxWidth: '100%', maxHeight: '100%',
              width: 'auto', height: 'auto',
              objectFit: 'contain', display: 'block'
            }}
          />
        ) : (
          <div style={{ color: '#6b6d75', fontSize: '14px', textAlign: 'center', padding: '24px' }}>
            This frame has no preview image
            {slide.img.filename ? <div style={{ marginTop: '6px', color: '#4e5058' }}>{slide.img.filename}</div> : null}
          </div>
        )}
      </div>

      {/* ── Note band. Absent note or notes-off = the frame keeps the screen ─ */}
      {noteShowing && (
        <div style={{
          flexShrink: 0,
          padding: '16px 8vw 26px',
          textAlign: 'center',
          fontSize: 'clamp(13px, 1.5vw, 17px)',
          lineHeight: 1.5,
          color: '#c9c6bd',
          whiteSpace: 'pre-wrap'
        }}>
          {note}
        </div>
      )}

      {/* ── Controls: fade out when the mouse goes still ──────────────────── */}
      <div style={{
        position: 'absolute', inset: 0, pointerEvents: 'none',
        opacity: uiVisible ? 1 : 0,
        transition: 'opacity 260ms ease'
      }}>
        {/* Top-left: where you are */}
        <div style={{
          position: 'absolute', top: '18px', left: '22px',
          display: 'flex', alignItems: 'center', gap: '10px',
          fontSize: '12.5px', color: '#8e9099'
        }}>
          <span style={{ color: '#c9c6bd' }}>{deckName}</span>
          {slide.type === 'photo' && (
            <>
              <span style={{ opacity: 0.5 }}>·</span>
              <span>{slide.label}</span>
              <span style={{ opacity: 0.5 }}>·</span>
              <span>{slide.number} / {totalPhotos}</span>
            </>
          )}
        </div>

        {/* Top-right: notes toggle + exit */}
        <div style={{
          position: 'absolute', top: '14px', right: '18px',
          display: 'flex', gap: '8px', pointerEvents: 'auto'
        }}>
          <button
            onClick={(e) => { e.stopPropagation(); toggleNotes(); }}
            style={{ ...pill, color: showNotes ? '#d9a441' : '#8e9099' }}
            title="Show or hide the note under each frame (N)"
          >
            {showNotes ? '💬 Notes on' : '💬 Notes off'}
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); close(); }}
            style={pill}
            title="Leave the presentation (Esc)"
          >
            ✕ Exit
          </button>
        </div>

        {/* Edges: prev / next */}
        <button
          onClick={(e) => { e.stopPropagation(); goPrev(); }}
          disabled={atStart}
          style={{
            ...edgeBtn,
            position: 'absolute', left: '18px', top: '50%', transform: 'translateY(-50%)',
            pointerEvents: 'auto',
            opacity: atStart ? 0.25 : 1,
            cursor: atStart ? 'default' : 'pointer'
          }}
          title="Previous (←)"
        >
          ‹
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); goNext(); }}
          disabled={atEnd}
          style={{
            ...edgeBtn,
            position: 'absolute', right: '18px', top: '50%', transform: 'translateY(-50%)',
            pointerEvents: 'auto',
            opacity: atEnd ? 0.25 : 1,
            cursor: atEnd ? 'default' : 'pointer'
          }}
          title="Next (→)"
        >
          ›
        </button>

        {/* Opening hint, then it gets out of the way for good */}
        {hintVisible && (
          <div style={{
            position: 'absolute', bottom: '22px', left: '50%', transform: 'translateX(-50%)',
            background: 'rgba(20,20,22,0.72)',
            border: '1px solid rgba(255,255,255,0.12)',
            borderRadius: '999px',
            padding: '9px 18px',
            fontSize: '12.5px', color: '#9c988d',
            whiteSpace: 'nowrap'
          }}>
            ← → or space to move · N for notes · Esc to exit
          </div>
        )}
      </div>
    </div>
  );
}
