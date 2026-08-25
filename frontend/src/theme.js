// Frame Atlas color tokens — mirrors DESIGN.md's Color System section.
// Phase 1 of the V56 token migration: this file is the single source of
// truth for color values. Nothing outside PAGE_BG (below) consumes these
// yet — call sites still hand-type hex — later phases migrate one file/area
// at a time. See DESIGN.md for what each token is meant for.
//
// Naming follows DESIGN.md's existing scheme (surface/on-surface/outline/
// primary/secondary/tertiary/error, camelCase instead of kebab-case for
// valid JS identifiers). The "on-surface*", "warning"/"danger", "success",
// and "accent*" tokens below were undocumented-but-clearly-deliberate colors
// found already in heavy use across the app (frequency counts in DESIGN.md);
// added here as real tokens rather than left as scattered hex.

// ── Surfaces (backgrounds and panels) ───────────────────────────────────────
// Full page/screen canvases — the app shell, auth screens, and full-screen
// editors (Storyboard, Crop). Lighter than pure near-black (V52/V55) so dark
// or letterboxed photos don't disappear into it.
export const PAGE_BG = '#1c1e22';
export const surface = PAGE_BG;
export const surfaceDim = PAGE_BG;
export const surfaceBright = '#37393e';
export const surfaceContainerHighest = '#37393e';
export const surfaceContainerHigh = '#2a2c31';
export const surfaceContainerLow = '#1a1c20';
// Two near-black surfaces that read as "the same color" at a glance but are
// used in different roles — kept distinct rather than merged (V56 scoping
// decision: treat as intentional, zero visual risk from collapsing them).
export const surfaceContainerLowest = '#0a0a0b';       // modals, full-screen editors (CropModal)
export const surfaceContainerLowestAlt = '#111317';    // input field backgrounds
export const sidebarSurface = '#111114';                // sidebar / mobile nav drawer
// V58 (Phase 3): more near-black/near-gray surfaces found in the components.
// Same treatment as the three above — named, not merged, so the migration
// stays visually lossless. Several sit within a few RGB steps of an existing
// token and are consolidation candidates for a later, deliberate design pass.
export const surfaceContainerDark = '#18181b';         // dropdowns/popovers (ImageDetail, CropModal)
export const surfaceContainerWarmDark = '#141318';     // warm near-black thumbnail/preview backdrop
export const surfaceContainerLowAlt = '#1b1d21';       // AddPhotosModal panel (vs. surfaceContainerLow #1a1c20)
export const surfaceContainerMuted = '#3d3d42';        // progress-bar track (TagRemovalPreview)

// V59 (Phase 4): from the mid-size pages.
export const surfaceContainerInput = '#0f1013';        // AccountPage's own input backgrounds
// V62 (Phase 5): from the five large files.
export const surfaceContainerCrop = '#111113';         // CropModal side panel
export const surfaceContainerHover = '#222226';        // autocomplete row hover/highlight (Home)
export const surfaceContainerDivider = '#4a4a52';      // CropModal workspace panes + the 1px gutter between them

// Border shades subtler than outlineVariant (#44474f)
export const outlineSubtle = '#33353b';                // modal/panel borders
export const outlineDim = '#2c2f35';                   // faint dividers (StoryboardView)
export const outlineMuted = '#3a3d44';                 // SharePage card/input borders
export const outlineFaint = '#35373d';                 // DeckDetail card borders (V62)

// ── Text ─────────────────────────────────────────────────────────────────
export const onSurface = '#e2e2e6';
export const onSurfaceVariant = '#c4c6d0';
// Warmer text color used on the lighter photo-grid pages (Home, Favorites/
// Recent, Decks) — distinct from onSurface's cooler tone used on cards/panels.
export const onSurfaceWarm = '#efeadd';
export const onSurfaceMuted = '#9c988d';   // subtitles, secondary/supporting text
export const onSurfaceFaint = '#65625a';   // least-prominent text and icons
// V68: onSurfaceWarmDim used to be 3 near-identical warm grays (#c9c6bd,
// #c9c5ba, #c8c3b8 — within 1-3 RGB steps of each other, invisible to the
// eye) kept apart during the migration so it stayed lossless. Collapsed here
// into one token, valued at #c9c5ba (the most-used of the three, 5 call
// sites vs. 2 and 2) — dimmed warm secondary text: presentation captions,
// feedback comment bodies, share-page viewer name/pick label, analytics
// table cells. The two merged names are gone; nothing imports them anymore.
export const onSurfaceWarmDim = '#c9c5ba';
export const onSurfaceDim = '#6b6d75';         // dim cool-gray text
export const onSurfaceFaintCool = '#4e5058';   // faintest cool-gray text (PresentationMode filename)
export const onSurfaceWarmFaint = '#8e7f77';   // disabled warm text (SettingsPage)

// ── Borders ──────────────────────────────────────────────────────────────
export const outline = '#8e9099';
export const outlineVariant = '#44474f';

// ── Primary — Warm Gold (the accent color) ──────────────────────────────
export const primary = '#d9a441';
export const onPrimary = '#3d2f00';
export const primaryContainer = '#594400';
export const onPrimaryContainer = '#ffdf9d';
export const primaryDim = '#c9a253';   // dimmer gold — links, spinners

// ── Secondary — Warm Taupe ───────────────────────────────────────────────
export const secondary = '#d1c5b4';
export const onSecondary = '#362f24';
export const secondaryContainer = '#4d4639';
export const onSecondaryContainer = '#eee1cf';

// ── Tertiary — Muted Sage ────────────────────────────────────────────────
export const tertiary = '#b8cea1';
export const onTertiary = '#243516';
export const tertiaryContainer = '#3a4c2b';
export const onTertiaryContainer = '#d4eabb';

// ── Error ────────────────────────────────────────────────────────────────
export const error = '#ffb4ab';
export const onError = '#690005';
export const errorContainer = '#93000a';
export const onErrorContainer = '#ffdad6';

// ── Additional semantic accents (undocumented until V56, in real use) ──────
export const warning = '#dcbd76';   // warning/highlight gold, distinct from primary
export const danger = '#cf7152';    // destructive text/icons, distinct from `error`
export const success = '#7fb87f';
export const successBright = '#6ee7b7';
// Natural-language search chips (violet) — see CollectionPage/Home "nlChips"
export const accentViolet = '#8b7cf6';
export const accentVioletLight = '#a99bf7';
export const accentVioletLighter = '#c9a8f2';
// Composition-guide overlay accent (blue)
export const accentBlue = '#7fb3d9';
export const accentTeal = '#7dd3c8';
// V59 (Phase 4): tag-frequency heatmap text, hot vs. cool cell (AnalyticsPage)
export const heatmapTextHot = '#f4e8cd';
export const heatmapTextCool = '#d6c9a8';
// V68: dangerWarmAlt (#e07a55) merged in here — one hex digit apart from
// this value, same drift-not-intent case as onSurfaceWarmDim above. Used for
// inline warning text (SharePage) and the CropModal destructive ghost button.
export const dangerWarm = '#e07a5f';
export const accentBlueLight = '#8fc3d8';      // aspect-ratio suggestions (Home)
export const accentOrange = '#e0935a';         // note/film suggestions (Home)
export const onSurfaceCool = '#aab2c0';        // offline-banner text (DeckDetail)
export const accentBlueMuted = '#7fa9d9';       // Recent view accent (CollectionPage)

// ── Color-search swatch picker ──────────────────────────────────────────
// A deliberately multi-hue palette — NOT app chrome, kept separate from the
// tokens above. Moved here from Home.jsx's PRESET_SWATCHES (same 12 values,
// unchanged) so it has one home instead of living inline in one component.
export const SWATCH_COLORS = [
  '#D9A441', '#E08840', '#B33A3A', '#C75B8B',
  '#7B5BC7', '#3A5BB3', '#2E8B8B', '#6FA3B8',
  '#4E7A3A', '#8A7A3A', '#E8DFC8', '#1A1A1E'
];
