import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)

// Caches the app shell so Frame Atlas still opens with no connection — the
// decks themselves come from IndexedDB. Registered after load so it never
// competes with the first paint. Needs HTTPS (Railway) or localhost.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('/sw.js')
      .catch(err => console.warn('Service worker registration failed:', err))
  })
}
