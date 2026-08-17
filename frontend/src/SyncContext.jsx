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
//
// V49: this also absorbed UploadProgressBadge (V22), which was a SECOND
// component subscribing to the SAME /api/tag-progress/stream to show the
// same job in a different corner of Home. Two indicators for one job is
// confusing on its own, but the badge also had the bug described on
// `sawTaggingRunRef` below and sat on screen showing an empty gear forever.
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

  // True once we've actually WATCHED a tagging run be in progress.
  //
  // This guard is the whole reason the old badge misbehaved. The server keeps
  // its last job's outcome in memory and replays it to every new stream
  // subscriber, so a page load long after a job ended still receives
  // {running: false, status: 'complete'}. Treating that as "a job just
  // finished" means a stale toast on every refresh — and it's exactly what
  // pinned UploadProgressBadge open on a gear icon with no label. We only
  // report an ending we saw the beginning of.
  const sawTaggingRunRef = useRef(false);

  // Read inside the stream handler, which closes over its first render.
  // A ref, not the phase state, so the handler always sees the live value.
  const syncingRef = useRef(false);
  useEffect(() => { syncingRef.current = phase === 'syncing'; }, [phase]);

  const stopSyncPoll = useCallback(() => {
    clearInterval(syncPollRef.current);
    syncPollRef.current = null;
  }, []);

  // Appends a note about a non-fatal sync warning (e.g. one bad file) to an
  // otherwise-successful toast, instead of hiding it or treating it as a
  // full failure — same "skip and report" spirit as the bulk-delete errors.
  const withSyncWarning = (msg) =>
    lastSyncErrorRef.current ? `${msg} (${lastSyncErrorRef.current})` : msg;

  // Reports a finished tagging run. Split out because it's reached from two
  // directions: the live stream below, and the sync handoff for a run that
  // was already over by the time sync's own poll noticed.
  const finishTagging = useCallback((data) => {
    setPhase('idle');
    const failed = data.failed || 0;
    const total = data.total || 0;
    const tagged = Math.max(0, (data.done || 0) - failed);
    let msg;
    let type = 'success';
    if (data.status === 'error' && tagged === 0) {
      msg = `Tagging didn't run: ${data.message || 'unknown error'}`;
      type = 'error';
    } else if (tagged > 0) {
      msg = `✓ Tagged ${tagged} photo${tagged === 1 ? '' : 's'}${failed ? ` — ${failed} failed` : ''}.`;
    } else if (total > 0) {
      // Distinct from "nothing was queued": photos WERE queued and every
      // single one failed (e.g. an expired Gemini key) — silently calling
      // that "nothing to tag" would hide a real problem.
      msg = `Tagging failed for all ${total} photo${total === 1 ? '' : 's'}.`;
      type = 'error';
    } else {
      msg = '✓ Nothing new to tag.';
    }
    showToast(withSyncWarning(msg), type, 5000);
    lastSyncErrorRef.current = null;
  }, [showToast]);

  // ── One persistent tagging stream for the whole session ────────────────
  //
  // Deliberately NOT opened only around a sync: tagging also starts from a
  // drag-and-drop upload or a browser clip, at any moment, with no sync
  // involved. Watching continuously is what lets those show progress at all
  // — it's the one job UploadProgressBadge did that this context didn't.
  //
  // Admin-only because /api/tag-progress/stream is @admin_required; a friend
  // subscribing would just collect 403s (the old badge rendered for everyone
  // and did exactly that). Friends have /api/tag-progress/mine, which is a
  // separate feature, not wired up here.
  useEffect(() => {
    if (!isAdmin) return undefined;

    const es = new EventSource('/api/tag-progress/stream');
    tagEsRef.current = es;

    es.onmessage = (e) => {
      let d;
      try {
        d = JSON.parse(e.data);
      } catch {
        return; // malformed keepalive frame
      }
      setTagDone(d.done || 0);
      setTagTotal(d.total || 0);

      if (d.running) {
        sawTaggingRunRef.current = true;
        // Don't stomp the syncing phase — sync's own progress is the more
        // useful thing to show while Drive is still being read, and the
        // handoff below switches us over when it's done.
        if (!syncingRef.current) setPhase('tagging');
      } else if (d.status === 'complete' || d.status === 'error') {
        if (sawTaggingRunRef.current) {
          sawTaggingRunRef.current = false;
          finishTagging(d);
        }
      }
    };

    // EventSource reconnects on its own; closing here would end the watch
    // permanently on one dropped connection.
    es.onerror = () => {};

    return () => {
      es.close();
      tagEsRef.current = null;
    };
  }, [isAdmin, finishTagging]);

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
          // a tagging run that started faster than a fixed wait would catch.
          try {
            const t = await fetch('/api/tag-progress').then(r => r.json());
            if (t.running) {
              // The persistent stream above drives it from here.
              sawTaggingRunRef.current = true;
              setPhase('tagging');
              return;
            }
            if ((t.total || 0) > 0 && (t.status === 'complete' || t.status === 'error')) {
              // A short batch can finish before this poll comes back around.
              // Report the tagging outcome rather than the sync summary,
              // otherwise a run where every photo failed (dead Gemini key)
              // would be announced as a plain success.
              sawTaggingRunRef.current = false;
              finishTagging(t);
              return;
            }
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
            lastSyncErrorRef.current = null;
          } catch {
            setPhase('idle');
            lastSyncErrorRef.current = null;
          }
        }
      } catch {
        /* transient — next tick retries */
      }
    }, 800);
  }, [showToast, stopSyncPoll, finishTagging]);

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

  // Resume watching if a SYNC was already running before this page loaded
  // (e.g. a refresh mid-sync). Tagging needs no equivalent — the persistent
  // stream above picks an in-flight run up on its own.
  useEffect(() => {
    if (!isAdmin) return undefined;
    (async () => {
      try {
        const s = await fetch('/api/sync/status').then(r => r.json());
        if (s.in_progress && s.yours !== false) {
          setSyncProcessed(s.processed || 0);
          setSyncTotal(s.total || 0);
          watchSync();
        }
      } catch { /* ignore */ }
    })();
    return () => { stopSyncPoll(); };
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
