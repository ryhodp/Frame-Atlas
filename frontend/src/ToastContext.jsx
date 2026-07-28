import { createContext, useContext, useState, useCallback } from 'react';

const ToastContext = createContext(null);

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const showToast = useCallback((message, type = 'info', duration = 4000) => {
    const id = Math.random().toString(36).slice(2);
    setToasts(prev => [...prev, { id, message, type }]);
    if (duration) {
      setTimeout(() => {
        setToasts(prev => prev.filter(t => t.id !== id));
      }, duration);
    }
    return id;
  }, []);

  return (
    <ToastContext.Provider value={{ showToast, toasts }}>
      {children}
      <ToastContainer toasts={toasts} />
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within ToastProvider');
  }
  return context.showToast;
}

function ToastContainer({ toasts }) {
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
              background: 'rgba(184,206,161,0.18)',
              border: '1px solid rgba(184,206,161,0.4)',
              color: '#b8cea1',
            }),
            ...(toast.type === 'error' && {
              background: 'rgba(207,113,82,0.18)',
              border: '1px solid rgba(207,113,82,0.4)',
              color: '#cf7152',
            }),
            ...(toast.type === 'info' && {
              background: 'rgba(217,164,65,0.18)',
              border: '1px solid rgba(217,164,65,0.4)',
              color: '#d9a441',
            }),
          }}
        >
          {toast.message}
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
