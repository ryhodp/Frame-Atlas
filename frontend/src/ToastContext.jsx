import { createContext, useContext, useState, useCallback } from 'react';
import { danger, primary, tertiary, white, withAlpha } from './theme';

const ToastContext = createContext(null);

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const dismissToast = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  // `action` (optional) is {label, onClick} and renders as a button inside the
  // toast — for a background job whose result needs a decision rather than just
  // an FYI (e.g. upload finding duplicates). The toast dismisses itself once the
  // action is clicked, so the handler doesn't have to.
  const showToast = useCallback((message, type = 'info', duration = 4000, action = null) => {
    const id = Math.random().toString(36).slice(2);
    setToasts(prev => [...prev, { id, message, type, action }]);
    if (duration) {
      setTimeout(() => dismissToast(id), duration);
    }
    return id;
  }, [dismissToast]);

  return (
    <ToastContext.Provider value={{ showToast, dismissToast, toasts }}>
      {children}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </ToastContext.Provider>
  );
}

// Duration 0 means "stays until something explicitly dismisses it" (e.g. a
// long-running background job) — pass the returned id to dismissToast once
// that job resolves, otherwise it never goes away.
export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within ToastProvider');
  }
  return { showToast: context.showToast, dismissToast: context.dismissToast };
}

function ToastContainer({ toasts, onDismiss }) {
  return (
    <div style={{
      position: 'fixed',
      bottom: '20px',
      right: '20px',
      zIndex: 2000,
      display: 'flex',
      flexDirection: 'column',
      gap: '8px',
      maxWidth: '360px',
      pointerEvents: 'none',
    }}>
      {toasts.map(toast => (
        <div
          key={toast.id}
          style={{
            padding: '12px 16px',
            borderRadius: '8px',
            fontSize: '13px',
            fontWeight: 500,
            animation: 'slideIn 0.2s ease',
            pointerEvents: 'auto',
            ...(toast.type === 'success' && {
              background: withAlpha(tertiary,0.18),
              border: `1px solid ${withAlpha(tertiary,0.4)}`,
              color: tertiary,
            }),
            ...(toast.type === 'error' && {
              background: withAlpha(danger,0.18),
              border: `1px solid ${withAlpha(danger,0.4)}`,
              color: danger,
            }),
            ...(toast.type === 'info' && {
              background: withAlpha(primary,0.18),
              border: `1px solid ${withAlpha(primary,0.4)}`,
              color: primary,
            }),
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <span style={{ flex: 1, minWidth: 0 }}>{toast.message}</span>
            {toast.action && (
              <button
                onClick={() => { onDismiss(toast.id); toast.action.onClick?.(); }}
                style={{
                  flexShrink: 0,
                  background: withAlpha(white, 0.1),
                  border: `1px solid ${withAlpha(white, 0.25)}`,
                  color: 'inherit',
                  borderRadius: '6px',
                  padding: '5px 10px',
                  fontSize: '11.5px',
                  fontWeight: 600,
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
              >
                {toast.action.label}
              </button>
            )}
          </div>
        </div>
      ))}
      <style>{`
        @keyframes slideIn {
          from {
            opacity: 0;
            transform: translateX(20px);
          }
          to {
            opacity: 1;
            transform: translateX(0);
          }
        }
      `}</style>
    </div>
  );
}
