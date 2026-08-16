import { createContext, useContext, useState, useRef, useCallback, useEffect } from 'react';
import { useAuth } from './AuthContext';
import { useToast } from './ToastContext';

const SyncContext = createContext(null);

// V48: sync used to be its own page you had to sit on and watch. This
// provider owns the two-phase job (Drive sync, then auto-tagging) at the
// app shell level — above <Routes> — specifically so it keeps running and
// still fires the completion toast even if you've already navigated off to
// Decks or Analytics while it works. A phase tracked inside Home's own
// state would stop polling the moment Home unmounted.
export function SyncProvider({ children }) {
  const { isAdmin } = useAuth();
  const { showToast } = useToast();

  const [phase, setPhase] = useState('idle'); // 'idle' | 'syncing' | 'tagging'
  const [syncProcessed, setSyncProcessed] = useState(0);
  const [syncTotal, setSyncTotal] = useState(0);
  const [tagDone, setTagDone] = useState(0);
  const [tagTotal, setTagTotal] = useState(0);

  const syncPollRef = useRef(null);
  const tagEsRef = useRef(null);
  const lastSyncErrorRef = useRef(null);

  const stopSyncPoll = useCallback(() => {
    clearInterval(syncPollRef.current);
    syncPollRef.current = null;
  }, []);

  const stopTagStream = useCallback(() => {
    tagEsRef.current?.close();
    tagEsRef.current = null;
  }, []);

  // Appends a note about a non-fatal sync warning (e.g. one bad file) to an
  // otherwise-successful toast, instead of hiding it or treating it as a
  // full failure — same "skip and report" spirit as the bulk-delete errors.
  const withSyncWarning = (msg) =>
    lastSyncErrorRef.current ? `${msg} (${lastSyncErrorRef.current})` : msg;

  // ── Phase 2: watch tagging through to completion via SSE ──────────────
  const watchTagging = useCallback((initialData) => {
    setPhase('tagging');
    setTagDone(initialData.done || 0);
    setTagTotal(initialData.total || 0);

    const finish = (data) => {
      stopTagStream();
      setPhase('idle');
      const failed = data.failed || 0;
      const total = data.total || 0;
      const tagged = Math.max(0, (data.done || 0) - failed);
      let msg;
      let type = 'success';
      if (data.status === 'error' && tagged === 0) {
        msg = `Sync finished, but tagging didn't run: ${data.message || 'unknown error'}`;
        type = 'error';
      } else if (tagged > 0) {
        msg = `✓ Synced and tagged ${tagged} photo${tagged === 1 ? '' : 's'}${failed ? ` — ${failed} failed to tag` : ''}.`;
      } else if (total > 0) {
        // Distinct from "nothing was queued": photos WERE queued and every
        // single one failed (e.g. an expired Gemini key) — silently calling
        // that "nothing to tag" would hide a real problem.
        msg = `Sync complete, but tagging failed for all ${total} photo${total === 1 ? '' : 's'}.`;
        type = 'error';
      } else {
        msg = '✓ Sync complete — nothing new to tag.';
      }
      showToast(withSyncWarning(msg), type, 5000);
    };

    // Snapshot already landed on a terminal state (e.g. a near-instant
    // "nothing pending" resolution) — nothing to stream.
    if (initialData.status === 'complete' || initialData.status === 'error') {
      finish(initialData);
      return;
    }

    if (tagEsRef.current) return; // already watching
    const es = new EventSource('/api/tag-progress/stream');
    tagEsRef.current = es;
    es.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data);
        setTagDone(d.done || 0);
        setTagTotal(d.total || 0);
        if (d.status === 'complete' || d.status === 'error') finish(d);
      } catch {
        /* ignore malformed keepalive/frame */
      }
    };
    es.onerror = () => { stopTagStream(); setPhase('idle'); };
  }, [showToast, stopTagStream]);

  // ── Phase 1: poll sync status until done, then hand off to tagging ─────
  const watchSync = useCallback(() => {
    setPhase('syncing');
    if (syncPollRef.current) return;
    syncPollRef.current = setInterval(async () => {
      try {
        const s = await fetch('/api/sync/status').then(r => r.json());
        if (s.yours === false) return; // someone else's sync — not ours to watch
        setSyncProcessed(s.processed || 0);
        setSyncTotal(s.total || 0);
        if (s.errors && s.errors.length) lastSyncErrorRef.current = s.errors[s.errors.length - 1];

        if (!s.in_progress) {
          stopSyncPoll();
          // Backend note (see trigger_tagging() in app.py): the worker
          // resolves "is there anything to tag" SYNCHRONOUSLY and calls
          // trigger_tagging() before flipping sync_state.in_progress false —
          // so the instant this poll sees in_progress:false, _tag_progress
          // is already caught up. No arbitrary delay needed to avoid racing
          // a tagging run that started (and, for a handful of images,
          // could otherwise finish) faster than a fixed wait would catch.
          try {
            const t = await fetch('/api/tag-progress').then(r => r.json());
            if (t.running) {
              watchTagging(t);
            } else {
              setPhase('idle');
              const added = s.new_count || 0;
              const removed = s.removed_count || 0;
              const parts = [];
              if (added) parts.push(`${added} new photo${added === 1 ? '' : 's'}`);
              if (removed) parts.push(`${removed} removed`);
              showToast(
                withSyncWarning(parts.length ? `✓ Synced — ${parts.join(', ')}.` : '✓ Already up to date.'),
                'success', 4000
              );
            }
          } catch {
            setPhase('idle');
          } finally {
            lastSyncErrorRef.current = null;
          }
        }
      } catch {
        /* transient — next tick retries */
      }
    }, 800);
  }, [showToast, stopSyncPoll, watchTagging]);

  const startSync = useCallback(async () => {
    lastSyncErrorRef.current = null;
    setSyncProcessed(0); setSyncTotal(0);
    try {
      const res = await fetch('/api/sync/start', { method: 'POST' });
      const data = await res.json();
      if (!res.ok || !data.success) {
        showToast(data.error || 'Sync failed to start.', 'error', 5000);
        return;
      }
      watchSync();
    } catch {
      showToast('Could not reach the server.', 'error', 5000);
    }
  }, [watchSync, showToast]);

  // Resume watching if a sync or tag job was already running before this
  // page loaded (e.g. a refresh mid-sync) — same resilience the old
  // dedicated Sync page had.
  useEffect(() => {
    if (!isAdmin) return;
    (async () => {
      try {
        const s = await fetch('/api/sync/status').then(r => r.json());
        if (s.in_progress && s.yours !== false) {
          setSyncProcessed(s.processed || 0);
          setSyncTotal(s.total || 0);
          watchSync();
          return;
        }
      } catch { /* ignore */ }
      try {
        const t = await fetch('/api/tag-progress').then(r => r.json());
        if (t.running) watchTagging(t);
      } catch { /* ignore */ }
    })();
    return () => { stopSyncPoll(); stopTagStream(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin]);

  return (
    <SyncContext.Provider value={{
      phase,
      syncing: phase === 'syncing',
      tagging: phase === 'tagging',
      running: phase !== 'idle',
      syncProcessed, syncTotal,
      tagDone, tagTotal,
      startSync,
    }}>
      {children}
    </SyncContext.Provider>
  );
}

export function useSync() {
  return useContext(SyncContext);
}
