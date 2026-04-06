# BaseStock Full Redesign

## Overview

Full visual redesign of the BaseStock CRM (used phone inventory management). Transition from emerald green dark theme with sidebar to a bold Cyan+Magenta neon aesthetic with top navbar.

## Design Decisions

### Theme: Dark + Bold/Vibrant

### Color Palette: Cyan + Magenta

| Token | Value | Usage |
|-------|-------|-------|
| `--bg` | `#080810` | Page background |
| `--bg2` | `#101018` | Cards, panels |
| `--bg3` | `#18182a` | Inputs, hover states |
| `--bg4` | `#22223a` | Table headers, elevated surfaces |
| `--accent` | `#06b6d4` | Primary accent (cyan) — links, active nav, primary info |
| `--accent2` | `#d946ef` | Secondary accent (magenta) — CTAs, badges, highlights |
| `--cyan` | `#22d3ee` | Light cyan — hover states, subtle highlights |
| `--text` | `#e2e8f0` | Primary text |
| `--muted` | `#9494a6` | Secondary text |
| `--success` | `#34d399` | Success states |
| `--warn` | `#f59e0b` | Warning states |
| `--danger` | `#ef4444` | Error/danger states |
| `--border` | `#1a1a2a` | Subtle borders |
| `--border2` | `#2a2a3a` | Stronger borders |
| `--gradient` | `linear-gradient(135deg, #06b6d4, #d946ef)` | Primary gradient |
| `--gradient2` | `linear-gradient(135deg, #22d3ee, #06b6d4)` | Secondary gradient |

### Logo: Shield + Phone with pulse glow animation

- SVG icon: shield outline with phone silhouette inside
- Gradient stroke: cyan → magenta
- Pulse glow animation: subtle breathing glow on the shield (2-3s cycle)
- Wordmark: "Base" in `--text` + "Stock" with gradient text (cyan → magenta)

### Typography

| Role | Font | Weights |
|------|------|---------|
| UI text, headings | Space Grotesk | 400, 500, 600, 700 |
| Prices, IMEI, mono data | Fira Code | 400, 500, 700 |

Source: Google Fonts.

### Navigation: Top Navbar

Replace collapsible sidebar with horizontal top navbar:
- Logo (left) — shield icon + "BaseStock" wordmark
- Nav items (center) — horizontal menu with active indicator
- Right section — store selector, role badge, user avatar/menu
- Mobile: hamburger → dropdown menu

### Store Colors (preserved)

| Store | Color |
|-------|-------|
| iPrice.Store | `#a78bfa` |
| МОБИЛАКС | `#c084fc` |
| REM-GSM | `#60a5fa` |
| ДИСКИ | `#f97316` |
| ТЕХНО | `#f472b6` |
| Склад | `#94a3b8` |

## Assets to Generate (Replicate)

1. **Favicon** — 512x512 PNG, shield+phone icon, neon cyan+magenta on dark bg
2. **Login background** — abstract neon art, dark, cyan/magenta tones, suitable as fullscreen bg
3. **Empty state illustration** — minimal neon illustration for empty tables/lists

## Component Redesign

### Buttons
- Primary: gradient background (cyan→magenta), dark text, bold
- Outline: cyan border, cyan text, transparent bg
- Ghost: no border, muted text, hover → bg3
- Danger: red variant

### Tables
- Header: bg4 background, uppercase muted labels (Space Grotesk)
- Rows: bg2 background, subtle border-bottom
- Hover: bg3 with left border accent (cyan)
- Prices in Fira Code

### Cards / Modals
- Background: bg2
- Border: border color
- Header with gradient accent line (top border)
- Backdrop blur on modal overlay

### Chips / Badges
- Excellent: success bg with success text
- Good: cyan bg with cyan text
- Fair: warn bg with warn text
- Bad/Repair: danger bg with danger text
- Sold: magenta bg with magenta text

### Inputs
- Background: bg3
- Border: border, focus → cyan
- Placeholder: muted color

### Login Page
- Centered card on generated background image
- Animated logo with glow
- Gradient submit button

## Layout Changes

### Before (current)
```
┌──────────┬────────────────────┐
│ Sidebar  │ Topbar             │
│ 250px    ├────────────────────┤
│          │ Content            │
│          │                    │
└──────────┴────────────────────┘
```

### After (new)
```
┌──────────────────────────────────┐
│ Navbar (logo | nav items | user) │
├──────────────────────────────────┤
│                                  │
│ Content (full width, max-width)  │
│                                  │
└──────────────────────────────────┘
```

- Content area: max-width 1400px, centered, with padding
- No sidebar — full horizontal space

## Pages

All pages receive the new palette, typography, and component styles. Specific changes:

- **Login**: fullscreen bg image, centered glass card, animated logo
- **Products (Used/New/Sold)**: full-width table, better filters bar, photo thumbnails
- **Product Card modal**: wider, better photo gallery, cleaner sections
- **Avito / Messages**: updated cards and message bubbles
- **Analytics**: charts with new palette
- **Competitor Prices**: updated table styles
- **Users / Logs / Settings (admin)**: consistent with new design system

## Animations

- Logo shield: pulse glow (box-shadow breathing, 2-3s)
- Page transitions: fadeUp on mount
- Buttons: scale + glow on hover
- Modals: scaleIn entrance
- Table rows: subtle fadeIn on load
- Nav items: underline slide on active
