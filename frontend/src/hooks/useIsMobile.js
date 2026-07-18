import { useEffect, useState } from 'react';

export const MOBILE_BREAKPOINT = 768;

// Below this width the sidebar becomes a hamburger drawer instead of a
// fixed column — matches the breakpoint used across Sidebar/MobileHeader/
// App.jsx so the layout switches over consistently everywhere.
export function useIsMobile() {
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < MOBILE_BREAKPOINT);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < MOBILE_BREAKPOINT);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  return isMobile;
}
