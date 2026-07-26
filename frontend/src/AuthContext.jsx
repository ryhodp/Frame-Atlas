import { createContext, useContext, useEffect, useState, useCallback } from 'react';

const AuthContext = createContext(null);

// Who was signed in last time we could actually reach the server. Offline, the
// /api/auth/me check can't succeed, and without this the app would treat a
// dropped connection as "logged out" and show the login screen — locking you
// out of the decks already cached on the device.
//
// This is a UI hint only. Every endpoint still checks the real session
// server-side, so a remembered user with no valid cookie can't read anything
// new; all they can see is what this device already downloaded.
const LAST_USER_KEY = 'fa_last_user';

const readLastUser = () => {
  try {
    const raw = localStorage.getItem(LAST_USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
};

const writeLastUser = (user) => {
  try {
    if (user) localStorage.setItem(LAST_USER_KEY, JSON.stringify(user));
    else localStorage.removeItem(LAST_USER_KEY);
  } catch {
    /* private browsing / storage full — offline resume just won't work */
  }
};

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);       // {id, username, email, role} or null
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false); // reachable server, or resumed from cache?

  const refresh = useCallback(() => {
    setLoading(true);
    return fetch('/api/auth/me')
      .then(res => res.json())
      .then(data => {
        // The server answered, so its word is final either way.
        const next = data.logged_in ? data.user : null;
        setUser(next);
        setOffline(false);
        writeLastUser(next);
      })
      .catch(() => {
        // Couldn't reach the server at all — distinct from being told
        // "you're logged out". Resume the last known session read-only.
        const remembered = readLastUser();
        setUser(remembered);
        setOffline(!!remembered);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Reconnecting should promote an offline session back to a verified one.
  useEffect(() => {
    if (!offline) return;
    const onOnline = () => refresh();
    window.addEventListener('online', onOnline);
    return () => window.removeEventListener('online', onOnline);
  }, [offline, refresh]);

  const logout = async () => {
    writeLastUser(null);
    setOffline(false);
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch {
      /* offline: the local session is cleared regardless */
    }
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{
      user,
      loading,
      offline,
      refresh,
      logout,
      isAdmin: user?.role === 'admin',
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
