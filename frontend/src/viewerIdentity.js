/**
 * V42 — anonymous viewer identity for the public share page.
 *
 * Two SEPARATE things live in localStorage, on purpose:
 *  - a random token the browser generates for itself, invisible to the
 *    viewer, which is what the server actually uses to tell "the same
 *    browser came back" (toggling a pick, not double-counting a retry)
 *  - the display name the viewer typed, which can change at any time
 *    (they retype it, a different person borrows the laptop) without
 *    losing or forking their pick history, because the token never moves
 *
 * Not a login. There is no server-side account behind either value — the
 * token only ever proves "this is the same browser," never who someone is.
 */

const TOKEN_KEY = 'fa.viewer.token';
const NAME_KEY = 'fa.viewer.name';

export function getViewerToken() {
  try {
    let token = localStorage.getItem(TOKEN_KEY);
    if (!token) {
      token = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
      localStorage.setItem(TOKEN_KEY, token);
    }
    return token;
  } catch {
    // Private browsing / storage disabled: fall back to a per-load token.
    // Picks won't persist across a refresh, but the page still works.
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}

export function getViewerName() {
  try {
    return localStorage.getItem(NAME_KEY) || '';
  } catch {
    return '';
  }
}

export function setViewerName(name) {
  try {
    localStorage.setItem(NAME_KEY, name);
  } catch {
    // Nothing to do — the name just won't be remembered next visit.
  }
}
