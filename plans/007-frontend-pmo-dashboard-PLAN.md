# 007 — Frontend: the PM-OS Dashboard

**Covers:** #12 (copy the frontend service where kanban lives, build on top), #13
(apply the `erp.datansh.com` design system).
**Depends on:** 001 for the fork; consumes the APIs from 002–006.
**Time budget:** 2 h, spread through the day rather than taken as one block.
**Definition of done:** `/pmo` renders the ERP shell, a project switcher, a board with
a `draft` column, a ticket drawer with `@`-autocomplete comments, an Approvals inbox
and the Founder's Office chat — and a screenshot placed beside `erp.datansh.com/tasks/board`
reads as the same product.

---

## 1. The good news about #12

`plugins/kanban/dashboard/dist/index.js` is **not a build artifact**. Its own header:

> *"Plain IIFE, no build step. Uses `window.__HERMES_PLUGIN_SDK__` for React + shadcn
> primitives; HTML5 drag-and-drop for card movement on desktop and a pointer-based
> fallback for touch."*

4,280 lines of readable hand-written source, `React.createElement` (aliased `h`), no
JSX, no bundler. So "copy paste the frontend service" is literally `cp -r` and then
edit the file. No `npm run build`, no watch process, no TypeScript config.

The cost of that: no JSX and no type checking. Accept it for day 1 — the trade is
worth it. Migrating to a real build is a P1 note at the end of this plan.

## 2. Reading the fork before editing it

Spend fifteen minutes mapping the file before you touch it. It is organised roughly:

1. SDK unwrap + component shims (`Checkbox`, `useI18n` fallbacks for older hosts —
   **keep these**, they are why the bundle survives host upgrades)
2. API helpers
3. `useBoard` / polling + `/events` WebSocket
4. Card, Column, Drawer components
5. Drag-and-drop (HTML5 + pointer fallback)
6. Board root + filters
7. Registration with the host

You are keeping 3, 4 and 5 nearly intact and rewriting 6 into a router with several
screens.

## 3. Styling

Per `design/design.md` §8, four rules:

1. **Everything from design.md §2b/§4/§5 goes into
   `plugins/pmo/dashboard/dist/style.css` as literal CSS.** No Tailwind build exists
   inside the plugin.
2. **Scope every selector under `.pmo-root`** so the plugin cannot restyle the host
   Hermes dashboard. Wrap the plugin's top-level element in `<div class="pmo-root">`.
3. **Prefer plain elements over the SDK's shadcn primitives.** `SDK.components.Button`
   carries Hermes' theme, not Datansh's. Use `<button class="btn btn-primary">`.
   The SDK is still worth using for `React`, `hooks`, `cn` and `timeAgo`.
4. **Self-host the fonts** into `web/public/fonts-datansh/`
   (Plus Jakarta Sans 400/500/600/680, IBM Plex Mono 400/500) and `@font-face` them
   from the plugin stylesheet. No Google Fonts hotlink — the dashboard runs offline.

Write the stylesheet **first**, before touching the JS. Having the tokens and
`.btn`/`.widget`/`.pill`/`.tbl`/`.hnav` classes available makes every component below a
matter of putting the right class name on the right element.

## 4. Screens

### 4a. Shell

Reproduce design.md §4 inside the plugin's page area: the plugin owns the content
column, the host owns the outer chrome. So render the **232px section nav** and the
**page header** yourself; do not try to take over the host's sidebar.

```
┌──────────┬──────────────────────────────────────────┐
│ .hnav    │  page-title / page-sub      [primary]    │
│  232px   │  filter row                              │
│          │  screen content                          │
└──────────┴──────────────────────────────────────────┘
```

`.hnav-head`: a 32px `.hnav-badge` with the module glyph, **Datansh PM-OS**, and the
active project name as the 11px muted descriptor.

Nav items (006 §5): Board · Backlog · My Queue · Approvals · Founder's Office ·
Escalations · Members. Counts as small right-aligned badges on Approvals, Escalations
and My Queue.

### 4b. Project switcher

A `<select>` in the filter row, exactly like the ERP's Task Board project select
(`BeWizor (PRJ-001)` — design.md §6d). It lists **only projects you are a member of**;
the server filters, not the client.

Changing it changes `project_id` on every subsequent call and re-subscribes the
WebSocket. Persist the choice in `localStorage` (UI state, not user config — this is
not a violation of #11; see 002 §5).

### 4c. Board

Columns: **Draft · To Do · In Progress · Blocked · Review · Done**, from
`board.columns` in `.datansh/project.yaml` with `draft` always prepended.

Column header, copying the ERP's markup shape (design.md §6d):

```js
h("div", { className: "pmo-col-head" },
  h("div", { className: "pmo-col-title" }, label),
  h("span", { className: "pmo-col-count" }, String(count)))
```

Card contents: title, ticket id in mono, assignee chip (colour by kind, design.md §8),
priority dot, comment count, and — new — an **Awaiting approval** or **Escalated** pill
when applicable.

Keep the existing drag-and-drop **and** the per-card move control. The ERP's own
subtitle says *"Drag cards across columns, or use each card's move control"* — that
accessibility affordance is part of the design language, not an optional extra.

`draft` cards are visually muted and not draggable out of the column: they leave only
via **Finalize**, which is the PM's action (004 §4). A human `project_admin` gets a
Finalize button; everyone else sees the column read-only.

WIP limits from `board.wip_limits` colour the column count `--warning` when exceeded.
Advisory, not blocking, on day 1.

### 4d. Ticket drawer

The existing drawer is good — keep its structure and restyle it. Add:

- **Comments tab as the default view.** This is the communication surface (#7); it
  should not be behind a click.
- Comment rendering per 005 §7: identity chips, inline `@handle` tokens, muted system
  comments.
- **Composer with `@` autocomplete.** Typing `@` opens a filtered list of this
  project's handles and members. This list *is* the scope boundary made visible.
- Acceptance-criteria block rendered from the `## Acceptance` section, since
  `pmo_ticket_finalize` refuses without it — showing it makes the refusal legible.
- Run history (already in the fork) and attachments (already in the fork) stay.

### 4e. Approvals inbox

`.widget` + `.tbl` (design.md §5): `TITLE | PROJECT | TICKET | REQUESTED BY | NEEDS |
STATUS | ⋯`. Two segmented tabs (`.seg`): **To decide** / **Raised by me**.
Row click opens the approval detail with Approve / Reject / Escalate ↑.

### 4f. Founder's Office

Built per 006 §5. This is the highest-value screen; budget the most polish here.

### 4g. Members

Copy `erp.datansh.com/project/settings` almost exactly — design.md §6c documents it.
`.widget` "Members", `+ Add member` in the head, table with an **inline `<select>` for
role per row**, status pill, trailing icon button. Org-role column editable only by a
CEO.

## 5. State and data

Keep the fork's polling + WebSocket hybrid; it is already battle-tested against the
kanban board.

- One `useProject()` hook holding the active `project_id`; every fetch goes through a
  wrapper that injects it. No component builds a URL by hand — that is how a
  cross-project call ships.
- Optimistic updates on card moves and role changes, reconciled by the next event.
- On WS disconnect: fall back to 5-second polling and show a small muted "reconnecting"
  line in the header. Do not show a modal; the board should stay usable.

## 6. Verification

The design is a fidelity target, so check it like one:

1. Open `erp.datansh.com/tasks/board` and `/pmo` side by side at the same zoom.
2. Compare: nav width (232px), row density, `--control-h` (36px), pill height (22px),
   card radius (`--r-lg` 12px), shadow (`--sh-card`), heading size (22px/680/-0.025em).
3. Toggle dark mode on both.
4. `resize_window` to 1280×800 and to tablet; the board must scroll horizontally inside
   its own container, never the page body.
5. Keyboard: tab to a card, use the move control, tab to the composer, `@`-autocomplete
   with arrow keys and Enter. Focus rings must be `2px solid var(--brand)` throughout.

## P0 / P1

**P0:** the stylesheet with all tokens; `.pmo-root` scoping; shell + section nav;
project switcher; board with the `draft` column and existing DnD; ticket drawer with
comments + `@` autocomplete; Approvals inbox; Founder's Office chat; Members page.

**P1:** Backlog and My Queue as distinct screens (day 1 can filter the board);
Escalations screen (day 1: a filter on the board); i18n strings (the fork has a
`useI18n` shim — leave English literals inline and extract later); a real Vite build
for the plugin with JSX + TS; virtualized columns for boards over ~200 cards; charts.

## Traps

- **Hard-refresh after every edit.** The IIFE is served as a static file with no HMR.
  You will otherwise spend twenty minutes debugging a cached bundle. `Ctrl+Shift+R`.
- **Do not delete the SDK shims** at the top of the fork (`Checkbox`, `useI18n`). They
  exist so the bundle renders on older hosts; removing them is an invisible break.
- **Do not restyle the host.** Every selector under `.pmo-root`. A global `button { }`
  rule in the plugin stylesheet will bleed into the whole Hermes dashboard.
- **No JSX.** It is `h(tag, props, ...children)`. A stray `<div>` is a syntax error at
  load time with a stack trace that points nowhere useful.
- **Do not hotlink fonts.** Self-host, or the dashboard degrades to system fonts
  offline and the design fidelity evaporates.
- Density: the ERP is a dense enterprise app. Resist loosening padding — it is the
  fastest way to make the port stop looking like the original.
