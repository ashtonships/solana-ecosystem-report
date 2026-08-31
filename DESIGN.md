---
version: alpha
name: Solana Ecosystem Report
description: Evidence-led Solana network and ecosystem reporting for people and agents.
colors:
  primary: "#5522e0"
  tint: "#bcb3ff"
  ink: "#18181b"
  muted: "#71717a"
  canvas: "#ffffff"
  rule: "#e4e4e7"
  grid: "#f4f4f5"
  positive: "#5522e0"
  negative: "#52525b"
  warning: "#f4a60b"
  sampled: "#bcb3ff"
typography:
  display:
    fontFamily: 'Archivo, sans-serif'
    fontVariationSettings: "'wdth' 112.5"
    fontSize: "2.5rem"
    fontWeight: 800
    lineHeight: 0.92
    letterSpacing: "-0.035em"
  body:
    fontFamily: 'Archivo, sans-serif'
    fontVariationSettings: "'wdth' 112.5"
    fontSize: "14px"
    fontWeight: 500
    lineHeight: 1.5
  label:
    fontFamily: 'Archivo, sans-serif'
    fontVariationSettings: "'wdth' 112.5"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.2
rounded:
  restrained: "18px"
  control: "9px"
  mobile-panel: "18px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "44px"
components:
  panel:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    rounded: "{rounded.restrained}"
    padding: "16px"
  status:
    backgroundColor: "#faf8ff"
    textColor: "{colors.primary}"
    rounded: "{rounded.control}"
    padding: "5px 9px"
  action:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.primary}"
    rounded: "{rounded.control}"
    padding: "12px 16px"
---

# Design System: Solana Ecosystem Report

## Overview

**Creative North Star: "The Evidence Field Instrument"**

The interface behaves like a public research instrument: quiet enough for extended reading, precise enough for operational use, and distinctive through disciplined violet evidence marks rather than decorative crypto styling. Charts, controls, provenance, and limitations share one visual grammar so each page feels like another view of the same recorded system. Light preserves the original white-field identity and is the first-visit default; Dark and System are complete user-selectable modes.

Mobile is re-composed around one-handed inspection and a persistent five-view dock. Desktop is a wide editorial workbench with a centered report navigation, compact evidence density, and the same source language.

**Key Characteristics:**
- Light uses a white field and black evidence text; Dark uses zinc-black fields and high-contrast light text; both retain one focused violet.
- Fine neutral rules instead of ornamental cards and shadows.
- Tabular numerals and explicit measurement basis.
- Recorded, sampled, estimated, unavailable, and test states never rely on color alone.
- Page-specific compositions inside one shared shell.

## Theme behavior

- No saved preference means System, following the operating-system theme.
- The native theme controls offer Light, Dark, and System and store only that explicit choice under `solana-report-theme`.
- System follows the operating-system color scheme without replacing the stored `system` choice.
- Theme tokens, focus, state labels, charts, tables, loading/empty/error surfaces, and fixed mobile chrome must remain legible in every mode.
- The bootstrap applies a valid saved choice before body paint; an invalid or unavailable preference falls back to System.

## Colors

Violet indicates product focus and recorded evidence; semantic colors communicate state, never decoration.

### Primary
- **Superteam Violet** (`#5522e0`): active navigation, key chart series, focus rings, selected controls, and evidence emphasis.

### Secondary
- **Sampled Violet** (`#a06bea`): sampled and extrapolated series that must remain distinct from directly measured evidence.
- **Recorded Green** (`#12894a`): verified positive or healthy recorded states.
- **Material Red** (`#c84b35`): negative movement, unhealthy state, or blocking failure.
- **Caution Amber** (`#b7862e`): warnings and bounded uncertainty.

### Neutral
- **Evidence Ink** (`#101012`): primary text and values.
- **Field White** (`#ffffff`): page and panel surface.
- **Muted Graphite** (`#64646b`): provenance and secondary explanation.
- **Rule Gray** (`#deded9`): separators and panel outlines.
- **Grid Gray** (`#ececf0`): chart guides and skeleton structure.

**The One-Violet Rule.** Violet carries focus; do not add competing brand accents or crypto gradients.

## Typography

**Display Font:** the bundled Archivo Semi Expanded face, with the native
platform sans display stack as its fallback.
**Body Font:** the bundled Archivo Semi Expanded face, with the native platform
sans text stack as its fallback.

**Character:** compact, technical, and editorial without using monospace as costume. Numerals use tabular alignment; prose remains a comfortable workhorse sans.

### Hierarchy
- **Display** (675, `clamp(2rem, 4.6vw, 3.75rem)`, `0.96`): page thesis and major comparison state.
- **Headline** (650–680, 22–32px): route and section entry.
- **Title** (620–680, 13–18px): panels, controls, and chart names.
- **Body** (400, 13–16px, 1.5–1.6): interpretation and method copy; target 65–75 characters per line.
- **Label** (620, 9–12px): basis, units, timestamps, and provenance.

**The Measurement Rule.** Every prominent number keeps its unit, period, comparison, or evidence basis close enough to read as one claim.

## Layout

Desktop uses the full canvas with a sticky 60px report header and bounded content regions up to roughly 1,560–1,840px where the data benefits from width. The Overview uses an editorial metric rail, chart grid, and two-part evidence row; Data uses a catalog and provenance support row; Methods uses a source flow and method ledger; History uses selectors, A/B comparison, and deltas; Project uses a plain-language orientation, recorded snapshot status, proof links, and the recorded Development stream.

At 700px and below, desktop compositions are replaced by mobile-specific views rather than squeezed. The mobile shell uses a shared 60px status topbar, 12–16px page gutters, and a fixed 74px five-view dock with safe-area padding. Intermediate widths use two-column compositions before the wide desktop grid.

## Elevation & Depth

The system is flat by default. Hierarchy comes from whitespace, rules, tonal fields, and data density. Shadows are reserved for sticky mobile chrome, dialogs, and transient tooltips where separation from moving content is functional.

**The Structural Depth Rule.** Static evidence panels use either a border or tonal separation, not a decorative border-plus-shadow stack.

## Shapes

Panels use restrained 7px corners on desktop and 10–12px where mobile touch surfaces need more generosity. Status pills are reserved for compact state labels. Charts use crisp rectangular fields, fine axes, and direct endpoint markers.

## Components

### Buttons and actions
- Primary report actions use violet text or violet outlines on white, direct verb-object labels, and at least 44px touch height.
- Focus uses a 2px violet outline with visible offset.
- Disabled or unavailable actions explain the missing prerequisite.

### Status labels
- Compact violet-on-pale-violet state treatment.
- Wording names the basis: recorded snapshot, offline, sampled, unavailable, or UI test state.

### Panels and evidence cards
- White surface, 1px neutral rule, 7–12px radius, no decorative glow.
- Internal spacing groups one claim, its value, and its evidence basis.

### Inputs and disclosures
- Search and selectors use white fields, neutral rules, and violet focus.
- Mobile disclosures keep one section open where comparison benefits from focus.
- Empty search, loading, empty-data, and error states use product-specific recovery copy.

### Navigation
- Desktop centers five text routes in the sticky header; the active route uses violet text and a 2px underline.
- Mobile uses the same five routes in a fixed icon-and-label dock. Every route shares one product/status topbar.

### Charts
- Measured series are solid violet; sampled series are both differently hued and dashed.
- Missing observations break lines. A chart with fewer than two usable observations shows an explicit unavailable state instead of a zero line.
- Hover and keyboard inspection expose the same recorded values available in JSON.

## Do's and Don'ts

### Do:
- **Do** preserve measurement basis, source, freshness, and missingness near every important claim.
- **Do** compose mobile and desktop independently inside the shared identity.
- **Do** keep UI-test fixtures visibly labelled and separate from normal recorded output.
- **Do** use the shared topbar, navigation, focus, rule, radius, and evidence tokens on every route.

### Don't:
- **Don't** fabricate scores, confidence, interpolated observations, active-address estimates, or causal stories.
- **Don't** add crypto gradients, glowing edges, glass cards, or generic dashboard tile soup.
- **Don't** hide unavailable data behind zero, an empty chart, or a reassuring color.
- **Don't** make one route invent a separate shell, status treatment, or typographic system.
