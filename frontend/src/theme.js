// Shared background shade for full page/screen canvases — the app shell,
// auth screens, and full-screen editors (Storyboard, Crop). Lighter than pure
// near-black (V52/V55) so dark or letterboxed photos don't disappear into it.
// NOT for floating cards, modal panels, or inputs — those keep their own
// near-black surface color for contrast against this background.
export const PAGE_BG = '#1c1e22';
