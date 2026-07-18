import { useEffect, useState, useCallback } from 'react';

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

export function useOfflineCache() {
  const [db, setDb] = useState(null);
  const [error, setError] = useState(null);

  // Initialize DB on mount
  useEffect(() => {
    getDB()
      .then(setDb)
      .catch(err => {
        console.error('IndexedDB init failed:', err);
        setError(err.message);
      });
  }, []);

  // Cache a deck locally
  const cacheDeck = useCallback(async (deckData) => {
    if (!db || !deckData) return false;
    try {
      const store = db.transaction([DECKS_STORE], 'readwrite').objectStore(DECKS_STORE);
      const entry = {
        deck_id: deckData.id,
        data: deckData,
        cached_at: new Date().toISOString(),
        updated_at: deckData.updated_at,
      };
      store.put(entry);
      return true;
    } catch (e) {
      console.error('Cache deck failed:', e);
      return false;
    }
  }, [db]);

  // Get cached deck
  const getCachedDeck = useCallback(async (deckId) => {
    if (!db) return null;
    return new Promise((resolve, reject) => {
      const store = db.transaction([DECKS_STORE], 'readonly').objectStore(DECKS_STORE);
      const request = store.get(deckId);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }, [db]);

  // List all cached decks
  const getCachedDecks = useCallback(async () => {
    if (!db) return [];
    return new Promise((resolve, reject) => {
      const store = db.transaction([DECKS_STORE], 'readonly').objectStore(DECKS_STORE);
      const request = store.getAll();
      request.onsuccess = () => resolve(request.result || []);
      request.onerror = () => reject(request.error);
    });
  }, [db]);

  // Clear all cache
  const clearCache = useCallback(async () => {
    if (!db) return false;
    try {
      const store = db.transaction([DECKS_STORE], 'readwrite').objectStore(DECKS_STORE);
      store.clear();
      return true;
    } catch (e) {
      console.error('Clear cache failed:', e);
      return false;
    }
  }, [db]);

  // Remove specific deck from cache
  const removeCachedDeck = useCallback(async (deckId) => {
    if (!db) return false;
    try {
      const store = db.transaction([DECKS_STORE], 'readwrite').objectStore(DECKS_STORE);
      store.delete(deckId);
      return true;
    } catch (e) {
      console.error('Remove cached deck failed:', e);
      return false;
    }
  }, [db]);

  // Check if deck has been updated online since caching
  const hasRemoteUpdates = (cachedEntry, remoteUpdatedAt) => {
    if (!cachedEntry) return false;
    if (!remoteUpdatedAt) return false;

    const cachedTime = new Date(cachedEntry.updated_at).getTime();
    const remoteTime = new Date(remoteUpdatedAt).getTime();
    return remoteTime > cachedTime;
  };

  return {
    ready: !!db,
    error,
    cacheDeck,
    getCachedDeck,
    getCachedDecks,
    clearCache,
    removeCachedDeck,
    hasRemoteUpdates,
  };
}
