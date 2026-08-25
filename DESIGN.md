# DESIGN.md — Frame Atlas
*Visual design system. Reference this before writing any UI code.*

---

## Design Philosophy

Frame Atlas is a **premium visual reference tool for cinematographers**. The aesthetic is:

> "Cinematic, quiet, and image-first."

Inspired by Arc Browser, Obsidian, and Apple Photos. Every UI decision should make the images the hero — chrome (buttons, labels, borders) should recede. The interface should feel like a high-end pro app, not a generic web dashboard.

---

## Color System

These are the exact color values. Use them by name, not by hex.

**Status (V69, Aug 2026):** this table is the source of truth, and — as of
V67 — it's no longer aspirational: every color in `frontend/src` is imported
by name from `frontend/src/theme.js`, which this table mirrors exactly. Zero
raw hex OR raw `rgba(...)` triple remains anywhere in the frontend (verified
by a repo-wide grep, not spot-checked) — V69 extended the migration to
translucency: `theme.js` exports a `withAlpha(hex, alpha)` helper, and every
`rgba(R,G,B,A)` literal that used to hand-copy one of these tokens' RGB
values (~95% of the ~380 that existed) now calls `withAlpha(token, alpha)`
instead, so a color and its hover/border/overlay variants can never drift
apart from each other again — which is exactly what had already happened
once (see `danger-warm` below). The remaining 5% were repeated tinted-chip
colors that had genuinely never been given a flat-hex token; they're in the
tables below now too (`offline-accent`, `accent-similar`, `overlay-violet`,
`accent-film`, `presentation-control-bg`), alongside plain `white`/`black`
for the many borders/backdrops that were just translucent white or black.
The migration itself ran in 6 phases (V57–V67), smallest/safest files
first; `theme.js` carries a version note next to each token group recording
which phase added it. V68 collapsed 5 near-duplicate colors kept apart
during the migration to stay lossless (see `on-surface-warm-dim` and
`danger-warm` below) — the first real "deliberate design pass" cleanup
mentioned as future work in earlier versions of this doc.

### Surfaces (backgrounds and panels)
| Name | Hex | Use it for |
|---|---|---|
| `surface` / `surface-dim` | `#1c1e22` | Full page/screen canvases — the app shell, auth screens, Home, Favorites/Recent, Decks, image detail panel, Storyboard editor. `theme.js`'s `PAGE_BG` |
| `surface-bright` / `surface-container-highest` | `#37393e` | Topmost floating elements, highlighted surface areas |
| `surface-container-high` | `#2a2c31` | Elevated panels, dropdowns |
| `surface-container-low` | `#1a1c20` | Cards, panels resting on surface |
| `surface-container-lowest` | `#0a0a0b` | Modals (Duplicate Review, Upload) and the Crop tool's full-screen editor — near-black on purpose for maximum contrast while judging an image closely |
| `surface-container-lowest-alt` | `#111317` | Input field backgrounds specifically — visually close to `surface-container-lowest` but a distinct, consistently-used value; kept as its own token rather than merged |
| `sidebar-surface` | `#111114` | The left sidebar / mobile nav drawer |
| `surface-container-dark` | `#18181b` | Dropdowns/popovers (ImageDetail, CropModal) |
| `surface-container-warm-dark` | `#141318` | Warm near-black thumbnail/preview backdrop |
| `surface-container-low-alt` | `#1b1d21` | AddPhotosModal panel |
| `surface-container-muted` | `#3d3d42` | Progress-bar track (TagRemovalPreview) |
| `surface-container-input` | `#0f1013` | AccountPage's own input backgrounds |
| `surface-container-crop` | `#111113` | CropModal side panel |
| `surface-container-hover` | `#222226` | Autocomplete row hover/highlight (Home) |
| `surface-container-divider` | `#4a4a52` | CropModal workspace panes + the 1px gutter between them |

### Text
| Name | Hex | Use it for |
|---|---|---|
| `on-surface` | `#e2e2e6` | Primary body text on cards/panels |
| `on-surface-warm` | `#efeadd` | Primary body text on the lighter photo-grid pages (Home, Favorites/Recent, Decks) — warmer than `on-surface` |
| `on-surface-variant` | `#c4c6d0` | Secondary/supporting text, labels |
| `on-surface-muted` | `#9c988d` | Subtitles, secondary/supporting text |
| `on-surface-faint` | `#65625a` | Least-prominent text and icons (footer hints, quiet labels) |
| `on-surface-warm-dim` | `#c9c5ba` | Dimmed warm secondary text: presentation captions, feedback comment bodies, share-page viewer name/pick label, analytics table cells. **V68:** was 3 separate near-identical tokens (`#c9c6bd`/`#c9c5ba`/`#c8c3b8`, invisible to the eye apart) kept distinct mid-migration to stay lossless — collapsed into this one value, the most-used of the three |
| `on-surface-dim` | `#6b6d75` | Dim cool-gray text |
| `on-surface-faint-cool` | `#4e5058` | Faintest cool-gray text (PresentationMode filename) |
| `on-surface-warm-faint` | `#8e7f77` | Disabled warm text (SettingsPage) |

### Borders
| Name | Hex | Use it for |
|---|---|---|
| `outline` | `#8e9099` | Visible borders (input fields, dividers) |
| `outline-variant` | `#44474f` | Subtle borders (panel edges, separators) |
| `outline-subtle` | `#33353b` | Modal/panel borders |
| `outline-dim` | `#2c2f35` | Faint dividers (StoryboardView) |
| `outline-muted` | `#3a3d44` | SharePage card/input borders |
| `outline-faint` | `#35373d` | DeckDetail card borders |

### Primary — Warm Gold (the accent color)
| Name | Hex | Use it for |
|---|---|---|
| `primary` | `#d9a441` | Buttons, active states, highlights, selected chips |
| `on-primary` | `#3d2f00` | Text/icons sitting ON a gold background |
| `primary-container` | `#594400` | Gold tinted backgrounds (hover states, badges) |
| `on-primary-container` | `#ffdf9d` | Text sitting inside a gold container |
| `primary-dim` | `#c9a253` | A dimmer gold — links, loading spinners |

### Secondary — Warm Taupe
| Name | Hex | Use it for |
|---|---|---|
| `secondary` | `#d1c5b4` | Secondary actions, passive chips |
| `on-secondary` | `#362f24` | Text on secondary backgrounds |
| `secondary-container` | `#4d4639` | Secondary tinted containers |
| `on-secondary-container` | `#eee1cf` | Text in secondary containers |

### Tertiary — Muted Sage
| Name | Hex | Use it for |
|---|---|---|
| `tertiary` | `#b8cea1` | Occasional accent, success states |
| `on-tertiary` | `#243516` | Text on tertiary backgrounds |
| `tertiary-container` | `#3a4c2b` | Tertiary tinted containers |
| `on-tertiary-container` | `#d4eabb` | Text in tertiary containers |

### Error
| Name | Hex | Use it for |
|---|---|---|
| `error` | `#ffb4ab` | Error text, destructive action indicators |
| `on-error` | `#690005` | Text on error backgrounds |
| `error-container` | `#93000a` | Error background fill |
| `on-error-container` | `#ffdad6` | Text inside error containers |

### Additional semantic accents
| Name | Hex | Use it for |
|---|---|---|
| `warning` | `#dcbd76` | Warning/highlight gold — distinct from `primary` |
| `danger` | `#cf7152` | Destructive text and icons — distinct from `error` (a different, more muted red-orange) |
| `success` | `#7fb87f` | Success states (e.g. sync complete, connected) |
| `success-bright` | `#6ee7b7` | A brighter success accent, used alongside `success` in a couple of spots |
| `accent-violet` | `#8b7cf6` | Natural-language search chips ("nlChips") |
| `accent-violet-light` | `#a99bf7` | Lighter variant of `accent-violet` |
| `accent-violet-lighter` | `#c9a8f2` | Lightest variant of `accent-violet` |
| `accent-blue` | `#7fb3d9` | Composition-guide overlay accent |
| `accent-blue-light` | `#8fc3d8` | Aspect-ratio suggestions (Home autocomplete) |
| `accent-blue-muted` | `#7fa9d9` | Recent view's accent (CollectionPage) |
| `accent-teal` | `#7dd3c8` | A secondary accent color |
| `accent-orange` | `#e0935a` | Note/film suggestions (Home autocomplete) |
| `heatmap-text-hot` | `#f4e8cd` | Tag-frequency heatmap, hot (well-used) cell text |
| `heatmap-text-cool` | `#d6c9a8` | Tag-frequency heatmap, cool (rarely-used) cell text |
| `danger-warm` | `#e07a5f` | Inline warning text (SharePage), CropModal's destructive ghost button. **V68:** absorbed a second near-identical token (`#e07a55`, one hex digit apart) kept distinct mid-migration for the same lossless reason as `on-surface-warm-dim` above. **V69:** this is exactly the drift the `withAlpha()` migration was meant to prevent — CropModal's own border for this button was still hand-typed as `rgba(224,122,85,...)`, the *old* merged-away value, silently one shade off its own button's text since V68. Fixed to `withAlpha(danger-warm, 0.55)` |
| `on-surface-cool` | `#aab2c0` | Offline-banner text (App shell, DeckDetail) |
| `offline-accent` | `#8c96aa` | Offline-mode banner background/border (App shell, DecksPage, DeckDetail) — a tinted-chip pair, not just text |
| `accent-similar` | `#b282f0` | The "Similar to…" chip and similarity-percentage badge border (Home) |
| `overlay-violet` | `#1e142d` | The similarity badge's dark backdrop — pairs with `accent-similar` |
| `accent-film` | `#6fa3b8` | The film-credit chip's background/border (Home) |
| `presentation-control-bg` | `#141416` | PresentationMode's floating nav buttons and pill |

### Neutral base
| Name | Hex | Use it for |
|---|---|---|
| `white` | `#ffffff` | Almost never used at full opacity — nearly every use is `withAlpha(white, …)` for hairline borders, subtle overlays |
| `black` | `#000000` | Same — modal backdrops, drop shadows, scrims, almost always via `withAlpha(black, …)` |

### Translucency
Every color above has translucent variants somewhere in the app (a hover fill, a border, a backdrop) — hand-typing each as its own `rgba(R,G,B,A)` literal is exactly the duplication problem this whole system exists to avoid, just for opacity instead of hue. `theme.js` exports `withAlpha(hex, alpha)`, which turns any token into an `rgba()` string: `withAlpha(primary, 0.2)` instead of a separately-typed `rgba(217,164,65,0.2)`. Change the token, every alpha derived from it updates too; a stale RGB triple one digit off from its own token (see `danger-warm` above) becomes structurally impossible. As of V69 this covers every translucent color in the frontend — none are hand-typed RGB triples anymore.

### Color-search swatch picker
A separate, deliberately multi-hue 12-color palette (the round swatches under the search bar) — not part of the neutral/gold app-chrome system above. Lives as `SWATCH_COLORS` in `frontend/src/theme.js`.

### Tag category colors
The 15 tag-category colors (shown on tag chips) are **not** part of this file or `theme.js` — they're defined once on the backend (`CAT_COLORS` in `backend/app.py`) and served to the frontend via `/api/tag-categories`. Already a proper single source of truth; nothing to migrate there.

---

## Typography

**Font family:** Manrope (import from Google Fonts)

```css
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&display=swap');
```

| Style name | Size | Line height | Weight | Use it for |
|---|---|---|---|---|
| `headline-lg` | 32px | 40px | 700 | Page titles |
| `headline-md` | 28px | 36px | 600 | Section headers |
| `title-lg` | 22px | 28px | 600 | Panel titles, modal headers |
| `title-md` | 16px | 24px | 500 | Card titles, sidebar labels |
| `body-lg` | 16px | 24px | 400 | Primary body text |
| `body-md` | 14px | 20px | 400 | Secondary body, metadata |
| `label-lg` | 14px | 20px | 500 | Button labels, tag chips |
| `label-md` | 12px | 16px | 500 | Captions, small labels |

---

## Spacing

| Token | Value | Use it for |
|---|---|---|
| `gutter-grid` | 24px | Gap between image grid columns |
| `panel-padding` | 16px | Internal padding inside panels and cards |
| `item-spacing` | 12px | Space between items in a list or stack |

---

## Shape

- **Border radius:** 12px on all cards, panels, buttons, chips, and modals
- Inputs: 8px radius is acceptable for smaller elements
- Images in the grid: 8px radius

---

## Layout Architecture

Three-column layout on desktop:

```
┌─────────────┬──────────────────────────┬─────────────┐
│             │                          │             │
│  Left       │   Center Content         │  Right      │
│  Sidebar    │   (Masonry Image Grid)   │  Inspector  │
│  Nav        │                          │  Panel      │
│             │                          │  (slides in)│
│  ~220px     │   Fills remaining space  │  ~320px     │
│             │                          │             │
└─────────────┴──────────────────────────┴─────────────┘
```

- **Left sidebar:** `sidebar-surface` background (`#111114`), `outline-variant` right border
- **Center:** `surface` background (`#1c1e22`), masonry grid with `gutter-grid` (24px) gaps
- **Right inspector:** Slides in from right on image click, `surface-container-low` background, does not push content (overlaps)

---

## Component Patterns

### Image Cards (grid thumbnails)
- No border by default
- 8px border radius
- On hover: subtle scale up (`transform: scale(1.02)`), transition 150ms ease
- On hover: very subtle shadow (`box-shadow: 0 8px 24px rgba(0,0,0,0.4)`)
- Selected state: `primary` (`#d9a441`) border, 2px

### Tag Chips
- Background: `surface-container-high` (`#2a2c31`)
- Text: `on-surface-variant` (`#c4c6d0`)
- Border: `outline-variant` (`#44474f`)
- Border radius: 99px (fully rounded, pill shape)
- Padding: 6px 12px
- Font: `label-lg` (14px / 500)
- **Active/selected chip:** background `primary-container` (`#594400`), text `on-primary-container` (`#ffdf9d`), border `primary` (`#d9a441`)
- Remove (×) button inside chip: appears on hover

### Search Bar
- Background: `surface-container-low` (`#1a1c20`)
- Border: `outline-variant` (`#44474f`), 1px
- Border radius: 12px
- On focus: border changes to `primary` (`#d9a441`)
- Font: `body-lg` (16px / 400)
- Placeholder text color: `outline` (`#8e9099`)

### Buttons — Primary
- Background: `primary` (`#d9a441`)
- Text: `on-primary` (`#3d2f00`)
- Border radius: 12px
- Font: `label-lg` (14px / 500)
- Hover: slightly lighter gold, subtle shadow

### Buttons — Secondary / Ghost
- Background: transparent
- Border: `outline-variant` (`#44474f`), 1px
- Text: `on-surface` (`#e2e2e6`)
- Hover: background `surface-container-high` (`#2a2c31`)

### Panels and Cards
- Background: `surface-container-low` (`#1a1c20`)
- Border: `outline-variant` (`#44474f`), 1px
- Border radius: 12px
- Padding: `panel-padding` (16px)

### Dropdowns / Autocomplete
- Background: `surface-container-high` (`#2a2c31`)
- Border: `outline-variant` (`#44474f`), 1px
- Border radius: 12px
- Item hover: background `surface-bright` (`#37393e`)
- Shadow: `0 8px 32px rgba(0,0,0,0.5)`

---

## Interaction Principles

1. **Hover states are always subtle** — scale, shadow, or background lightening. Never jarring.
2. **Transitions:** 150ms ease for most interactions. 250ms for panels sliding in/out.
3. **Glassmorphism** for tooltips and floating overlays: `backdrop-filter: blur(12px)`, semi-transparent background.
4. **Focus states:** Gold outline (`primary`) on all interactive elements for accessibility.
5. **Loading states:** Skeleton screens using `surface-container-high` with a shimmer animation — never spinners on image content.

---

## What to Avoid

- Bright white backgrounds — always use the dark surface tokens
- Harsh borders — prefer `outline-variant` over `outline` wherever possible
- Rounded corners less than 8px or more than 16px
- Multiple competing accent colors — gold is the one accent, use it sparingly
- Dense text — Frame Atlas is image-first; UI chrome should be minimal
- Generic "web app" feel — every component should feel considered and intentional

---

## Reference File

`/docs/Frame_Atlas.html` — open this in a browser to see the full visual reference for the intended look and feel.
