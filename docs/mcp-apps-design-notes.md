# MCP Apps Card Design Notes (Phase 3 prep)

Researched 2026-10-04 from the official Alexa+ Design Guide
(Layout and Rendering + QuickStart). Implications for HomeKeeper's cards.

## Source facts

- **Viewport types**: Block (wider-than-tall, scales + scrolls horizontally —
  *recommended for native Alexa+ designs*) vs Card (taller-than-wide, scales +
  scrolls vertically — primarily for web interfaces). Orientation decides:
  design wider than tall → Block. For Echo Show, adapt Card patterns to Block
  treatments where possible. No custom breakpoints needed — Alexa snaps the
  viewport to its grid and CSS-scales components.
- **hostContext** (via MCP Apps postMessage bridge at render time):
  `deviceClass` (authoritative — wins over everything), `maxWidth`,
  `maxHeight`, `isMobile`, `platform`, `displayMode`, `safeAreaInsets`,
  `deviceCapabilities`, `theme`. Resolve once at render time → single CSS class
  on root. Re-adapt on `host-context-changed` event (rotation, surface change).
  No reload, no extra tool calls; the MCP wire is identical across surfaces.
- **Sandboxed iframe**: opaque origin. No `window.parent`, no cross-origin
  reads, no persistent `localStorage`/cookies. The postMessage bridge
  (`ui/notifications/*`) is the ONLY host channel.
- **CSP**: declare `connectDomains` (fetch/XHR) and `resourceDomains`
  (images/media) in `_meta.ui.csp`. Anything undeclared is blocked. Keep both
  minimal — ideally empty for our cards (everything inline).
- **Packaging**: one self-contained HTML file, all deps inlined/co-located.
  Version the resource URI with a content hash
  (`ui://…/widget-<hash>.html`) so the host caches correctly. Gzip the bundle.
  Lazy-load heavy deps (we have none — keep it that way).
- **Local Inspector**: standalone tool emulating the host — synthesizes
  `hostContext` per device class, renders in device bezels (mobile, Echo Show
  8/15, voice-only), light/dark switching. Use it before any hardware testing.
  Final verification still needs real devices + both themes.
- **Fallback**: a tool response WITHOUT `resourceUri` gets Alexa+'s default
  data rendering. So every card is optional — structured data alone still
  shows something. (This is our documented fallback.)

## Decisions for HomeKeeper cards

1. **Briefing card (must-have)**: design wider-than-tall → Block viewport on
   Echo Show. Urgency-grouped list (overdue → due soon → warranty → recalls),
   each row tappable to drill into detail. All state comes from the tool
   response JSON — zero localStorage, zero network fetches (empty CSP lists).
2. **One HTML file per card**, inline CSS/JS, no external assets. Target
   < 50 KB uncompressed each; gzip on serve.
3. **Theme**: read `theme` from hostContext, support light + dark from day one
   (test both in Local Inspector).
4. **Touch targets**: keep ≥ minimum per Accessibility guide; rows must remain
   tappable on Echo Show 8.
5. **resourceUri wiring**: tool responses include `resourceUri` pointing at the
   hashed widget URI only when the card is ready; otherwise omit it and let
   default rendering carry the demo (per fallback above).
6. Cards to build in order: briefing (must) → appliance detail → reorder →
   photo-onboarding QR. If time runs short, ship briefing only.

## Open questions for Phase 3
- Exact `resourceUri` scheme Alexa+ expects (`ui://` vs https) — confirm in
  MCP Apps SDK docs / @modelcontextprotocol/ext-apps Agent Skill when
  scaffolding (blueprint suggests using the Agent Skill for this).
- Whether tool responses can carry per-item deep links for tap-through, or
  whether drill-down needs a second tool call.
