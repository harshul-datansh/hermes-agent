# 025 — Accessibility, i18n & the UX Quality Bar

**Why this plan exists:** two of these are silent regressions against the host, and the
third has no written standard.

Hermes ships **20 locales** in `web/src/i18n/`, and the kanban plugin bundle carries a
`useI18n` shim precisely so it degrades gracefully. Our fork ships English string
literals — a capability the host has and we drop. And `erp.datansh.com` makes a specific
accessibility commitment in its own copy that we would break by copying only the visuals.

**Day-1 touchpoint:** put every user-facing string in one `STRINGS` object at the top of
the bundle. Ten minutes on day 1; a full-file rewrite later. And keep the per-card move
control — it is already in the fork.
**Full build:** 2 h.

---

## 1. The accessibility commitment we inherit

The ERP's own Task Board subtitle reads:

> *"Drag cards across columns, or use each card's move control."*

That is a design-language commitment, not a nicety: **drag-and-drop is never the only way
to move a card.** The forked kanban bundle already implements both (HTML5 DnD plus a
pointer fallback and a move control). 007 says keep it. This plan says why, and adds the
rest.

### The bar

| Area | Requirement |
|---|---|
| **Every drag has a non-drag equivalent** | Card move control; keyboard-reachable |
| **Focus visible everywhere** | `outline: 2px solid var(--brand); outline-offset: 1px` (design.md §7). Never `outline: none` |
| **Keyboard path through every flow** | Post a message, `@`-autocomplete, decide an approval, move a card, open a drawer, close it with Esc |
| **Focus trapped and restored in the drawer/modal** | Open → focus first control; close → focus returns to the trigger |
| **Semantic roles** | Columns `role="list"`, cards `role="listitem"`, the board `role="region"` with a label |
| **Live regions** | New chat messages and board changes announced via `aria-live="polite"`. Not `assertive` — a busy board would talk continuously |
| **Colour is never the only signal** | Every status pill carries text, not just a tint. Blocked cards get an icon as well as the `--danger` border |
| **Contrast** | ≥4.5:1 body, ≥3:1 large text and UI borders — **in both themes.** `--muted #515b6c` on `--surface #fff` passes; verify `--muted-foreground` on `--surface-sunken`, which is the pair most likely to fail |
| **Motion** | Respect `prefers-reduced-motion`: drop the 120–160 ms transitions, keep the state change |
| **Target size** | ≥24×24 px. `--control-h` is 36 px so buttons pass; check icon-only buttons in table rows |

### The `@`-autocomplete is the risky one

It is a custom combobox and it is the most-used control in the product (#7). Get it right:
`role="combobox"` + `aria-expanded` + `aria-activedescendant`, arrow keys move,
Enter selects, Esc closes without selecting, and typing continues to filter. A custom
autocomplete with no keyboard support makes the only communication channel in the system
mouse-only.

### Testing

- Keyboard-only pass through the 008 acceptance scenario. No mouse. If any step is
  impossible, it is a bug, not a polish item.
- One screen-reader pass (NVDA on Windows) over the board and the chat.
- `axe` DevTools on each screen — it catches the mechanical half.

---

## 2. i18n

**Decision (021 GAP 8): English-only on day 1, structured for extraction.**

The reasoning: the plugin has no build step (007 §1), so there is no extraction tooling
available, and doing it properly means the Vite migration first. Doing it *badly* — a
half-populated locale file — is worse than English-only, because it makes the product look
translated while leaving sentences in the wrong language mid-screen.

What day 1 must do anyway, because it is the difference between a rename and a rewrite:

```js
const STRINGS = {
  board: { draft: "Draft", todo: "To Do", inProgress: "In Progress",
           blocked: "Blocked", review: "Review", done: "Done" },
  approval: { needs: (role) => `Needs: ${role}`,
              escalate: "Escalate ↑",
              requiresTooltip: (role) => `Requires ${role}` },
  empty: { board: "No tickets yet. Ask the PM in Founder's Office …" },
  // ...
};
const t = (path, ...args) => { /* lookup; call if function */ };
```

Rules:

- **No string literal in a render function.** Everything through `t()`.
- **Interpolation via functions, not concatenation.** `` `Needs: ${role}` `` assembled at
  the call site cannot be reordered by a translator; a function can.
- **Keep the host's `useI18n` shim** (007 traps) — when we do extract, we plug into the
  host's locale rather than shipping a second mechanism.
- **No dates or numbers formatted by hand.** `Intl.DateTimeFormat` / `Intl.NumberFormat`
  with the host locale. Money via `Intl.NumberFormat` with the currency from
  `budget.currency` (012).

Pluralisation: use `Intl.PluralRules` even in English-only. `"1 tickets"` is the kind of
detail that makes a product feel unfinished, and the fix is one helper.

RTL is out of scope until a locale needs it — but avoid `margin-left` where
`margin-inline-start` works. Costs nothing now, saves a full CSS pass later.

---

## 3. The UX quality bar

Not covered anywhere else, and each of these is a decision someone will otherwise make
inconsistently at 5pm.

| Rule | Why |
|---|---|
| **Every destructive action confirms, naming the thing** | "Delete project acme" not "Are you sure?" |
| **Every async action shows its state** | Button → spinner → result. Never a dead-looking button |
| **Every failure says what to do** | "Couldn't finalize: 2 tickets have no acceptance criteria" + links. Not "Error 400" |
| **Disabled beats hidden, with a reason** | 006 §5: a CFO who cannot see Approve assumes it's broken; one who sees "Requires CEO" reaches for Escalate |
| **Optimistic updates reconcile** | Card moves and role changes apply immediately, revert with a toast on failure |
| **Nothing blocks on a model call** | An agent turn is 30 s+. The UI shows it running; it never waits |
| **Relative time everywhere, absolute on hover** | "42m ago", title="2026-08-05 14:12 IST" |
| **Empty ≠ loading ≠ error** | Three distinct states, three distinct renderings. Conflating them is why users refresh |
| **Deep links work** | Every ticket, thread, approval and escalation has a URL that survives a reload. Notifications (016) depend on this |
| **Density stays** | 007 traps. Loosening padding is the fastest way to stop looking like the ERP |

### Error copy

Backend errors are structured (`REFERENCE-api-and-tools.md` §7) precisely so the UI can
render them usefully. Map each `error` code to a sentence and an action:

```
finalize_refused  → "Can't finalize yet: {reasons}."          [Show tickets]
forbidden         → "This needs {required_role}."             [Escalate ↑]
scope_violation   → "That file is outside this project."      —
unknown_mention   → "No @{handle} on this project."           [Show handles]
```

The `forbidden` → Escalate mapping is the one that matters: it turns a dead end into the
next step, and it is requirement #8's flow surfacing at the exact moment a person hits
the wall.

---

## 4. Tests

```
tests/plugins/test_pmo_a11y.py
  test_every_column_move_has_non_drag_path
  test_drawer_traps_and_restores_focus
  test_mention_autocomplete_is_a_combobox
  test_status_conveyed_by_text_not_only_colour
  test_contrast_pairs_pass_in_both_themes
  test_reduced_motion_respected

tests/plugins/test_pmo_strings.py
  test_no_string_literals_in_render_functions   # source grep
  test_interpolation_uses_functions
  test_dates_and_money_use_intl
```

`test_no_string_literals_in_render_functions` is a grep over the bundle. It is the whole
reason the day-1 touchpoint is cheap: enforced from the first commit, it never needs a
cleanup pass.

## P0 (day-1 touchpoint) / P1

**Day 1:** `STRINGS` + `t()`; keep the per-card move control; keep focus outlines; three
distinct empty/loading/error states.

**P1:** full keyboard pass; combobox semantics on the autocomplete; live regions; contrast
audit in both themes; `Intl` formatting; reduced motion; the error-copy map; deep links.

## Traps

- Copying the ERP's visuals without its move-control commitment.
- `outline: none` anywhere.
- A custom `@`-autocomplete with no keyboard support — it is the only comms channel in the
  product.
- `aria-live="assertive"` on a busy board.
- A half-populated locale file. Worse than English-only.
- Status conveyed by tint alone.
- Conflating empty, loading and error.
