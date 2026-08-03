import { useEffect, useRef, useState } from 'react';
import { SIDEBAR_WIDTH } from './Sidebar';
import { useIsMobile } from '../hooks/useIsMobile';
import { useAuth } from '../AuthContext';

// ── Confirm step — small inline modal, dark panel look ────────────────────────
function ConfirmModal({ text, confirmLabel = 'Confirm', danger, busy, onConfirm, onCancel }) {
  return (
    <div
      onClick={onCancel}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
        zIndex: 1100, display: 'flex', alignItems: 'center', justifyContent: 'center'
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: '#2a2c31',
          border: '1px solid rgba(255,255,255,0.12)',
          borderRadius: '12px',
          padding: '18px 20px',
          width: '320px',
          boxShadow: '0 20px 48px rgba(0,0,0,0.6)',
          animation: 'fapop 0.12s ease'
        }}
      >
        <div style={{ fontSize: '13.5px', color: '#efeadd', lineHeight: 1.5, marginBottom: '16px' }}>
          {text}
        </div>
        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            disabled={busy}
            style={{
              background: 'none', border: '1px solid rgba(255,255,255,0.12)',
              color: '#9c988d', borderRadius: '6px', padding: '7px 14px',
              cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit'
            }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            style={{
              background: danger ? 'rgba(255,180,171,0.18)' : 'rgba(184,206,161,0.18)',
              border: `1px solid ${danger ? 'rgba(255,180,171,0.6)' : 'rgba(184,206,161,0.6)'}`,
              color: danger ? '#ffb4ab' : '#b8cea1',
              borderRadius: '6px', padding: '7px 14px',
              cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit',
              opacity: busy ? 0.6 : 1
            }}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function TagModeBar({
  images,
  totalResults = 0,   // V32: how many images the current search matches in total,
                      // not just how many thumbnails the grid has loaded
  selectedIds,
  setSelectedIds,
  onSelectAllResults, // V32: async () => {ok, count} — selects EVERY match, via the server
  onExit,
  onBulkChanged, // (patchedIds, patchFn) — let Home.jsx update local image state
  onBulkMutated, // () — a bulk write (tag/filmography) just completed; re-sync active filters
  onBulkDeleted, // (deletedIds) — the selected photos were removed from the library
  onResync,      // () — re-run the active search; used when a delete's outcome is unknown
  onCrop,        // V18: open the crop review modal for the current selection
}) {
  // V18: Select Mode is open to everyone now (friends crop their own images
  // and add to their decks); the tag/filmography panels stay admin-only
  // because their backend endpoints are.
  const { isAdmin } = useAuth();
  const [categories, setCategories] = useState([]);
  const [summary, setSummary] = useState({ total: 0, tags: [] });
  const [suggestions, setSuggestions] = useState([]);
  const [tagSearch, setTagSearch] = useState('');
  const [deleteMsg, setDeleteMsg] = useState('');
  const deleteMsgTimer = useRef(null);

  // Apply-tag panel state
  const [tagName, setTagName] = useState('');
  const [tagCategory, setTagCategory] = useState('');
  const [autocomplete, setAutocomplete] = useState([]);
  const [showAuto, setShowAuto] = useState(false);
  const autoDebounce = useRef(null);
  const summaryDebounce = useRef(null);

  // Confirm modal state — { kind: 'apply'|'remove'|'filmography-set'|'filmography-clear', ... }
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);

  // Set-filmography panel state
  const [filmTitle, setFilmTitle] = useState('');
  const [filmDirector, setFilmDirector] = useState('');
  const [filmDp, setFilmDp] = useState('');
  const [filmYear, setFilmYear] = useState('');

  // Add-to-Deck panel state
  const [decks, setDecks] = useState([]);
  const [decksLoaded, setDecksLoaded] = useState(false);
  const [showDeckPicker, setShowDeckPicker] = useState(false);
  const [newDeckName, setNewDeckName] = useState('');
  const [addingToDeck, setAddingToDeck] = useState(false);
  const [addDeckMsg, setAddDeckMsg] = useState('');
  const addDeckMsgTimer = useRef(null);

  const count = selectedIds.size;

  // ── Select all results (V32) ───────────────────────────────────────────────
  const [selectingAll, setSelectingAll] = useState(false);
  const [selectMsg, setSelectMsg] = useState('');
  const everythingLoaded = !totalResults || images.length >= totalResults;
  const allLoadedAndSelected = everythingLoaded && count > 0 && count >= images.length;

  const selectAll = async () => {
    if (selectingAll) return;
    setSelectMsg('');
    // Everything's already on screen — no round trip needed.
    if (everythingLoaded || !onSelectAllResults) {
      setSelectedIds(new Set(images.map(i => i.id)));
      return;
    }
    setSelectingAll(true);
    const result = await onSelectAllResults();
    setSelectingAll(false);
    // Never silently select fewer than asked — saying nothing is the exact
    // failure this button was built to fix.
    if (!result?.ok) setSelectMsg("Couldn't reach the server — nothing selected.");
  };

  // ── Load fixed category list once ──────────────────────────────────────────
  useEffect(() => {
    fetch('/api/tag-categories')
      .then(res => res.json())
      .then(data => setCategories(Array.isArray(data) ? data : []))
      .catch(() => setCategories([]));
  }, []);

  // ── Fetch summary + suggestions, debounced on selection change ────────────
  const refetchSelectionData = () => {
    if (count === 0) {
      setSummary({ total: 0, tags: [] });
      setSuggestions([]);
      return;
    }
    const ids = Array.from(selectedIds);
    fetch('/api/tags/selection-summary', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_ids: ids })
    })
      .then(res => res.json())
      .then(data => {
        setSummary({ total: data.total || 0, tags: data.tags || [] });
        // Autofill: a field only comes back non-null when EVERY selected
        // image already agrees on it — lets you glance at "Spike Jonze"
        // already sitting in Director and know the whole batch matches,
        // without having to retype it just to touch the DP field.
        const cf = data.common_filmography || {};
        setFilmTitle(cf.title || '');
        setFilmDirector(cf.director || '');
        setFilmDp(cf.dp || '');
        setFilmYear(cf.year || '');
      })
      .catch(() => {});

    fetch('/api/tags/suggestions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_ids: ids })
    })
      .then(res => res.json())
      .then(data => setSuggestions(data.suggestions || []))
      .catch(() => setSuggestions([]));
  };

  useEffect(() => {
    clearTimeout(summaryDebounce.current);
    summaryDebounce.current = setTimeout(refetchSelectionData, 200);
    return () => clearTimeout(summaryDebounce.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIds]);

  // ── Tag-name autocomplete (reuse existing endpoint) ────────────────────────
  useEffect(() => {
    clearTimeout(autoDebounce.current);
    if (!tagName.trim()) {
      setAutocomplete([]);
      setShowAuto(false);
      return;
    }
    autoDebounce.current = setTimeout(async () => {
      try {
        const params = new URLSearchParams({ q: tagName });
        const res = await fetch(`/api/autocomplete?${params}`);
        const data = await res.json();
        setAutocomplete(data);
        setShowAuto(data.length > 0);
      } catch {}
    }, 200);
    return () => clearTimeout(autoDebounce.current);
  }, [tagName]);

  const pickAutocomplete = (opt) => {
    setTagName(opt.value);
    // Convenience default — pre-select matching category, dropdown stays editable
    const match = categories.find(c => c.key === opt.category);
    if (match) setTagCategory(match.key);
    setShowAuto(false);
  };

  const pickSuggestion = (s) => {
    setTagName(s.value);
    setTagCategory(s.category);
    setShowAuto(false);
  };

  const catLabelFor = (key) => categories.find(c => c.key === key)?.label || key || 'Misc';
  const catColorFor = (key) => categories.find(c => c.key === key)?.color || '#8b7cf6';

  // ── Add to Deck ─────────────────────────────────────────────────────────────
  const loadDecks = () => {
    fetch('/api/decks')
      .then(res => res.json())
      .then(data => { setDecks(Array.isArray(data) ? data : []); setDecksLoaded(true); })
      .catch(() => { setDecks([]); setDecksLoaded(true); });
  };

  const toggleDeckPicker = () => {
    setShowDeckPicker(v => {
      const next = !v;
      if (next && !decksLoaded) loadDecks();
      return next;
    });
  };

  const flashAddMsg = (msg) => {
    setAddDeckMsg(msg);
    clearTimeout(addDeckMsgTimer.current);
    addDeckMsgTimer.current = setTimeout(() => setAddDeckMsg(''), 4000);
  };

  const addSelectionToDeck = async (deckId, deckName) => {
    if (addingToDeck) return;
    setAddingToDeck(true);
    const ids = Array.from(selectedIds);
    try {
      await fetch(`/api/decks/${deckId}/images`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_ids: ids })
      });
      flashAddMsg(`Added ${ids.length} photo${ids.length === 1 ? '' : 's'} to "${deckName}"`);
    } catch (e) {
      console.error('Add to deck failed', e);
    }
    setAddingToDeck(false);
  };

  const createDeckAndAdd = async () => {
    const name = newDeckName.trim();
    if (!name || addingToDeck) return;
    setAddingToDeck(true);
    const ids = Array.from(selectedIds);
    try {
      const res = await fetch('/api/decks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      });
      const deck = await res.json();
      await fetch(`/api/decks/${deck.id}/images`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_ids: ids })
      });
      setDecks(prev => [{ ...deck, image_count: ids.length }, ...prev]);
      setNewDeckName('');
      flashAddMsg(`Added ${ids.length} photo${ids.length === 1 ? '' : 's'} to "${name}"`);
    } catch (e) {
      console.error('Create deck and add failed', e);
    }
    setAddingToDeck(false);
  };

  // ── Apply / remove flow ────────────────────────────────────────────────────
  const openApplyConfirm = () => {
    const value = tagName.trim().toLowerCase();
    if (!value) return;
    setConfirm({ kind: 'apply', category: tagCategory, value, catLabel: catLabelFor(tagCategory) });
  };

  const openRemoveConfirm = (tag) => {
    setConfirm({ kind: 'remove', category: tag.category, value: tag.value, catLabel: tag.catLabel });
  };

  const openBulkDeleteConfirm = () => setConfirm({ kind: 'bulk-delete' });

  const flashDeleteMsg = (msg) => {
    setDeleteMsg(msg);
    clearTimeout(deleteMsgTimer.current);
    deleteMsgTimer.current = setTimeout(() => setDeleteMsg(''), 5000);
  };

  // Group the (already intersection-only) shared tags by category, in the
  // same order as the fixed category list. When a search term is active,
  // tags matching it float to the top within their category, and any
  // category containing a match moves ahead of categories that don't —
  // so "night" doesn't get lost under a Mood section sitting at the bottom.
  const groupedSharedTags = (() => {
    const q = tagSearch.trim().toLowerCase();
    const byCategory = new Map();
    for (const tag of summary.tags) {
      if (!byCategory.has(tag.category)) byCategory.set(tag.category, []);
      byCategory.get(tag.category).push(tag);
    }
    const catOrder = categories.map(c => c.key);
    const orderedKeys = [...byCategory.keys()].sort((a, b) => {
      if (q) {
        const aHas = byCategory.get(a).some(t => t.value.toLowerCase().includes(q));
        const bHas = byCategory.get(b).some(t => t.value.toLowerCase().includes(q));
        if (aHas !== bHas) return aHas ? -1 : 1;
      }
      const ia = catOrder.indexOf(a), ib = catOrder.indexOf(b);
      return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
    });
    return orderedKeys.map(catKey => {
      const tags = byCategory.get(catKey);
      const sorted = q
        ? [...tags].sort((a, b) => {
            const am = a.value.toLowerCase().includes(q) ? 0 : 1;
            const bm = b.value.toLowerCase().includes(q) ? 0 : 1;
            return am - bm;
          })
        : tags;
      return { catKey, tags: sorted };
    });
  })();

  // ── Set / clear filmography flow ───────────────────────────────────────────
  const canSetFilm = filmTitle.trim() || filmDirector.trim() || filmDp.trim() || filmYear.trim();

  const openFilmSetConfirm = () => {
    if (!canSetFilm) return;
    // Only the fields that actually have something in them get applied —
    // blank fields (whether never touched or left un-autofilled) mean
    // "leave this field as each image already has it," not "clear it."
    const fields = { title: filmTitle.trim(), director: filmDirector.trim(), dp: filmDp.trim(), year: filmYear.trim() };
    const touched = Object.fromEntries(Object.entries(fields).filter(([, v]) => v));
    setConfirm({ kind: 'filmography-set', touched });
  };

  const openFilmClearConfirm = () => setConfirm({ kind: 'filmography-clear' });

  const runConfirm = async () => {
    if (!confirm) return;
    setBusy(true);
    const ids = Array.from(selectedIds);
    try {
      if (confirm.kind === 'bulk-delete') {
        // Its own try/catch, not the shared one below: a network hiccup or a
        // slow response here can throw AFTER the server has already moved
        // the files in Drive and deleted the DB rows — the generic catch
        // used to just log that and leave the stale selection on screen, so
        // "9 selected" persisted even though the photos were already gone
        // (Ryan hit this: files sitting in _Removed, grid/selection unmoved).
        // Since we can't tell from here which ids actually succeeded, resync
        // the grid from the server and drop the whole selection rather than
        // risk it pointing at photos that no longer exist.
        try {
          const res = await fetch('/api/images/bulk-delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image_ids: ids })
          });
          const data = await res.json();
          const deletedIds = data.deleted || [];
          const failed = data.errors || [];
          onBulkDeleted?.(deletedIds);
          setSelectedIds(prev => {
            const next = new Set(prev);
            deletedIds.forEach(id => next.delete(id));
            return next;
          });
          flashDeleteMsg(
            failed.length > 0
              ? `Deleted ${deletedIds.length}, ${failed.length} failed — ${failed[0].error}`
              : `Deleted ${deletedIds.length} photo${deletedIds.length === 1 ? '' : 's'}`
          );
        } catch (e) {
          console.error('Bulk delete: response failed, resyncing to find out what actually happened', e);
          flashDeleteMsg("Lost track of that delete — refreshing to check what went through.");
          setSelectedIds(new Set());
          onResync?.();
        }
        setBusy(false);
        setConfirm(null);
        return;
      }
      if (confirm.kind === 'filmography-set') {
        await fetch('/api/filmography/bulk-set', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image_ids: ids, ...confirm.touched })
        });
        // Only overlay the touched fields onto each image's own existing
        // filmography — mirrors the backend's per-field merge exactly.
        onBulkChanged?.(ids, (img) => {
          if (!ids.includes(img.id)) return img;
          const merged = { ...(img.filmography || {}), ...confirm.touched };
          const hasAny = merged.title || merged.director || merged.dp || merged.year;
          return { ...img, filmography: hasAny ? merged : null };
        });
      } else if (confirm.kind === 'filmography-clear') {
        await fetch('/api/filmography/bulk-clear', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image_ids: ids })
        });
        onBulkChanged?.(ids, (img) => ids.includes(img.id) ? { ...img, filmography: null } : img);
      } else if (confirm.kind === 'apply') {
        await fetch('/api/tags/bulk-apply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image_ids: ids, category: confirm.category, value: confirm.value })
        });
        // Update local image state so grid/detail reflect the new tag without a full reload
        onBulkChanged?.(ids, (img) => {
          if (!ids.includes(img.id)) return img;
          const already = (img.tags || []).some(t => t.category === confirm.category && t.value === confirm.value);
          if (already) return img;
          return { ...img, tags: [...(img.tags || []), { category: confirm.category, value: confirm.value }] };
        });
        setTagName('');
        setTagCategory('');
      } else {
        await fetch('/api/tags/bulk-remove', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image_ids: ids, category: confirm.category, value: confirm.value })
        });
        onBulkChanged?.(ids, (img) => {
          if (!ids.includes(img.id)) return img;
          return { ...img, tags: (img.tags || []).filter(t => !(t.category === confirm.category && t.value === confirm.value)) };
        });
      }
      // onBulkChanged only patches each photo's own fields in local state —
      // it never re-checks whether a photo still belongs under the currently
      // active search filters (e.g. a photo just untagged "car" while the
      // grid is filtered to car). onBulkMutated tells Home.jsx a bulk write
      // just happened so it can re-run the active search and drop anything
      // that no longer qualifies, instead of leaving stale results on screen.
      onBulkMutated?.();
      refetchSelectionData();
    } catch (e) {
      console.error('Bulk tag operation failed', e);
    }
    setBusy(false);
    setConfirm(null);
  };

  const canApply = tagName.trim().length > 0;
  const isMobile = useIsMobile();

  return (
    <>
      <div
        data-tagmode-area
        style={{
          position: 'fixed', left: isMobile ? 0 : `${SIDEBAR_WIDTH}px`, right: 0, bottom: 0,
          zIndex: 900,
          background: '#1a1c20',
          borderTop: '1px solid #44474f',
          boxShadow: '0 -12px 32px rgba(0,0,0,0.45)',
          maxHeight: isMobile ? '80vh' : '46vh',
          overflowY: 'auto'
        }}
      >
        {/* Top row — selection controls, always visible */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: '10px',
          padding: isMobile ? '12px 14px' : '12px 20px',
          borderBottom: count > 0 ? '1px solid #2a2c31' : 'none',
          flexWrap: isMobile ? 'wrap' : 'nowrap',
          rowGap: '8px'
        }}>
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: '6px',
            background: 'rgba(184,206,161,0.14)',
            border: '1px solid rgba(184,206,161,0.5)',
            borderRadius: '99px',
            padding: '5px 12px',
            fontSize: '12.5px', color: '#b8cea1', fontWeight: 500
          }}>
            {count} selected
          </span>

          {/* V32: "Select all loaded" used to grab only the thumbnails the
              grid had scrolled far enough to load — 60 of 118 results, with
              nothing on screen saying so. Now the button always means every
              match, and the count in the label is the real one. */}
          <button
            onClick={selectAll}
            disabled={selectingAll || allLoadedAndSelected}
            title={everythingLoaded
              ? 'Select every image in these results'
              : 'Select every image these filters match — including the ones further down that haven\'t loaded yet'}
            style={{ ...ghostBtn(), opacity: selectingAll ? 0.6 : 1 }}
          >
            {selectingAll ? 'Selecting…' : `Select all ${totalResults || images.length}`}
          </button>
          <button onClick={() => setSelectedIds(new Set())} style={ghostBtn()}>
            Clear selection
          </button>

          {selectMsg && (
            <span style={{ fontSize: '11.5px', color: '#ffb4ab' }}>{selectMsg}</span>
          )}

          {count > 0 && onCrop && (
            <button
              onClick={onCrop}
              title="Auto-detect and remove letterbox bars / screenshot chrome from the selected images"
              style={{
                background: 'rgba(217,164,65,0.14)',
                border: '1px solid rgba(217,164,65,0.5)',
                color: '#d9a441', borderRadius: '8px', padding: '7px 14px',
                cursor: 'pointer', fontSize: '12px', fontWeight: 600, fontFamily: 'inherit'
              }}
            >
              ✂ Crop {count}
            </button>
          )}

          {count > 0 && (
            <button
              onClick={openBulkDeleteConfirm}
              title="Move the selected photos to Drive's _Removed folder and remove them from Frame Atlas"
              style={{
                background: 'rgba(255,180,171,0.14)',
                border: '1px solid rgba(255,180,171,0.5)',
                color: '#ffb4ab', borderRadius: '8px', padding: '7px 14px',
                cursor: 'pointer', fontSize: '12px', fontWeight: 600, fontFamily: 'inherit'
              }}
            >
              🗑 Delete {count}
            </button>
          )}

          {deleteMsg && (
            <span style={{ fontSize: '11.5px', color: '#b8cea1' }}>{deleteMsg}</span>
          )}

          <div style={{ flex: 1 }} />

          <button onClick={onExit} style={ghostBtn('#ffb4ab', 'rgba(255,180,171,0.35)')}>
            Exit Select Mode
          </button>
        </div>

        {count > 0 && (
          <div style={{
            display: 'flex', flexWrap: 'wrap', gap: '20px',
            padding: isMobile ? '16px 14px' : '16px 20px'
          }}>
            {isAdmin && <>
            {/* Apply tag panel */}
            <div style={{ minWidth: '280px', flex: '1 1 280px' }} data-tagmode-area>
              <div style={sectionLabel()}>APPLY TAG</div>
              <div style={{ display: 'flex', gap: '6px', position: 'relative' }}>
                <div style={{ position: 'relative', flex: 1 }}>
                  <input
                    value={tagName}
                    onChange={e => setTagName(e.target.value)}
                    onFocus={() => { if (autocomplete.length) setShowAuto(true); }}
                    placeholder="Tag name…"
                    style={inputStyle()}
                  />
                  {showAuto && autocomplete.length > 0 && (
                    <div style={{
                      position: 'absolute', bottom: '38px', left: 0, right: 0,
                      background: '#2a2c31',
                      border: '1px solid #44474f',
                      borderRadius: '10px',
                      boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
                      maxHeight: '220px', overflowY: 'auto',
                      zIndex: 60
                    }}>
                      {autocomplete.map(opt => (
                        <button
                          key={opt.value}
                          onMouseDown={() => pickAutocomplete(opt)}
                          style={{
                            width: '100%', display: 'flex', alignItems: 'center',
                            justifyContent: 'space-between', gap: '10px',
                            padding: '8px 12px',
                            background: 'transparent', border: 'none',
                            cursor: 'pointer', textAlign: 'left', fontFamily: 'inherit'
                          }}
                          onMouseEnter={e => e.currentTarget.style.background = '#37393e'}
                          onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                        >
                          <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <span style={{ width: '7px', height: '7px', borderRadius: '2px', background: opt.color, flexShrink: 0 }} />
                            <span style={{ fontSize: '13px', color: '#e2e2e6' }}>{opt.value}</span>
                            <span style={{ fontSize: '10.5px', color: '#8e9099' }}>{opt.catLabel}</span>
                          </span>
                          <span style={{ fontSize: '10px', color: '#8e9099' }}>{opt.count}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <select
                  value={tagCategory}
                  onChange={e => setTagCategory(e.target.value)}
                  style={{ ...inputStyle(), flex: '0 0 140px', cursor: 'pointer' }}
                >
                  <option value="">Category…</option>
                  {categories.map(c => (
                    <option key={c.key} value={c.key}>{c.label}</option>
                  ))}
                </select>
              </div>
              <button
                onClick={openApplyConfirm}
                disabled={!canApply}
                style={{
                  marginTop: '8px',
                  background: canApply ? '#d9a441' : 'rgba(217,164,65,0.2)',
                  color: canApply ? '#3d2f00' : '#8e9099',
                  border: 'none', borderRadius: '8px',
                  padding: '8px 14px', fontSize: '12.5px', fontWeight: 500,
                  cursor: canApply ? 'pointer' : 'default',
                  fontFamily: 'inherit', width: '100%'
                }}
              >
                Apply to {count} image{count === 1 ? '' : 's'}
              </button>
            </div>

            {/* Set/clear filmography panel */}
            <div style={{ minWidth: '280px', flex: '1 1 280px' }} data-tagmode-area>
              <div style={sectionLabel()}>FILMOGRAPHY</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                <input
                  value={filmTitle}
                  onChange={e => setFilmTitle(e.target.value)}
                  placeholder="Title"
                  style={inputStyle()}
                />
                <div style={{ display: 'flex', gap: '6px' }}>
                  <input
                    value={filmDirector}
                    onChange={e => setFilmDirector(e.target.value)}
                    placeholder="Director"
                    style={inputStyle()}
                  />
                  <input
                    value={filmDp}
                    onChange={e => setFilmDp(e.target.value)}
                    placeholder="DP"
                    style={inputStyle()}
                  />
                  <input
                    value={filmYear}
                    onChange={e => setFilmYear(e.target.value)}
                    placeholder="Year"
                    style={{ ...inputStyle(), flex: '0 0 70px' }}
                  />
                </div>
              </div>
              <div style={{ display: 'flex', gap: '6px', marginTop: '8px' }}>
                <button
                  onClick={openFilmSetConfirm}
                  disabled={!canSetFilm}
                  style={{
                    flex: 1,
                    background: canSetFilm ? '#d9a441' : 'rgba(217,164,65,0.2)',
                    color: canSetFilm ? '#3d2f00' : '#8e9099',
                    border: 'none', borderRadius: '8px',
                    padding: '8px 10px', fontSize: '12.5px', fontWeight: 500,
                    cursor: canSetFilm ? 'pointer' : 'default', fontFamily: 'inherit'
                  }}
                >
                  Set on {count}
                </button>
                <button
                  onClick={openFilmClearConfirm}
                  title="Clear filmography from every selected image"
                  style={{
                    background: 'none', border: '1px solid rgba(255,180,171,0.35)',
                    color: '#ffb4ab', borderRadius: '8px',
                    padding: '8px 10px', fontSize: '12.5px',
                    cursor: 'pointer', fontFamily: 'inherit'
                  }}
                >
                  Clear
                </button>
              </div>
            </div>

            {/* Shared tags panel — only tags every selected image carries */}
            <div style={{ minWidth: '260px', flex: '1 1 260px' }} data-tagmode-area>
              <div style={sectionLabel()}>SHARED TAGS (ALL {summary.total})</div>
              {/* V32: this list is a strict intersection, and before now it
                  never said so. Looking for a tag that plainly IS on some of
                  your photos and not finding it here is baffling unless the
                  rule is written down — the rule itself is right, because a
                  Remove button must not be able to touch a photo that never
                  had the tag. Say it every time, not just when empty. */}
              {summary.total > 1 && (
                <div style={{ fontSize: '11px', color: '#8e9099', marginBottom: '9px', lineHeight: 1.5 }}>
                  Only tags that are on <strong>all {summary.total}</strong> selected photos show up here,
                  so removing one can never touch a photo that didn't have it.
                </div>
              )}
              {summary.tags.length === 0 ? (
                <div style={{ fontSize: '11.5px', color: '#8e9099', lineHeight: 1.55 }}>
                  {summary.total === 1
                    ? 'This photo has no tags yet.'
                    : `There isn't a single tag that all ${summary.total} of these photos have in common. ` +
                      'Select fewer photos to find one — or, to clear a tag out of your whole library, ' +
                      'search for that tag and use the “Remove tag from all…” button above the grid.'}
                </div>
              ) : (
                <>
                  {summary.tags.length > 6 && (
                    <input
                      value={tagSearch}
                      onChange={e => setTagSearch(e.target.value)}
                      placeholder="Search shared tags…"
                      style={{ ...inputStyle(), marginBottom: '10px' }}
                    />
                  )}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                    {groupedSharedTags.map(({ catKey, tags }) => (
                      <div key={catKey}>
                        <div style={{ fontSize: '10px', color: '#8e9099', marginBottom: '5px' }}>
                          {catLabelFor(catKey)}
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                          {tags.map(tag => {
                            const isMatch = tagSearch.trim() &&
                              tag.value.toLowerCase().includes(tagSearch.trim().toLowerCase());
                            return (
                              <span
                                key={`${tag.category}:${tag.value}`}
                                style={{
                                  display: 'inline-flex', alignItems: 'center', gap: '6px',
                                  background: isMatch ? 'rgba(217,164,65,0.2)' : 'rgba(201,162,83,0.12)',
                                  border: `1px solid ${isMatch ? 'rgba(217,164,65,0.7)' : 'rgba(201,162,83,0.25)'}`,
                                  borderRadius: '5px',
                                  padding: '4px 9px',
                                  fontSize: '11.5px', color: tag.color || '#dcbd76'
                                }}
                              >
                                {tag.value}
                                <button
                                  onClick={() => openRemoveConfirm(tag)}
                                  title="Remove from selection"
                                  style={{
                                    background: 'none', border: 'none', color: '#ffb4ab',
                                    cursor: 'pointer', padding: 0, fontSize: '13px', lineHeight: 1
                                  }}
                                >×</button>
                              </span>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>

            {/* Suggestions panel */}
            {suggestions.length > 0 && (
              <div style={{ minWidth: '260px', flex: '1 1 260px' }}>
                <div style={sectionLabel()}>SUGGESTIONS</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                  {suggestions.map(s => (
                    <button
                      key={`${s.category}:${s.value}`}
                      onClick={() => pickSuggestion(s)}
                      title={`Stage "${s.value}" in Apply Tag`}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: '6px',
                        background: 'transparent',
                        border: `1px dashed ${s.color || '#8b7cf6'}88`,
                        borderRadius: '5px',
                        padding: '4px 9px',
                        fontSize: '11.5px', color: s.color || '#a99bf7',
                        cursor: 'pointer', fontFamily: 'inherit'
                      }}
                    >
                      {s.value}
                      <span style={{ fontSize: '10px', color: '#8e9099' }}>{s.catLabel}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            </>}

            {/* Add to Deck panel */}
            <div style={{ minWidth: '220px', flex: '0 1 220px', position: 'relative' }} data-tagmode-area>
              <div style={sectionLabel()}>ADD TO DECK</div>
              <button
                onClick={toggleDeckPicker}
                style={{
                  background: showDeckPicker ? 'rgba(217,164,65,0.14)' : 'none',
                  border: `1px solid ${showDeckPicker ? 'rgba(217,164,65,0.5)' : '#44474f'}`,
                  color: showDeckPicker ? '#d9a441' : '#e2e2e6',
                  borderRadius: '8px', padding: '8px 14px',
                  fontSize: '12.5px', fontWeight: 500,
                  cursor: 'pointer', fontFamily: 'inherit', width: '100%'
                }}
              >
                + Add {count} image{count === 1 ? '' : 's'} to deck…
              </button>

              {addDeckMsg && (
                <div style={{ marginTop: '8px', fontSize: '11.5px', color: '#b8cea1' }}>
                  {addDeckMsg}
                </div>
              )}

              {showDeckPicker && (
                <div style={{
                  position: 'absolute', bottom: '38px', left: 0, right: 0,
                  background: '#2a2c31',
                  border: '1px solid #44474f',
                  borderRadius: '10px',
                  boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
                  maxHeight: '260px', overflowY: 'auto',
                  zIndex: 60
                }}>
                  {decksLoaded && decks.length === 0 && (
                    <div style={{ padding: '10px 12px', fontSize: '11.5px', color: '#8e9099' }}>
                      No decks yet — create one below.
                    </div>
                  )}
                  {decks.map(deck => (
                    <button
                      key={deck.id}
                      onClick={() => addSelectionToDeck(deck.id, deck.name)}
                      disabled={addingToDeck}
                      style={{
                        width: '100%', display: 'flex', alignItems: 'center',
                        justifyContent: 'space-between', gap: '10px',
                        padding: '8px 12px',
                        background: 'transparent', border: 'none',
                        cursor: 'pointer', textAlign: 'left', fontFamily: 'inherit'
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = '#37393e'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    >
                      <span style={{ fontSize: '13px', color: '#e2e2e6' }}>{deck.name}</span>
                      <span style={{ fontSize: '10px', color: '#8e9099' }}>{deck.image_count}</span>
                    </button>
                  ))}
                  <div style={{
                    display: 'flex', gap: '6px', padding: '8px 10px',
                    borderTop: decks.length > 0 ? '1px solid #44474f' : 'none'
                  }}>
                    <input
                      value={newDeckName}
                      onChange={e => setNewDeckName(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') createDeckAndAdd(); }}
                      placeholder="+ New deck…"
                      style={{
                        flex: 1, background: '#111317', color: '#e2e2e6',
                        border: '1px solid #44474f', borderRadius: '6px',
                        padding: '6px 8px', fontSize: '12px',
                        fontFamily: 'inherit', outline: 'none'
                      }}
                    />
                    <button
                      onClick={createDeckAndAdd}
                      disabled={!newDeckName.trim() || addingToDeck}
                      style={{
                        background: newDeckName.trim() ? '#d9a441' : 'rgba(217,164,65,0.2)',
                        color: newDeckName.trim() ? '#3d2f00' : '#8e9099',
                        border: 'none', borderRadius: '6px',
                        padding: '0 10px', fontSize: '12px', fontWeight: 500,
                        cursor: newDeckName.trim() ? 'pointer' : 'default',
                        fontFamily: 'inherit'
                      }}
                    >
                      Add
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {confirm && (
        <ConfirmModal
          text={
            confirm.kind === 'apply'
              ? <>Add "<strong>{confirm.value}</strong>" ({confirm.catLabel}) to <strong>{count}</strong> image{count === 1 ? '' : 's'}?</>
              : confirm.kind === 'remove'
              ? <>Remove "<strong>{confirm.value}</strong>" from <strong>{count}</strong> image{count === 1 ? '' : 's'}?</>
              : confirm.kind === 'filmography-set'
              ? <>Set {filmFieldSummary(confirm.touched)} on <strong>{count}</strong> image{count === 1 ? '' : 's'}?
                  Any other filmography field on those images stays as it already is.</>
              : confirm.kind === 'filmography-clear'
              ? <>Clear filmography from <strong>{count}</strong> image{count === 1 ? '' : 's'}?</>
              : <>Delete <strong>{count}</strong> photo{count === 1 ? '' : 's'}? Moved to Drive's _Removed folder.</>
          }
          confirmLabel={
            confirm.kind === 'apply' ? 'Apply'
            : confirm.kind === 'filmography-set' ? 'Set filmography'
            : confirm.kind === 'filmography-clear' ? 'Clear'
            : confirm.kind === 'bulk-delete' ? 'Delete'
            : 'Remove'
          }
          danger={confirm.kind === 'remove' || confirm.kind === 'filmography-clear' || confirm.kind === 'bulk-delete'}
          busy={busy}
          onConfirm={runConfirm}
          onCancel={() => !busy && setConfirm(null)}
        />
      )}
    </>
  );
}

const FILM_FIELD_LABELS = { title: 'title', director: 'director', dp: 'DP', year: 'year' };

function filmFieldSummary(touched) {
  const parts = Object.entries(touched).map(([field, value]) => `${FILM_FIELD_LABELS[field]} "${value}"`);
  if (parts.length === 1) return parts[0];
  if (parts.length === 2) return parts.join(' and ');
  return `${parts.slice(0, -1).join(', ')}, and ${parts[parts.length - 1]}`;
}

function ghostBtn(color = '#c4c6d0', borderColor = '#44474f') {
  return {
    background: 'none',
    border: `1px solid ${borderColor}`,
    color, borderRadius: '8px', padding: '7px 12px',
    cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit'
  };
}

function sectionLabel() {
  return {
    fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em',
    color: '#8e9099', marginBottom: '8px'
  };
}

function inputStyle() {
  return {
    background: '#111317', color: '#e2e2e6',
    border: '1px solid #44474f',
    borderRadius: '8px', padding: '8px 10px',
    fontSize: '12.5px', fontFamily: 'inherit', outline: 'none',
    width: '100%'
  };
}
