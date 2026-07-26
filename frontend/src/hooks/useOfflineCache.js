import { useEffect, useMemo, useRef, useState, useCallback } from 'react';

const DB_NAME = 'FrameAtlasCache';
const DECKS_STORE = 'decks';
const VERSION = 1;

// Initialize IndexedDB
const getDB = () => {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, VERSION);

    request.onerror = () => reject(request.error);
    request.onsuccess = () => resolve(request.result);

    request.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(DECKS_STORE)) {
        // Store cached decks: deck_id -> {deckData, cached_at, updated_at}
        db.createObjectStore(DECKS_STORE, { keyPath: 'deck_id' });
      }
    };
  });
};

// Wait for the write transaction to actually commit. store.put() only *queues*
// the write — returning before oncomplete means a deck can silently fail to
// cache (exactly what you don't want right before going offline).
const commit = (tx) =>
  new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve(true);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });

// Pure — takes no state, so it lives outside the hook. Defining it inside
// gave it a new identity on every render, which is what made every consumer's
// useCallback churn and spin the deck page into an endless fetch loop.
export function hasRemoteUpdates(cachedEntry, remoteUpdatedAt) {
  if (!cachedEntry || !remoteUpdatedAt || !cachedEntry.updated_at) return false;

  const cachedTime = new Date(cachedEntry.updated_at).getTime();
  const remoteTime = new Date(remoteUpdatedAt).getTime();
  if (Number.isNaN(cachedTime) || Number.isNaN(remoteTime)) return false;

  return remoteTime > cachedTime;
}

export function useOfflineCache() {
  // The db handle lives in a ref, not state, so every callback below can have
  // an empty dependency array and keep a stable identity for the life of the
  // component. `ready` is the only thing consumers re-render on.
  const dbRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    getDB()
      .then((db) => {
        if (cancelled) { db.close(); return; }
        dbRef.current = db;
        setReady(true);
      })
      .catch((err) => {
        console.error('IndexedDB init failed:', err);
        if (!cancelled) setError(err.message);
      });
    return () => { cancelled = true; };
  }, []);

  // Cache a deck locally
  const cacheDeck = useCallback(async (deckData) => {
    const db = dbRef.current;
    if (!db || !deckData || deckData.id == null) return false;
    try {
      const tx = db.transaction([DECKS_STORE], 'readwrite');
      tx.objectStore(DECKS_STORE).put({
        deck_id: Number(deckData.id),
        data: deckData,
        cached_at: new Date().toISOString(),
        updated_at: deckData.updated_at,
      });
      return await commit(tx);
    } catch (e) {
      console.error('Cache deck failed:', e);
      return false;
    }
  }, []);

  // Get one cached deck. Keys are stored as numbers, so a string id from
  // useParams would silently miss — normalize before looking up.
  const getCachedDeck = useCallback(async (deckId) => {
    const db = dbRef.current;
    if (!db || deckId == null) return null;
    const key = Number(deckId);
    if (Number.isNaN(key)) return null;
    return new Promise((resolve) => {
      try {
        const request = db
          .transaction([DECKS_STORE], 'readonly')
          .objectStore(DECKS_STORE)
          .get(key);
        request.onsuccess = () => resolve(request.result || null);
        request.onerror = () => resolve(null);
      } catch (e) {
        console.error('Read cached deck failed:', e);
        resolve(null);
      }
    });
  }, []);

  // List all cached decks
  const getCachedDecks = useCallback(async () => {
    const db = dbRef.current;
    if (!db) return [];
    return new Promise((resolve) => {
      try {
        const request = db
          .transaction([DECKS_STORE], 'readonly')
          .objectStore(DECKS_STORE)
          .getAll();
        request.onsuccess = () => resolve(request.result || []);
        request.onerror = () => resolve([]);
      } catch (e) {
        console.error('List cached decks failed:', e);
        resolve([]);
      }
    });
  }, []);

  // Clear all cache
  const clearCache = useCallback(async () => {
    const db = dbRef.current;
    if (!db) return false;
    try {
      const tx = db.transaction([DECKS_STORE], 'readwrite');
      tx.objectStore(DECKS_STORE).clear();
      return await commit(tx);
    } catch (e) {
      console.error('Clear cache failed:', e);
      return false;
    }
  }, []);

  // Remove specific deck from cache
  const removeCachedDeck = useCallback(async (deckId) => {
    const db = dbRef.current;
    if (!db || deckId == null) return false;
    try {
      const tx = db.transaction([DECKS_STORE], 'readwrite');
      tx.objectStore(DECKS_STORE).delete(Number(deckId));
      return await commit(tx);
    } catch (e) {
      console.error('Remove cached deck failed:', e);
      return false;
    }
  }, []);

  // One stable object. Without useMemo this is a fresh literal every render,
  // which defeats every useCallback/useEffect that depends on it downstream.
  return useMemo(
    () => ({
      ready,
      error,
      cacheDeck,
      getCachedDeck,
      getCachedDecks,
      clearCache,
      removeCachedDeck,
      hasRemoteUpdates,
    }),
    [ready, error, cacheDeck, getCachedDeck, getCachedDecks, clearCache, removeCachedDeck]
  );
}
