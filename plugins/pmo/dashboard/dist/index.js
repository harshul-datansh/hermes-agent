/**
 * Hermes Kanban — Dashboard Plugin
 *
 * Board view for the multi-agent collaboration board backed by
 * ~/.hermes/kanban.db. Calls the plugin's backend at /api/plugins/pmo/
 * and tails task_events over a WebSocket for live updates.
 *
 * Plain IIFE, no build step. Uses window.__HERMES_PLUGIN_SDK__ for React +
 * shadcn primitives; HTML5 drag-and-drop for card movement on desktop and
 * a pointer-based fallback for touch.
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React } = SDK;
  const h = React.createElement;
  const {
    Card, CardContent,
    Badge, Button, Input, Label, Select, SelectOption,
  } = SDK.components;
  const { useState, useEffect, useCallback, useMemo, useRef } = SDK.hooks;
  const { cn, timeAgo } = SDK.utils;

  // Newer host dashboards expose a DS-styled Checkbox on the plugin SDK.
  // Fall back to a native <input type="checkbox"> shim so older hosts that
  // predate the design-system rollout still render. The shim normalises
  // Radix's onCheckedChange(checked) signature to native onChange(event).
  const Checkbox = SDK.components.Checkbox || function (props) {
    const { checked, onCheckedChange, className, onClick, ...rest } = props;
    return h("input", Object.assign({
      type: "checkbox",
      checked: !!checked,
      className: className,
      onClick: onClick,
      onChange: function (e) {
        if (onCheckedChange) onCheckedChange(e.target.checked);
      },
    }, rest));
  };

  // useI18n is a hook each component calls locally. Older host dashboards
  // may not expose it yet; fall back to a shim so the bundle still renders
  // English against an older host SDK. English fallback strings live
  // alongside each call site (passed as the third arg of tx()).
  const useI18n = SDK.useI18n || function () { return { t: { kanban: null }, locale: "en" }; };

  // Resolve a translation by dotted path under the kanban namespace
  // (e.g. "columnLabels.triage"); fall back to the English string passed in.
  function tx(t, path, fallback, vars) {
    let node = t && t.kanban;
    if (node) {
      const parts = path.split(".");
      for (let i = 0; i < parts.length; i++) {
        if (node && typeof node === "object" && parts[i] in node) {
          node = node[parts[i]];
        } else { node = null; break; }
      }
    }
    let str = (typeof node === "string") ? node : fallback;
    if (vars) {
      for (const k in vars) {
        str = str.replace(new RegExp("\\{" + k + "\\}", "g"), vars[k]);
      }
    }
    return str;
  }

  // ``fetchJSON`` throws ``Error("<status>: <raw body>")`` on non-2xx, and
  // FastAPI bodies look like ``{"detail":"<message>"}``.  Pull the
  // human-readable message out so banners/toasts don't have to leak HTTP
  // plumbing at the user (e.g. ``409: {"detail":"…"}``).  See #26744.
  function parseApiErrorMessage(err) {
    const raw = (err && err.message) ? String(err.message) : String(err || "");
    const m = raw.match(/^(\d{3}):\s*(.*)$/s);
    const body = m ? m[2] : raw;
    try {
      const parsed = JSON.parse(body);
      if (parsed && typeof parsed.detail === "string") return parsed.detail;
      if (parsed && parsed.detail && typeof parsed.detail.message === "string") {
        return parsed.detail.message;
      }
    } catch (_e) { /* not JSON — fall through to raw body */ }
    return body || raw;
  }

  // Order matches BOARD_COLUMNS in plugin_api.py.
  const COLUMN_ORDER = ["triage", "todo", "ready", "running", "blocked", "done"];
  // English fallback dictionaries — used when the i18n catalog is missing
  // a key, and as defaults for the get*() helpers below so callers running
  // outside any React component (where there's no `t`) still get sane text.
  // Every status in kanban_db.VALID_STATUSES needs an entry here. A missing
  // key falls through to the raw status string, which is why `review`
  // previously rendered as a lowercase, description-less column next to
  // title-cased siblings.
  const FALLBACK_COLUMN_LABEL = {
    triage: "Triage",
    todo: "Todo",
    scheduled: "Scheduled",
    ready: "Ready",
    running: "In Progress",
    blocked: "Blocked",
    review: "Review",
    done: "Done",
    archived: "Archived",
  };
  const FALLBACK_COLUMN_HELP = {
    triage: "Raw ideas — a specifier will flesh out the spec",
    todo: "Waiting on dependencies or unassigned",
    scheduled: "Waiting on a known time delay or scheduled follow-up",
    ready: "Dependencies satisfied; assign a profile to dispatch",
    running: "Claimed by a worker — in-flight",
    blocked: "Worker asked for human input",
    review: "Awaiting reviewer sign-off before it can be marked done",
    done: "Completed",
    archived: "Archived",
  };
  const FALLBACK_DESTRUCTIVE = {
    done: "Mark this task as done? The worker's claim is released and dependent children become ready.",
    archived: "Archive this task? It disappears from the default board view.",
    blocked: "Mark this task as blocked? The worker's claim is released.",
  };
  const FALLBACK_DIAGNOSTIC_EVENT_LABELS = {
    completion_blocked_hallucination: "⚠ Completion blocked — phantom card ids",
    suspected_hallucinated_references: "⚠ Prose referenced phantom card ids",
  };
  const FALLBACK_TRASH = {
    label: "Trash",
    title: "Drag a card here to permanently delete it",
    confirm: "Permanently delete this task? This cannot be undone.",
    dropHint: "Drop to delete",
  };
  const DIAGNOSTIC_EVENT_KIND_KEYS = {
    completion_blocked_hallucination: "completionBlockedHallucination",
    suspected_hallucinated_references: "suspectedHallucinatedReferences",
  };
  const DESTRUCTIVE_KEYS = {
    done: "confirmDone",
    archived: "confirmArchive",
    blocked: "confirmBlocked",
  };

  function getColumnLabel(t, status) {
    return tx(t, "columnLabels." + status, FALLBACK_COLUMN_LABEL[status] || status);
  }
  function getColumnHelp(t, status) {
    return tx(t, "columnHelp." + status, FALLBACK_COLUMN_HELP[status] || "");
  }
  function getDestructiveConfirm(t, status) {
    const key = DESTRUCTIVE_KEYS[status];
    if (!key) return null;
    return tx(t, key, FALLBACK_DESTRUCTIVE[status]);
  }
  function getDiagnosticEventLabel(t, kind) {
    const key = DIAGNOSTIC_EVENT_KIND_KEYS[kind];
    if (!key) return null;
    return tx(t, key, FALLBACK_DIAGNOSTIC_EVENT_LABELS[kind]);
  }

  const COLUMN_DOT = {
    triage: "hermes-kanban-dot-triage",
    todo: "hermes-kanban-dot-todo",
    ready: "hermes-kanban-dot-ready",
    running: "hermes-kanban-dot-running",
    blocked: "hermes-kanban-dot-blocked",
    done: "hermes-kanban-dot-done",
    archived: "hermes-kanban-dot-archived",
  };

  function isDiagnosticEvent(kind) {
    return Object.prototype.hasOwnProperty.call(FALLBACK_DIAGNOSTIC_EVENT_LABELS, kind);
  }

  function phantomIdsFromEvent(ev) {
    if (!ev || !ev.payload) return [];
    const p = ev.payload;
    return p.phantom_cards || p.phantom_refs || [];
  }

  // Takes an optional `t` so the prompt/alert text is localised. Callers
  // outside React components can pass null and fall through to English.
  function withCompletionSummary(patch, count, t) {
    if (!patch || patch.status !== "done") return patch;
    const label = count && count > 1 ? `${count} selected task(s)` : "this task";
    const value = window.prompt(
      tx(t, "completionSummary",
        "Completion summary for {label}. This is stored as the task result.",
        { label: label }),
      "",
    );
    if (value === null) return null;
    const summary = value.trim();
    if (!summary) {
      window.alert(tx(t, "completionSummaryRequired",
        "Completion summary is required before marking a task done."));
      return null;
    }
    return Object.assign({}, patch, { result: summary, summary });
  }

  const API = "/api/plugins/pmo";
  const MIME_TASK = "text/x-hermes-task";

  // Docs link — surfaced as a `?` icon next to the board switcher and as
  // `title=` hints on unlabelled controls. Kept in one place so rebrands or
  // path changes are a single edit.
  const DOCS_URL = "https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban";
  const DOCS_TUTORIAL_URL = "https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban-tutorial";

  // localStorage key for the user's selected board. Independent of the
  // CLI's on-disk ``<root>/kanban/current`` pointer so browser users
  // can inspect any board without shifting the CLI's active board out
  // from under a terminal they left open.
  const LS_BOARD_KEY = "hermes.pmo.selectedBoard";
  const PMO_PROJECT_BY_BOARD = Object.create(null);

  function rememberProjectBoards(projects) {
    (Array.isArray(projects) ? projects : []).forEach(function (project) {
      if (project && project.board_slug && project.project_id) {
        PMO_PROJECT_BY_BOARD[project.board_slug] = project.project_id;
      }
    });
  }

  function withProject(url, projectId) {
    if (!projectId || /(?:\?|&)project_id=/.test(url)) return url;
    const sep = url.indexOf("?") >= 0 ? "&" : "?";
    return `${url}${sep}project_id=${encodeURIComponent(projectId)}`;
  }

  function readSelectedBoard() {
    try {
      const v = window.localStorage.getItem(LS_BOARD_KEY);
      return (v || "").trim() || null;
    } catch (_e) { return null; }
  }

  function writeSelectedBoard(slug) {
    try {
      // Persist the user's dashboard-side board pin even for "default".
      // Previously this stripped "default" to keep localStorage empty,
      // but the fetch layer read that absence as "no opinion" and fell
      // through to the server-side ``current`` file — which the board
      // switcher also writes. Result: selecting the default tab after
      // creating a new board with "switch" checked showed the new
      // board's (wrong) data because the URL omitted ``?board=`` and
      // the backend happily returned whichever board was "current".
      // Persisting every selection keeps the dashboard's board opinion
      // independent of the CLI's active board, which was the original
      // design intent. Regression: #20879.
      if (slug) window.localStorage.setItem(LS_BOARD_KEY, slug);
      else window.localStorage.removeItem(LS_BOARD_KEY);
      window.dispatchEvent(new CustomEvent("pmo:board-selected", {
        detail: { board: slug || null },
      }));
    } catch (_e) { /* ignore quota / private mode */ }
  }

  function withBoard(url, board) {
    // Always append ?board=<slug> when we have one picked — including
    // "default". Omitting the param would fall through to the backend's
    // resolution chain (env var → ``current`` file → default), which
    // means the dashboard's tab selection gets silently overridden by
    // whatever board the CLI or "switch" checkbox last activated.
    // Regression: #20879.
    if (!board) return url;
    const sep = url.indexOf("?") >= 0 ? "&" : "?";
    return withProject(
      `${url}${sep}board=${encodeURIComponent(board)}`,
      PMO_PROJECT_BY_BOARD[board],
    );
  }

  // The SDK's Select component fires ``onValueChange(value)`` directly
  // (it's a shadcn-style popup, not a native <select>). Older plugin
  // code calls ``onChange({target: {value}})`` which silently never
  // fires. This helper wires both signatures so a setter works with
  // either API — use it as:
  //
  //   h(Select, {..., ...selectChangeHandler(setState), ...})
  function selectChangeHandler(setter) {
    return {
      onValueChange: function (v) { setter(v == null ? "" : v); },
      onChange: function (e) {
        const v = e && e.target ? e.target.value : e;
        setter(v == null ? "" : v);
      },
    };
  }

  // -------------------------------------------------------------------------
  // Minimal safe markdown renderer.
  //
  // Recognises a small subset (headings, bold, italic, inline code, fenced
  // code, links, bullet lists, paragraphs). HTML escaping first, then
  // inline replacements against the escaped string — no raw HTML from the
  // user is ever executed.
  // -------------------------------------------------------------------------

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
  function renderInline(esc) {
    // Fenced code has already been extracted before this runs; process
    // inline replacements on the escaped string.
    return esc
      // inline code
      .replace(/`([^`\n]+)`/g, (_m, c) => `<code>${c}</code>`)
      // bold
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      // italic
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      // safe links — only http(s) and mailto
      .replace(
        /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/g,
        (_m, text, href) =>
          `<a href="${href}" target="_blank" rel="noopener noreferrer">${text}</a>`,
      );
  }
  function renderMarkdown(src) {
    if (!src) return "";
    // Split out fenced code blocks first so their contents aren't mangled.
    const blocks = [];
    let working = String(src).replace(/```([\s\S]*?)```/g, (_m, code) => {
      blocks.push(code);
      return `\u0000CODE${blocks.length - 1}\u0000`;
    });
    const escaped = escapeHtml(working);
    const lines = escaped.split(/\r?\n/);
    const out = [];
    let inList = false;
    for (const raw of lines) {
      const line = raw;
      const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
      const heading = /^(#{1,4})\s+(.*)$/.exec(line);
      if (bullet) {
        if (!inList) { out.push("<ul>"); inList = true; }
        out.push(`<li>${renderInline(bullet[1])}</li>`);
        continue;
      }
      if (inList) { out.push("</ul>"); inList = false; }
      if (heading) {
        const level = heading[1].length;
        out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      } else if (line.trim() === "") {
        out.push("");
      } else {
        out.push(`<p>${renderInline(line)}</p>`);
      }
    }
    if (inList) out.push("</ul>");
    let html = out.join("\n");
    // Re-insert fenced code blocks.
    html = html.replace(/\u0000CODE(\d+)\u0000/g, (_m, i) =>
      `<pre class="hermes-kanban-md-code"><code>${escapeHtml(blocks[Number(i)])}</code></pre>`,
    );
    return html;
  }
  const MARKDOWN_ALLOWED_TAGS = new Set([
    "a",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "li",
    "p",
    "pre",
    "strong",
    "ul",
  ]);
  function escapeAttribute(value) {
    return escapeHtml(value).replace(/`/g, "&#96;");
  }
  function sanitizeMarkdownAttrs(tag, attrs) {
    if (tag === "a") {
      const hrefMatch =
        /\shref=(["'])(.*?)\1/i.exec(attrs) ||
        /\shref=([^\s>]+)/i.exec(attrs);
      const href = hrefMatch ? (hrefMatch[2] || hrefMatch[1] || "").trim() : "";
      if (!/^(https?:\/\/|mailto:|#pmo\/)/i.test(href)) return "";
      const external = /^(https?:\/\/|mailto:)/i.test(href);
      return ` href="${escapeAttribute(href)}"${external ? " target=\"_blank\" rel=\"noopener noreferrer\"" : ""}`;
    }
    if (tag === "pre" && /\sclass=(["'])hermes-kanban-md-code\1/i.test(attrs)) {
      return ' class="hermes-kanban-md-code"';
    }
    return "";
  }
  function sanitizeMarkdownHtml(html) {
    return String(html || "").replace(
      /<\/?([a-zA-Z][A-Za-z0-9-]*)([^>]*)>/g,
      (match, rawTag, attrs) => {
        const tag = rawTag.toLowerCase();
        if (!MARKDOWN_ALLOWED_TAGS.has(tag)) return "";
        if (/^<\s*\//.test(match)) return `</${tag}>`;
        return `<${tag}${sanitizeMarkdownAttrs(tag, attrs || "")}>`;
      },
    );
  }

  function MarkdownBlock(props) {
    const enabled = props.enabled !== false;
    if (!enabled) {
      return h("pre", { className: "hermes-kanban-pre" }, props.source || "");
    }
    return h("div", {
      className: "hermes-kanban-md",
      dangerouslySetInnerHTML: { __html: sanitizeMarkdownHtml(renderMarkdown(props.source || "")) },
    });
  }

  // -------------------------------------------------------------------------
  // Touch drag-drop helper.
  //
  // HTML5 DnD is desktop-only. On touch devices we attach a pointerdown
  // handler that simulates a drag proxy and fires a custom event on the
  // column under the finger when released. Columns listen for both the
  // standard `drop` event and our `hermes-kanban:drop` event.
  // -------------------------------------------------------------------------

  function attachTouchDrag(el, taskId) {
    if (!el) return;
    function onDown(e) {
      if (e.pointerType !== "touch") return;
      e.preventDefault();
      const proxy = el.cloneNode(true);
      proxy.classList.add("hermes-kanban-touch-proxy");
      document.body.appendChild(proxy);
      let lastTarget = null;

      function move(ev) {
        proxy.style.left = `${ev.clientX - proxy.offsetWidth / 2}px`;
        proxy.style.top = `${ev.clientY - 24}px`;
        proxy.style.display = "none";
        const under = document.elementFromPoint(ev.clientX, ev.clientY);
        proxy.style.display = "";
        const col = under && under.closest && under.closest("[data-kanban-column]");
        const trash = under && under.closest && under.closest("[data-kanban-trash]");
        const target = col || trash;
        if (target !== lastTarget) {
          if (lastTarget) lastTarget.classList.remove("hermes-kanban-column--drop");
          if (target) target.classList.add("hermes-kanban-column--drop");
          lastTarget = target;
        }
      }
      function up() {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", up);
        document.removeEventListener("pointercancel", up);
        if (lastTarget) {
          lastTarget.classList.remove("hermes-kanban-column--drop");
          const status = lastTarget.getAttribute("data-kanban-column");
          const isTrash = lastTarget.hasAttribute("data-kanban-trash");
          if (isTrash) {
            lastTarget.dispatchEvent(new CustomEvent("hermes-kanban:delete", {
              detail: { taskId },
              bubbles: true,
            }));
          } else if (status) {
            lastTarget.dispatchEvent(new CustomEvent("hermes-kanban:drop", {
              detail: { taskId, status },
              bubbles: true,
            }));
          }
        }
        proxy.remove();
      }
      // Kick off proxy at the pointer origin.
      proxy.style.position = "fixed";
      proxy.style.pointerEvents = "none";
      proxy.style.opacity = "0.85";
      proxy.style.zIndex = "9999";
      proxy.style.width = `${el.offsetWidth}px`;
      proxy.style.left = `${e.clientX - el.offsetWidth / 2}px`;
      proxy.style.top = `${e.clientY - 24}px`;
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
      document.addEventListener("pointercancel", up);
    }
    el.addEventListener("pointerdown", onDown);
    return function () { el.removeEventListener("pointerdown", onDown); };
  }

  // -------------------------------------------------------------------------
  // Error boundary
  // -------------------------------------------------------------------------

  // Wrap the boundary's fallback in a tiny function component so we can
  // call useI18n() — class components can't use hooks directly.
  function ErrorBoundaryFallback(props) {
    const { t } = useI18n();
    return h(Card, null,
      h(CardContent, { className: "p-6 text-sm" },
        h("div", { className: "text-destructive font-semibold mb-1" },
          tx(t, "renderingError", "Kanban tab hit a rendering error")),
        h("div", { className: "text-muted-foreground text-xs mb-3" },
          props.message),
        h(Button, {
          onClick: props.onReset,
          size: "sm",
        }, tx(t, "reloadView", "Reload view")),
      ),
    );
  }

  class ErrorBoundary extends React.Component {
    constructor(props) { super(props); this.state = { error: null }; }
    static getDerivedStateFromError(error) { return { error }; }
    componentDidCatch(error, info) {
      // eslint-disable-next-line no-console
      console.error("Kanban plugin crashed:", error, info);
    }
    render() {
      if (this.state.error) {
        return h(ErrorBoundaryFallback, {
          message: String(this.state.error && this.state.error.message || this.state.error),
          onReset: () => this.setState({ error: null }),
        });
      }
      return this.props.children;
    }
  }

  // -------------------------------------------------------------------------
  // Root page
  // -------------------------------------------------------------------------

  const PMO_COLUMN_PAGE_SIZE = 50;
  function pageColumnTasks(tasks, limit) {
    const bounded = Math.max(
      PMO_COLUMN_PAGE_SIZE,
      Number(limit) || PMO_COLUMN_PAGE_SIZE,
    );
    return {
      tasks: (tasks || []).slice(0, bounded),
      total: (tasks || []).length,
      hasMore: (tasks || []).length > bounded,
      nextLimit: bounded + PMO_COLUMN_PAGE_SIZE,
    };
  }

  function KanbanPage(props) {
    const { t } = useI18n();
    const [board, setBoard] = useState(() => readSelectedBoard() || null);
    const [boardList, setBoardList] = useState([]);      // [{slug, name, counts, ...}]
    const [showNewBoard, setShowNewBoard] = useState(false);
    const [showBoardSettings, setShowBoardSettings] = useState(false);

    // The copied Kanban keeps its own board state. Synchronize it with the
    // project context bar; otherwise the parent can correctly switch projects
    // while this child keeps fetching its stale board under the new project_id
    // and receives a project-boundary 403.
    useEffect(function () {
      if (!props.board || props.board === board) return;
      setBoard(props.board);
    }, [props.board, board]);

    const [kanbanBoard, setKanbanBoard] = useState(null);  // the grid data
    // Alias so the rest of the function can keep using `board` semantically
    // for the grid data (card columns + tenants + assignees) without
    // colliding with the selected-board slug above. History: the old
    // component had `const [board, setBoard]` for the grid data. We
    // renamed the grid data to `kanbanBoard` so the more useful name
    // (`board`) belongs to the selected slug.
    const boardData = kanbanBoard;
    const setBoardData = setKanbanBoard;
    const [config, setConfig] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    const [tenantFilter, setTenantFilter] = useState("");
    const [assigneeFilter, setAssigneeFilter] = useState("");
    const [includeArchived, setIncludeArchived] = useState(false);
    const [search, setSearch] = useState("");
    const [laneByProfile, setLaneByProfile] = useState(true);
    const [configApplied, setConfigApplied] = useState(false);
    const [columnLimits, setColumnLimits] = useState({});

    const [selectedTaskId, setSelectedTaskId] = useState(function () {
      try { return window.localStorage.getItem("pmo:open-task") || null; } catch (_error) { return null; }
    });
    useEffect(function () {
      if (!selectedTaskId) return;
      try { window.localStorage.removeItem("pmo:open-task"); } catch (_error) { /* best effort */ }
    }, [selectedTaskId]);
    const taskTriggerRef = useRef(null);
    const openTask = useCallback(function (taskId) {
      taskTriggerRef.current = document.activeElement;
      setSelectedTaskId(taskId);
    }, []);
    const closeTask = useCallback(function () {
      setSelectedTaskId(null);
      const trigger = taskTriggerRef.current;
      taskTriggerRef.current = null;
      window.requestAnimationFrame(function () {
        if (trigger && typeof trigger.focus === "function" && document.contains(trigger)) {
          trigger.focus();
        }
      });
    }, []);
    const [selectedIds, setSelectedIds] = useState(() => new Set());
    const [lastSelectedId, setLastSelectedId] = useState(null);
    const [failedIds, setFailedIds] = useState(() => new Set());
    const [draggingTaskId, setDraggingTaskId] = useState(null);
    const handleDragStart = useCallback(function (taskId) { setDraggingTaskId(taskId); }, []);
    const handleDragEnd = useCallback(function () { setDraggingTaskId(null); }, []);
    // Per-task event counter incremented whenever the WS stream reports
    // a new event for that task id. TaskDrawer useEffect-depends on its
    // own task's counter so it reloads itself on live events instead of
    // showing stale data.
    const [taskEventTick, setTaskEventTick] = useState({});

    const cursorRef = useRef(0);
    const reloadTimerRef = useRef(null);
    const wsRef = useRef(null);
    const wsBackoffRef = useRef(1000);
    const wsClosedRef = useRef(false);

    // --- load config once ---------------------------------------------------
    useEffect(function () {
      SDK.fetchJSON(withBoard(`${API}/config`, board))
        .then(function (c) {
          setConfig(c);
          if (!configApplied) {
            if (c.default_tenant) setTenantFilter(c.default_tenant);
            if (typeof c.lane_by_profile === "boolean") setLaneByProfile(c.lane_by_profile);
            if (typeof c.include_archived_by_default === "boolean") setIncludeArchived(c.include_archived_by_default);
            setConfigApplied(true);
          }
        })
        .catch(function () { setConfig({ render_markdown: true }); });
    }, []);  // eslint-disable-line react-hooks/exhaustive-deps

    // --- fetch full board ---------------------------------------------------
    const loadBoard = useCallback(() => {
      const qs = new URLSearchParams();
      if (tenantFilter) qs.set("tenant", tenantFilter);
      if (includeArchived) qs.set("include_archived", "true");
      const url = qs.toString() ? `${API}/board?${qs}` : `${API}/board`;
      return SDK.fetchJSON(withBoard(url, board))
        .then(function (data) {
          setBoardData(data);
          cursorRef.current = data.latest_event_id || 0;
          setError(null);
        })
        .catch(function (err) {
          setError(String(err && err.message ? err.message : err));
        })
        .finally(function () { setLoading(false); });
    }, [tenantFilter, includeArchived, board]);

    // --- load list of boards for the switcher ------------------------------
    const loadBoardList = useCallback(function () {
      return SDK.fetchJSON(withBoard(`${API}/boards`, board))
        .then(function (data) {
          const boards = (data && data.boards) || [];
          const storedBoard = readSelectedBoard();
          setBoardList(boards);
          if (!storedBoard && !board && data && (data.current || boards[0])) {
            const first = data.current || (boards[0] && boards[0].slug);
            setBoard(first);
            writeSelectedBoard(first);
            return;
          }
          // If the stored slug isn't in the list any longer (board was
          // deleted in the CLI while dashboard was open), fall back to
          // default so the UI doesn't hang on a 404.
          if (board && !boards.find(function (b) { return b.slug === board; })) {
            const fallback = (data.current && boards.find(function (b) { return b.slug === data.current; })) || boards[0];
            const nextBoard = fallback ? fallback.slug : null;
            setBoard(nextBoard);
            writeSelectedBoard(nextBoard || "");
          }
        })
        .catch(function () { /* non-fatal */ });
    }, [board]);

    useEffect(function () { loadBoardList(); }, [loadBoardList]);

    const scheduleReload = useCallback(function () {
      if (reloadTimerRef.current) return;
      reloadTimerRef.current = setTimeout(function () {
        reloadTimerRef.current = null;
        loadBoard();
      }, 250);
    }, [loadBoard]);

    useEffect(function () {
      loadBoard();
      return function () {
        if (reloadTimerRef.current) {
          clearTimeout(reloadTimerRef.current);
          reloadTimerRef.current = null;
        }
      };
    }, [loadBoard]);

    // --- WebSocket ---------------------------------------------------------
    useEffect(function () {
      if (!boardData) return undefined;
      wsClosedRef.current = false;
      function openWs() {
        if (wsClosedRef.current) return;
        // Build the WS URL via the host SDK so the correct auth param is used
        // in BOTH modes: single-use ?ticket= in gated OAuth mode, ?token= in
        // loopback. Reading window.__HERMES_SESSION_TOKEN__ directly (the old
        // path) sends an empty token and is rejected in gated mode. buildWsUrl
        // also applies the dashboard base-path prefix for reverse-proxied
        // deployments, which the old inline URL did not. It's async (gated
        // mode mints a fresh ticket per connect), so resolve then open.
        const wsParams = { since: String(cursorRef.current || 0) };
        // Pin the WS stream to the currently-selected board so events
        // from other boards don't bleed in. Includes "default" so the
        // dashboard's own board pin always wins over the server-side
        // ``current`` file — same rationale as ``withBoard()`` above.
        // Regression: #20879.
        if (board) wsParams.board = board;
        SDK.buildWsUrl(`${API}/events`, wsParams).then(function (url) {
          if (wsClosedRef.current) return;
          let ws;
          try { ws = new WebSocket(url); } catch (_e) { return; }
          wsRef.current = ws;
          ws.onopen = function () { wsBackoffRef.current = 1000; };
          ws.onmessage = function (ev) {
            try {
              const msg = JSON.parse(ev.data);
              if (msg && Array.isArray(msg.events) && msg.events.length > 0) {
                cursorRef.current = msg.cursor || cursorRef.current;
                // Stamp per-task signal so the TaskDrawer can reload itself.
                setTaskEventTick(function (prev) {
                  const next = Object.assign({}, prev);
                  for (const e of msg.events) {
                    if (e && e.task_id) next[e.task_id] = (next[e.task_id] || 0) + 1;
                  }
                  return next;
                });
                scheduleReload();
              }
            } catch (_e) { /* ignore */ }
          };
          ws.onclose = function (ev) {
            if (wsClosedRef.current) return;
            if (ev && ev.code === 1008) {
              setError(tx(t, "wsAuthFailed",
                "WebSocket auth failed — reload the page to refresh the session token."));
              return;
            }
            const delay = Math.min(wsBackoffRef.current, 30000);
            wsBackoffRef.current = Math.min(wsBackoffRef.current * 2, 30000);
            setTimeout(openWs, delay);
          };
        }).catch(function () {
          // Ticket mint / URL build failed (e.g. session expired). Back off
          // and retry; a hard auth failure surfaces via the 1008 close path.
          if (wsClosedRef.current) return;
          const delay = Math.min(wsBackoffRef.current, 30000);
          wsBackoffRef.current = Math.min(wsBackoffRef.current * 2, 30000);
          setTimeout(openWs, delay);
        });
      }
      openWs();
      return function () {
        wsClosedRef.current = true;
        try { wsRef.current && wsRef.current.close(); } catch (_e) { /* noop */ }
      };
    }, [!!boardData, board, scheduleReload]);

    // --- filtering ----------------------------------------------------------
    const filteredBoard = useMemo(function () {
      if (!boardData) return null;
      const q = search.trim().toLowerCase();
      const filterTask = function (t) {
        if (String(t.body || "").indexOf('"schema":"datansh-pmo-conversation.v1"') >= 0) return false;
        if (tenantFilter && t.tenant !== tenantFilter) return false;
        if (assigneeFilter && t.assignee !== assigneeFilter) return false;
        if (q) {
          const hay = `${t.id} ${t.title || ""} ${t.body || ""} ${t.result || ""} ${t.latest_summary || ""} ${t.assignee || ""} ${t.tenant || ""}`.toLowerCase();
          if (hay.indexOf(q) === -1) return false;
        }
        return true;
      };
      return Object.assign({}, boardData, {
        columns: boardData.columns.map(function (col) {
          const page = pageColumnTasks(
            col.tasks.filter(filterTask),
            columnLimits[col.name] || PMO_COLUMN_PAGE_SIZE,
          );
          return Object.assign({}, col, {
            tasks: page.tasks,
            total_filtered: page.total,
            has_more: page.hasMore,
          });
        }),
      });
    }, [boardData, tenantFilter, assigneeFilter, search, columnLimits]);

    const loadMoreColumn = useCallback(function (columnName) {
      setColumnLimits(function (limits) {
        const current = limits[columnName] || PMO_COLUMN_PAGE_SIZE;
        return Object.assign({}, limits, {
          [columnName]: current + PMO_COLUMN_PAGE_SIZE,
        });
      });
    }, []);

    // --- actions ------------------------------------------------------------
    const moveTask = useCallback(function (taskId, newStatus) {
      const confirmMsg = getDestructiveConfirm(t, newStatus);
      if (confirmMsg && !window.confirm(confirmMsg)) return;
      const patch = withCompletionSummary({ status: newStatus }, 1, t);
      if (!patch) return;
      setBoardData(function (b) {
        if (!b) return b;
        let moved = null;
        const columns = b.columns.map(function (col) {
          const next = col.tasks.filter(function (t) {
            if (t.id === taskId) { moved = Object.assign({}, t, { status: newStatus }); return false; }
            return true;
          });
          return Object.assign({}, col, { tasks: next });
        });
        if (moved) {
          const dest = columns.find(function (c) { return c.name === newStatus; });
          if (dest) dest.tasks = [moved].concat(dest.tasks);
        }
        return Object.assign({}, b, { columns });
      });
      SDK.fetchJSON(withBoard(`${API}/tasks/${encodeURIComponent(taskId)}`, board), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      }).catch(function (err) {
        setError(tx(t, "moveFailed", "Move failed: ") + parseApiErrorMessage(err));
        loadBoard();
      });
    }, [loadBoard, board, t]);

    const clearSelected = useCallback(function () {
      setSelectedIds(new Set());
      setLastSelectedId(null);
      setFailedIds(new Set());
    }, []);
    const moveSelected = useCallback(function (newStatus) {
      const confirmMsg = DESTRUCTIVE_TRANSITIONS[newStatus];
      if (confirmMsg && !window.confirm(confirmMsg)) return;
      if (selectedIds.size === 0) return;
      const patch = withCompletionSummary({ status: newStatus }, selectedIds.size);
      if (!patch) return;
      const ids = Array.from(selectedIds);
      // Optimistic UI: remove selected from all columns and prepend to target.
      setBoardData(function (b) {
        if (!b) return b;
        const moved = [];
        const columns = b.columns.map(function (col) {
          const kept = [];
          for (const t of col.tasks) {
            if (selectedIds.has(t.id)) moved.push(Object.assign({}, t, { status: newStatus }));
            else kept.push(t);
          }
          return Object.assign({}, col, { tasks: kept });
        });
        const dest = columns.find(function (c) { return c.name === newStatus; });
        if (dest) dest.tasks = moved.concat(dest.tasks);
        return Object.assign({}, b, { columns });
      });
      SDK.fetchJSON(withBoard(`${API}/tasks/bulk`, board), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({ ids }, patch)),
      }).then(function (res) {
        const failed = (res.results || []).filter(function (r) { return !r.ok; });
        if (failed.length > 0) {
          setError(`Bulk move: ${failed.length} of ${res.results.length} failed`);
          setFailedIds(new Set(failed.map(function (f) { return f.id; })));
        } else {
          setFailedIds(new Set());
        }
        setSelectedIds(new Set());
        setLastSelectedId(null);
        loadBoard();
      }).catch(function (err) {
        setError(`Move failed: ${err.message || err}`);
        setFailedIds(new Set(selectedIds));
        loadBoard();
      });
    }, [selectedIds, loadBoard, board]);

    const createTask = useCallback(function (body) {
      return SDK.fetchJSON(withBoard(`${API}/tasks`, board), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (res) {
        // Surface dispatcher-presence warnings (e.g. "no gateway is
        // running") via the existing error banner channel. Not fatal —
        // the task was created successfully — but the user should know
        // their ready task will sit idle until the gateway is up.
        if (res && res.warning) {
          setError(tx(t, "taskCreatedWarning", "Task created, but: ") + res.warning);
        }
        loadBoard();
        loadBoardList();  // refresh counts in the switcher
        return res;
      });
    }, [loadBoard, loadBoardList, board, t]);

    const toggleSelected = useCallback(function (id, additive) {
      setSelectedIds(function (prev) {
        const next = new Set(additive ? prev : []);
        if (prev.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
      setLastSelectedId(id);
      setFailedIds(function (prev) {
        if (prev.has(id)) {
          const next = new Set(prev);
          next.delete(id);
          return next;
        }
        return prev;
      });
    }, []);

    const toggleRange = useCallback(function (toId) {
      // Build flat visible task order from filteredBoard columns.
      setSelectedIds(function (prev) {
        const next = new Set(prev);
        if (!filteredBoard || !filteredBoard.columns) return next;
        const order = [];
        for (const col of filteredBoard.columns) {
          for (const t of col.tasks || []) order.push(t.id);
        }
        const anchor = lastSelectedId;
        if (!anchor || anchor === toId) {
          next.add(toId);
          return next;
        }
        const aIdx = order.indexOf(anchor);
        const bIdx = order.indexOf(toId);
        if (aIdx === -1 || bIdx === -1) {
          next.add(toId);
          return next;
        }
        const lo = Math.min(aIdx, bIdx);
        const hi = Math.max(aIdx, bIdx);
        for (let i = lo; i <= hi; i++) next.add(order[i]);
        return next;
      });
      setLastSelectedId(toId);
    }, [filteredBoard, lastSelectedId]);

    const selectAllVisible = useCallback(function () {
      if (!filteredBoard || !filteredBoard.columns) return;
      const next = new Set();
      for (const col of filteredBoard.columns) {
        for (const t of col.tasks || []) next.add(t.id);
      }
      setSelectedIds(next);
      if (next.size > 0) {
        const first = Array.from(next)[0];
        setLastSelectedId(first);
      }
    }, [filteredBoard]);

    const selectAllInColumn = useCallback(function (columnName) {
      if (!filteredBoard || !filteredBoard.columns) return;
      const col = filteredBoard.columns.find(function (c) { return c.name === columnName; });
      if (!col) return;
      const allSelected = col.tasks && col.tasks.length > 0 && col.tasks.every(function (t) { return selectedIds.has(t.id); });
      const next = new Set(selectedIds);
      if (allSelected) {
        for (const t of col.tasks || []) next.delete(t.id);
      } else {
        for (const t of col.tasks || []) next.add(t.id);
      }
      setSelectedIds(next);
      if (col.tasks && col.tasks.length > 0) setLastSelectedId(col.tasks[0].id);
    }, [filteredBoard, selectedIds]);

    const applyBulk = useCallback(function (patch, confirmMsg) {
      if (selectedIds.size === 0) return;
      if (confirmMsg && !window.confirm(confirmMsg)) return;
      const finalPatch = withCompletionSummary(patch, selectedIds.size, t);
      if (!finalPatch) return;
      const body = Object.assign({ ids: Array.from(selectedIds) }, finalPatch);
      // Optimistic UI for status moves (same pattern as moveSelected).
      if (finalPatch.status) {
        setBoardData(function (b) {
          if (!b) return b;
          const moved = [];
          const columns = b.columns.map(function (col) {
            const kept = [];
            for (const t of col.tasks) {
              if (selectedIds.has(t.id)) moved.push(Object.assign({}, t, { status: finalPatch.status }));
              else kept.push(t);
            }
            return Object.assign({}, col, { tasks: kept });
          });
          const dest = columns.find(function (c) { return c.name === finalPatch.status; });
          if (dest) dest.tasks = moved.concat(dest.tasks);
          return Object.assign({}, b, { columns });
        });
      }
      SDK.fetchJSON(withBoard(`${API}/tasks/bulk`, board), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then(function (res) {
          const failed = (res.results || []).filter(function (r) { return !r.ok; });
          if (failed.length > 0) {
            setError(tx(t, "bulkFailed", "Bulk: ") +
              `${failed.length} of ${res.results.length} failed: ` +
              failed.slice(0, 3).map(function (f) { return `${f.id} (${f.error})`; }).join("; "));
            setFailedIds(new Set(failed.map(function (f) { return f.id; })));
          } else {
            setFailedIds(new Set());
          }
          setSelectedIds(new Set());
          setLastSelectedId(null);
          loadBoard();
        })
        .catch(function (e) {
          setError(String(e.message || e));
          setFailedIds(new Set(selectedIds));
          loadBoard();
        });
    }, [selectedIds, loadBoard, board, t]);

    // --- board switching ----------------------------------------------------
    const switchBoard = useCallback(function (nextSlug) {
      if (!nextSlug || nextSlug === board) return;
      // Optimistic UI: clear the current grid + show loading, reset the
      // event cursor so the WS reopens aligned to the new board's
      // latest_event_id on the next loadBoard.
      setBoardData(null);
      cursorRef.current = 0;
      setLoading(true);
      setBoard(nextSlug);
      writeSelectedBoard(nextSlug);
      // Reset filters so stale search/tenant/assignee don't persist across boards.
      setSearch("");
      setTenantFilter("");
      setAssigneeFilter("");
      setIncludeArchived(false);
      setColumnLimits({});
      clearSelected();
    }, [board, clearSelected]);

    const createNewBoard = useCallback(function (payload) {
      return SDK.fetchJSON(`${API}/boards`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then(function (res) {
        loadBoardList();
        const slug = res && res.board && res.board.slug;
        if (slug && payload.switch) switchBoard(slug);
        return res;
      });
    }, [loadBoardList, switchBoard, board]);

    // PATCH board metadata (name / description / default project directory).
    // Refreshes the board list so InlineCreate's workspace defaults pick up
    // the new default_workdir immediately.
    const updateBoard = useCallback(function (slug, payload) {
      return SDK.fetchJSON(`${API}/boards/${encodeURIComponent(slug)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then(function (res) {
        loadBoardList();
        return res;
      });
    }, [loadBoardList]);

    const deleteBoard = useCallback(function (slug) {
      if (!slug || slug === "default") return Promise.resolve();
      return SDK.fetchJSON(`${API}/boards/${encodeURIComponent(slug)}`, {
        method: "DELETE",
      }).then(function () {
        loadBoardList();
        if (board === slug) switchBoard("default");
      });
    }, [board, loadBoardList, switchBoard]);

   const deleteTask = useCallback(function (taskId) {
     if (!window.confirm(tx(t, "trash.confirm", FALLBACK_TRASH.confirm))) return Promise.resolve();
     return SDK.fetchJSON(withBoard(`${API}/tasks/${encodeURIComponent(taskId)}`, board), {
       method: "DELETE",
     }).then(function () {
       loadBoard();
       setSelectedIds(function (prev) {
         const next = new Set(prev);
         next.delete(taskId);
         return next;
       });
     }).catch(function (e) { setError(String(e.message || e)); });
   }, [board, loadBoard, t]);

    const deleteSelected = useCallback(function (count) {
      if (selectedIds.size === 0) return Promise.resolve();
      if (!window.confirm(tx(t, "trash.confirmMany", "Permanently delete {n} selected tasks? This cannot be undone.", { n: count }))) return Promise.resolve();
      const ids = Array.from(selectedIds);
      setSelectedIds(new Set());
      return Promise.all(ids.map(function (id) {
        return SDK.fetchJSON(withBoard(`${API}/tasks/${encodeURIComponent(id)}`, board), { method: "DELETE" });
      })).then(function () {
        loadBoard();
      }).catch(function (e) { setError(String(e.message || e)); });
    }, [selectedIds, board, loadBoard, t]);

    // --- render -------------------------------------------------------------
    if (loading && !boardData) {
      return h("div", { className: "p-8 text-sm text-muted-foreground" },
        tx(t, "loading", "Loading Kanban board…"));
    }
    if (error && !boardData) {
      return h(Card, null,
        h(CardContent, { className: "p-6" },
          h("div", { className: "text-sm text-destructive" },
            tx(t, "loadFailed", "Failed to load Kanban board: "), error),
          h("div", { className: "text-xs text-muted-foreground mt-2" },
            tx(t, "loadFailedHint",
              "The backend auto-creates kanban.db on first read. If this persists, check the dashboard logs.")),
        ),
      );
    }
    if (!filteredBoard) return null;

    const renderMd = !config || config.render_markdown !== false;
    const visibleTaskCount = filteredBoard.columns.reduce(function (count, column) {
      return count + column.tasks.length;
    }, 0);

    return h(ErrorBoundary, null,
      h("div", { className: "hermes-kanban flex flex-col gap-4" },
        h(BoardSwitcher, {
          board: board,
          boardList: boardList,
          onSwitch: switchBoard,
          onNewClick: function () { setShowNewBoard(true); },
          onSettingsClick: function () { setShowBoardSettings(true); },
          onDeleteBoard: deleteBoard,
        }),
        showNewBoard ? h(NewBoardDialog, {
          onCancel: function () { setShowNewBoard(false); },
          onCreate: function (payload) {
            return createNewBoard(payload).then(function () { setShowNewBoard(false); });
          },
        }) : null,
        showBoardSettings ? h(BoardSettingsDialog, {
          board: boardList.find(function (item) { return item.slug === board; })
            || { slug: board },
          onCancel: function () { setShowBoardSettings(false); },
          onSave: function (payload) {
            return updateBoard(board, payload).then(function () { setShowBoardSettings(false); });
          },
        }) : null,
        h(OrchestrationPanel, null),
        h(AttentionStrip, {
          boardData,
          onOpen: openTask,
        }),
        h(BoardToolbar, {
          board: boardData,
          tenantFilter, setTenantFilter,
          assigneeFilter, setAssigneeFilter,
          includeArchived, setIncludeArchived,
          laneByProfile, setLaneByProfile,
          search, setSearch,
          onNudgeDispatch: function () {
            SDK.fetchJSON(withBoard(`${API}/dispatch?max=8`, board), { method: "POST" })
              .then(loadBoard)
              .catch(function (e) { setError(String(e.message || e)); });
          },
          onRefresh: loadBoard,
        }),
       selectedIds.size > 0 ? h(BulkActionBar, {
         count: selectedIds.size,
         assignees: (boardData && boardData.assignees) || [],
         onApply: applyBulk,
         onClear: clearSelected,
         onSelectAllVisible: selectAllVisible,
         onDelete: deleteSelected,
       }) : null,
        error ? h("div", { className: "text-xs text-destructive px-2" }, error) : null,
        visibleTaskCount === 0 ? h("div", {
          className: "pmo-board-empty", role: "status",
        }, pt("empty.board")) : null,
        h(BoardColumns, {
          board: filteredBoard,
          boardMeta: boardList.find(function (item) { return item.slug === board; }) || null,
          laneByProfile,
          selectedIds,
          failedIds,
          draggingTaskId,
          onDragStart: handleDragStart,
          onDragEnd: handleDragEnd,
          toggleSelected,
          toggleRange,
          selectAllInColumn,
          onMove: moveTask,
          onMoveSelected: moveSelected,
          onDelete: deleteTask,
          onOpen: openTask,
          onCreate: createTask,
          onLoadMore: loadMoreColumn,
          allTasks: boardData.columns.reduce(function (acc, c) { return acc.concat(c.tasks); }, []),
        }),
        selectedTaskId ? h(TaskDrawer, {
          taskId: selectedTaskId,
          boardSlug: board,
          onClose: closeTask,
          onOpenTask: openTask,
          onRefresh: loadBoard,
          renderMarkdown: renderMd,
          allTasks: boardData.columns.reduce(function (acc, c) { return acc.concat(c.tasks); }, []),
          assignees: (boardData && boardData.assignees) || [],
          eventTick: taskEventTick[selectedTaskId] || 0,
        }) : null,
      ),
    );
  }

  // -------------------------------------------------------------------------
  // Attention strip — surfaces every task with active diagnostics,
  // severity-marked (warning/error/critical). Collapsed by default; click
  // Show to expand into per-task rows with Open buttons. Dismissible
  // per session via state flag.
  // -------------------------------------------------------------------------

  function collectDiagTasks(boardData) {
    if (!boardData || !boardData.columns) return [];
    const out = [];
    for (const col of boardData.columns) {
      for (const t of col.tasks || []) {
        if (t.diagnostics && t.diagnostics.length > 0) out.push(t);
        else if (t.warnings && t.warnings.count > 0) out.push(t);
      }
    }
    // Sort: highest severity first (critical > error > warning), then by
    // most recent latest_at.
    const sevIdx = function (s) {
      if (s === "critical") return 3;
      if (s === "error") return 2;
      if (s === "warning") return 1;
      return 0;
    };
    out.sort(function (a, b) {
      const aSev = sevIdx((a.warnings && a.warnings.highest_severity) || "warning");
      const bSev = sevIdx((b.warnings && b.warnings.highest_severity) || "warning");
      if (aSev !== bSev) return bSev - aSev;
      const aLa = (a.warnings && a.warnings.latest_at) || 0;
      const bLa = (b.warnings && b.warnings.latest_at) || 0;
      return bLa - aLa;
    });
    return out;
  }

  function AttentionStrip(props) {
    const { t } = useI18n();
    const [expanded, setExpanded] = useState(false);
    const [dismissed, setDismissed] = useState(false);
    const diagTasks = useMemo(
      function () { return collectDiagTasks(props.boardData); },
      [props.boardData]
    );
    if (dismissed || diagTasks.length === 0) return null;
    // Pick the highest severity present so we can colour the strip.
    let topSev = "warning";
    for (const td of diagTasks) {
      const s = (td.warnings && td.warnings.highest_severity) || "warning";
      if (s === "critical") { topSev = "critical"; break; }
      if (s === "error" && topSev !== "critical") topSev = "error";
    }
    return h("div", {
      className: cn(
        "hermes-kanban-attention",
        "hermes-kanban-attention--" + topSev,
      ),
    },
      h("div", { className: "hermes-kanban-attention-bar" },
        h("span", { className: "hermes-kanban-attention-icon" },
          topSev === "critical" ? "!!!" : topSev === "error" ? "!!" : "⚠"),
        h("span", { className: "hermes-kanban-attention-text" },
          diagTasks.length === 1
            ? tx(t, "taskNeedsAttention", "1 task needs attention")
            : tx(t, "tasksNeedAttention", "{n} tasks need attention",
                { n: diagTasks.length }),
        ),
        h("button", {
          className: "hermes-kanban-attention-toggle",
          onClick: function () { setExpanded(function (x) { return !x; }); },
          type: "button",
        }, expanded ? tx(t, "hide", "Hide") : tx(t, "show", "Show")),
        h("button", {
          className: "hermes-kanban-attention-dismiss",
          onClick: function () { setDismissed(true); },
          title: "Hide until next page reload",
          type: "button",
        }, "\u2715"),
      ),
      expanded
        ? h("div", { className: "hermes-kanban-attention-list" },
            diagTasks.map(function (task) {
              const sev = (task.warnings && task.warnings.highest_severity) || "warning";
              const kinds = task.warnings && task.warnings.kinds ? Object.keys(task.warnings.kinds) : [];
              return h("div", {
                key: task.id,
                className: cn(
                  "hermes-kanban-attention-row",
                  "hermes-kanban-attention-row--" + sev,
                ),
              },
                h("span", { className: "hermes-kanban-attention-row-sev" },
                  sev === "critical" ? "!!!" : sev === "error" ? "!!" : "⚠"),
                h("span", { className: "hermes-kanban-attention-row-id" }, task.id),
                h("span", { className: "hermes-kanban-attention-row-title" },
                  task.title || tx(t, "untitled", "(untitled)")),
                h("span", { className: "hermes-kanban-attention-row-meta" },
                  task.assignee ? "@" + task.assignee : tx(t, "unassigned", "unassigned"),
                  " \u00b7 ",
                  kinds.length > 0 ? kinds.join(", ") : tx(t, "diagnostic", "diagnostic"),
                ),
                h("button", {
                  className: "hermes-kanban-attention-row-btn",
                  onClick: function () { props.onOpen(task.id); },
                  type: "button",
                }, tx(t, "open", "Open")),
              );
            }),
          )
        : null,
    );
  }

  // -------------------------------------------------------------------------
  // Diagnostics section — generic renderer for a task's active distress
  // signals. Each diagnostic carries its own title, detail, data payload,
  // and a list of structured actions; the section renders them uniformly
  // regardless of kind. Replaces the hallucination-specific
  // ``RecoveryPopover`` from the previous iteration.
  //
  // Action kinds supported today:
  //   reclaim   → POST /tasks/:id/reclaim
  //   reassign  → POST /tasks/:id/reassign (with profile picker)
  //   unblock   → PATCH /tasks/:id  body: {status: "ready"}
  //   comment   → scroll to the comment input at the bottom of the drawer
  //   cli_hint  → copy payload.command to clipboard
  //   open_docs → open payload.url in a new tab
  // Unknown kinds are rendered as a disabled informational row so the
  // server can add new action kinds without breaking the UI.
  // -------------------------------------------------------------------------

  function DiagnosticActionButton(props) {
    const { t } = useI18n();
    const { action, onExec, busy, extra } = props;
    const label = (action.suggested ? "\u2606 " : "") + action.label;
    const cls = cn(
      "hermes-kanban-diag-action-btn",
      action.suggested ? "hermes-kanban-diag-action-btn--suggested" : "",
    );
    if (action.kind === "reclaim" || action.kind === "reassign" ||
        action.kind === "unblock") {
      return h("button", {
        className: cls,
        disabled: busy || (extra && extra.disabled),
        onClick: function () { onExec(action); },
        type: "button",
      }, label);
    }
    if (action.kind === "cli_hint") {
      return h("button", {
        className: cls,
        disabled: busy,
        onClick: function () { onExec(action); },
        type: "button",
        title: tx(t, "copyCommand", "Copy command to clipboard"),
      }, (extra && extra.copied) ? tx(t, "copied", "Copied") : label);
    }
    if (action.kind === "comment") {
      return h("button", {
        className: cls,
        onClick: function () { onExec(action); },
        type: "button",
      }, label);
    }
    if (action.kind === "open_docs") {
      return h("a", {
        className: cls,
        href: (action.payload && action.payload.url) || "#",
        target: "_blank",
        rel: "noreferrer",
      }, label);
    }
    // Unknown kind — render informational, non-interactive.
    return h("span", { className: cls + " hermes-kanban-diag-action-btn--unknown" },
      label);
  }

  function DiagnosticCard(props) {
    const { t } = useI18n();
    const { diag, task, boardSlug, assignees, onRefresh } = props;
    const [busy, setBusy] = useState(false);
    const [msg, setMsg] = useState(null);
    const [copiedKey, setCopiedKey] = useState(null);
    const [reassignProfile, setReassignProfile] = useState(task.assignee || "");

    const execAction = function (action) {
      if (busy) return;
      if (action.kind === "cli_hint") {
        const cmd = (action.payload && action.payload.command) || action.label;
        const fallback = function () { window.prompt("Copy this command:", cmd); };
        try {
          const p = navigator.clipboard && navigator.clipboard.writeText(cmd);
          if (p && p.then) {
            p.then(function () {
              setCopiedKey(action.label);
              setTimeout(function () { setCopiedKey(null); }, 2000);
            }).catch(fallback);
          } else {
            fallback();
          }
        } catch (_) {
          fallback();
        }
        return;
      }
      if (action.kind === "comment") {
        // Scroll the comment input into view; the drawer already has one
        // at the bottom. Focus it so the operator can start typing.
        const ta = document.querySelector(".hermes-kanban-drawer-comment-row input, .hermes-kanban-drawer-comment-row textarea");
        if (ta) {
          ta.scrollIntoView({ behavior: "smooth", block: "nearest" });
          ta.focus();
        }
        return;
      }
      if (action.kind === "unblock") {
        setBusy(true); setMsg(null);
        const url = withBoard(`${API}/tasks/${encodeURIComponent(task.id)}`, boardSlug);
        SDK.fetchJSON(url, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "ready" }),
        }).then(function () {
          setMsg({ ok: true, text: tx(t, "unblockedMessage",
            "Unblocked {id}. Task is ready for the next tick.", { id: task.id }) });
          if (onRefresh) onRefresh();
        }).catch(function (err) {
          setMsg({ ok: false, text: tx(t, "unblockFailed", "Unblock failed: ") + (err.message || err) });
        }).then(function () { setBusy(false); });
        return;
      }
      if (action.kind === "reclaim") {
        setBusy(true); setMsg(null);
        const url = withBoard(`${API}/tasks/${encodeURIComponent(task.id)}/reclaim`, boardSlug);
        SDK.fetchJSON(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: `recovery action for ${diag.kind}` }),
        }).then(function () {
          setMsg({ ok: true, text: tx(t, "reclaimedMessage",
            "Reclaimed {id}. Task is back to ready.", { id: task.id }) });
          if (onRefresh) onRefresh();
        }).catch(function (err) {
          setMsg({ ok: false, text: tx(t, "reclaimFailed", "Reclaim failed: ") + (err.message || err) });
        }).then(function () { setBusy(false); });
        return;
      }
      if (action.kind === "reassign") {
        if (!reassignProfile) {
          setMsg({ ok: false, text: tx(t, "pickProfileFirst", "Pick a profile first.") });
          return;
        }
        setBusy(true); setMsg(null);
        const url = withBoard(`${API}/tasks/${encodeURIComponent(task.id)}/reassign`, boardSlug);
        const body = {
          profile: reassignProfile || null,
          reclaim_first: !!(action.payload && action.payload.reclaim_first),
          reason: `recovery action for ${diag.kind}`,
        };
        SDK.fetchJSON(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }).then(function () {
          setMsg({
            ok: true,
            text: tx(t, "reassignedMessage", "Reassigned {id} to {profile}.",
              { id: task.id, profile: reassignProfile }),
          });
          if (onRefresh) onRefresh();
        }).catch(function (err) {
          setMsg({ ok: false, text: tx(t, "reassignFailed", "Reassign failed: ") + (err.message || err) });
        }).then(function () { setBusy(false); });
        return;
      }
    };

    // Pull out the reassign action so we can render its picker inline.
    const reassignAction = (diag.actions || []).find(function (a) {
      return a.kind === "reassign";
    });

    const sevClass = "hermes-kanban-diag--" + (diag.severity || "warning");
    return h("div", { className: cn("hermes-kanban-diag", sevClass) },
      h("div", { className: "hermes-kanban-diag-header" },
        h("span", { className: "hermes-kanban-diag-sev" },
          diag.severity === "critical" ? "!!!" :
          diag.severity === "error" ? "!!" : "\u26a0"),
        h("span", { className: "hermes-kanban-diag-title" },
          diag.title),
      ),
      h("div", { className: "hermes-kanban-diag-detail" },
        diag.detail),
      diag.data && Object.keys(diag.data).length > 0
        ? h("div", { className: "hermes-kanban-diag-data" },
            Object.keys(diag.data).map(function (k) {
              const v = diag.data[k];
              if (Array.isArray(v) && v.length > 0 && typeof v[0] === "string" &&
                  v[0].indexOf("t_") === 0) {
                // Task-id list — render as chips.
                return h("div", { key: k, className: "hermes-kanban-diag-data-row" },
                  h("span", { className: "hermes-kanban-diag-data-key" }, k + ":"),
                  v.map(function (x) {
                    return h("code", {
                      key: x, className: "hermes-kanban-event-phantom-chip",
                    }, x);
                  }),
                );
              }
              return h("div", { key: k, className: "hermes-kanban-diag-data-row" },
                h("span", { className: "hermes-kanban-diag-data-key" }, k + ":"),
                h("span", { className: "hermes-kanban-diag-data-val" },
                  Array.isArray(v) ? v.join(", ") : String(v)),
              );
            }),
          )
        : null,
      // Inline reassign picker — only shown when the diagnostic offers
      // a reassign action. Profile list comes from the board payload.
      reassignAction
        ? h("div", { className: "hermes-kanban-diag-reassign-row" },
            h("span", { className: "hermes-kanban-diag-reassign-label" },
              tx(t, "reassignTo", "Reassign to:")),
            h("select", {
              className: "hermes-kanban-recovery-select",
              value: reassignProfile,
              onChange: function (e) { setReassignProfile(e.target.value); },
            },
              h("option", { value: "" }, "(unassigned)"),
              (assignees || []).map(function (a) {
                return h("option", { key: a, value: a }, a);
              }),
            ),
          )
        : null,
      h("div", { className: "hermes-kanban-diag-actions" },
        (diag.actions || []).map(function (a, i) {
          return h(DiagnosticActionButton, {
            key: a.kind + i,
            action: a,
            onExec: execAction,
            busy: busy,
            extra: {
              copied: copiedKey === a.label,
              disabled: (a.kind === "reassign" && !reassignProfile),
            },
          });
        }),
      ),
      msg
        ? h("div", {
            className: cn(
              "hermes-kanban-diag-msg",
              msg.ok ? "hermes-kanban-diag-msg--ok" : "hermes-kanban-diag-msg--err",
            ),
          }, msg.text)
        : null,
    );
  }

  function DiagnosticsSection(props) {
    const { t } = useI18n();
    const diags = props.diagnostics || [];
    const hasOpenDiags = diags.length > 0;
    const [open, setOpen] = useState(hasOpenDiags);
    useEffect(function () {
      if (hasOpenDiags) setOpen(true);
    }, [hasOpenDiags]);
    if (!hasOpenDiags && !props.alwaysVisible) {
      // Nothing active. Collapse the section entirely rather than showing
      // an empty "Recovery" header — keeps clean tasks visually clean.
      return null;
    }
    return h("div", { className: "hermes-kanban-section" },
      h("div", { className: "hermes-kanban-section-head-row" },
        h("span", { className: "hermes-kanban-section-head" },
          hasOpenDiags
            ? h("span", { className: "hermes-kanban-section-head-warning" },
                `\u26a0 ${tx(t, "diagnostics", "Diagnostics")} (${diags.length})`)
            : tx(t, "diagnostics", "Diagnostics"),
        ),
        h("button", {
          className: "hermes-kanban-section-toggle",
          onClick: function () { setOpen(function (x) { return !x; }); },
          type: "button",
        }, open ? tx(t, "hide", "Hide") : tx(t, "show", "Show")),
      ),
      open
        ? h("div", { className: "hermes-kanban-diag-list" },
            diags.map(function (d, i) {
              return h(DiagnosticCard, {
                key: props.task.id + ":" + d.kind + i,
                diag: d,
                task: props.task,
                boardSlug: props.boardSlug,
                assignees: props.assignees,
                onRefresh: props.onRefresh,
              });
            }),
          )
        : null,
    );
  }

    // -------------------------------------------------------------------------
  // Board switcher (multi-project)
  // -------------------------------------------------------------------------

  // Small `?` affordance next to the board controls. Opens the kanban docs
  // page in a new tab so users can look up what any of the widgets mean
  // without losing the current board view.
  function DocsLink() {
    return h("a", {
      href: DOCS_URL,
      target: "_blank",
      rel: "noopener noreferrer",
      className: "hermes-kanban-docs-link",
      title: "Open Hermes Kanban docs in a new tab",
      "aria-label": "Hermes Kanban documentation",
    }, "?");
  }

  // ---------------------------------------------------------------------
  // OrchestrationPanel — collapsible settings panel for the kanban
  // orchestrator (orchestrator profile picker, default assignee picker,
  // auto-decompose toggle, plus per-profile description editing with
  // auto-generate). Backed by /orchestration + /profiles endpoints.
  // ---------------------------------------------------------------------

  function OrchestrationPanel() {
    const [expanded, setExpanded] = useState(false);
    const [settings, setSettings] = useState(null);
    const [profiles, setProfiles] = useState([]);
    const [busy, setBusy] = useState({});
    const [msg, setMsg] = useState(null);

    const loadAll = useCallback(function () {
      Promise.all([
        SDK.fetchJSON(`${API}/orchestration`),
        SDK.fetchJSON(`${API}/profiles`),
      ]).then(function (results) {
        setSettings(results[0] || null);
        setProfiles((results[1] && results[1].profiles) || []);
        setMsg(null);
      }).catch(function (err) {
        setMsg({ ok: false, text: "Failed to load: " + (err.message || String(err)) });
      });
    }, []);

    useEffect(function () {
      // Load on mount so the collapsed pill shows the real mode without
      // requiring the user to expand the panel first.
      if (settings === null) loadAll();
    }, [settings, loadAll]);

    const saveSettings = function (patch) {
      setMsg(null);
      return SDK.fetchJSON(`${API}/orchestration`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      }).then(function (res) {
        setSettings(res);
        setMsg({ ok: true, text: "Settings saved." });
        return res;
      }).catch(function (err) {
        setMsg({ ok: false, text: "Save failed: " + (err.message || String(err)) });
      });
    };

    const saveProfileDescription = function (name, description) {
      setBusy(function (b) { return Object.assign({}, b, { [name]: "save" }); });
      return SDK.fetchJSON(`${API}/profiles/${encodeURIComponent(name)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: description }),
      }).then(function () {
        loadAll();
        setMsg({ ok: true, text: `Description saved for ${name}.` });
      }).catch(function (err) {
        setMsg({ ok: false, text: "Save failed: " + (err.message || String(err)) });
      }).then(function () {
        setBusy(function (b) {
          const next = Object.assign({}, b); delete next[name]; return next;
        });
      });
    };

    const autoGenerateDescription = function (name, overwrite) {
      setBusy(function (b) { return Object.assign({}, b, { [name]: "auto" }); });
      return SDK.fetchJSON(`${API}/profiles/${encodeURIComponent(name)}/describe-auto`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ overwrite: !!overwrite }),
      }).then(function (res) {
        if (res && res.ok) {
          loadAll();
          setMsg({ ok: true, text: `Auto-generated description for ${name}.` });
        } else {
          setMsg({
            ok: false,
            text: "Auto-generate failed: " + ((res && res.reason) || "unknown error"),
          });
        }
      }).catch(function (err) {
        setMsg({ ok: false, text: "Auto-generate failed: " + (err.message || String(err)) });
      }).then(function () {
        setBusy(function (b) {
          const next = Object.assign({}, b); delete next[name]; return next;
        });
      });
    };

    const headerLabel = expanded
      ? "▾ Orchestration settings"
      : "▸ Orchestration settings";

    // Mode pill — always visible (collapsed or expanded). One click flips
    // between Auto and Manual. Auto = dispatcher decomposes new triage tasks
    // every tick. Manual = pre-PR behavior, the user clicks ⚗ Decompose on
    // each triage card (or runs `hermes kanban decompose <id>`) and tasks
    // stay in triage until then.
    const autoOn = !!(settings && settings.auto_decompose);
    const modePillTitle = settings === null
      ? "Loading mode…"
      : (autoOn
          ? "Orchestration: Auto — the dispatcher decomposes new triage tasks automatically every tick. Click to switch to Manual (pre-PR behavior)."
          : "Orchestration: Manual — triage tasks stay in triage until you click ⚗ Decompose on each card. Click to switch to Auto.");
    const modePill = h("button", {
      type: "button",
      onClick: function () {
        if (settings === null) return;  // not loaded yet
        saveSettings({ auto_decompose: !autoOn });
      },
      disabled: settings === null,
      title: modePillTitle,
      className: "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 "
                 + "text-xs font-medium "
                 + (autoOn
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                    : "border-muted-foreground/30 bg-muted/30 text-muted-foreground"),
    },
      "Orchestration: ",
      h("span", { className: "ml-1 font-semibold" },
        settings === null ? "…" : (autoOn ? "Auto" : "Manual"))
    );

    if (!expanded) {
      return h("div", { className: "flex items-center gap-3 text-xs" },
        modePill,
        h("button", {
          type: "button",
          onClick: function () { setExpanded(true); },
          className: "underline text-muted-foreground hover:text-foreground",
          title: "Configure the kanban orchestrator (profile picker, default assignee, auto-decompose, profile descriptions)",
        }, headerLabel),
      );
    }

    const profileOptions = profiles.map(function (p) {
      const tag = p.is_default ? " (default)" : "";
      return h(SelectOption, { key: p.name, value: p.name }, p.name + tag);
    });

    return h(Card, { className: "p-3" },
      h(CardContent, { className: "p-2 flex flex-col gap-3" },
        h("div", { className: "flex items-center justify-between" },
          h("button", {
            type: "button",
            onClick: function () { setExpanded(false); },
            className: "text-sm font-medium underline-offset-2 hover:underline",
          }, headerLabel),
          modePill,
          h(Button, { onClick: loadAll, size: "sm" }, "Reload"),
        ),
        msg ? h("div", {
          className: msg.ok ? "hermes-kanban-msg-ok" : "hermes-kanban-msg-err",
        }, msg.text) : null,

        settings ? h("div", { className: "grid gap-3 sm:grid-cols-3" },
          h("div", { className: "flex flex-col gap-1" },
            h(Label, { className: "text-xs text-muted-foreground" },
              "Orchestrator profile"),
            h(Select, Object.assign({
              value: settings.orchestrator_profile || "",
              className: "h-8",
            }, selectChangeHandler(function (v) {
              saveSettings({ orchestrator_profile: v });
            })),
              h(SelectOption, { value: "" },
                "(default: " + (settings.active_profile || "default") + ")"),
              profileOptions,
            ),
            h("div", { className: "text-[10px] text-muted-foreground" },
              "Resolved: " + (settings.resolved_orchestrator_profile || "default")),
            h("div", { className: "text-[10px] text-muted-foreground" },
              "Owns the root task after fan-out (wakes back up to judge completion). Does not drive how tasks split — configure the decomposer model under auxiliary.kanban_decomposer."),
          ),
          h("div", { className: "flex flex-col gap-1" },
            h(Label, { className: "text-xs text-muted-foreground" },
              "Default assignee"),
            h(Select, Object.assign({
              value: settings.default_assignee || "",
              className: "h-8",
            }, selectChangeHandler(function (v) {
              saveSettings({ default_assignee: v });
            })),
              h(SelectOption, { value: "" },
                "(default: " + (settings.active_profile || "default") + ")"),
              profileOptions,
            ),
            h("div", { className: "text-[10px] text-muted-foreground" },
              "Resolved: " + (settings.resolved_default_assignee || "default")),
          ),
          h("div", { className: "flex flex-col gap-1" },
            h(Label, { className: "text-xs text-muted-foreground" },
              "Orchestration mode"),
            h("label", { className: "flex items-center gap-2 text-xs h-8" },
              h(Checkbox, {
                checked: !!settings.auto_decompose,
                onCheckedChange: function (checked) {
                  saveSettings({ auto_decompose: checked === true });
                },
              }),
              "Auto-decompose triage tasks",
            ),
            h("div", { className: "text-[10px] text-muted-foreground" },
              settings.auto_decompose
                ? "The dispatcher decomposes new triage tasks automatically."
                : "Triage tasks stay in triage until you click ⚗ Decompose."),
          ),
        ) : h("div", { className: "text-xs text-muted-foreground" },
          "Loading…"),

        h("div", { className: "border-t pt-3" },
          h(Label, { className: "text-xs text-muted-foreground" },
            "Profile descriptions"),
          h("div", { className: "text-[10px] text-muted-foreground pb-2" },
            "Descriptions guide the decomposer's routing. Click ⚗ to auto-generate, or edit and save."),
          profiles.length === 0
            ? h("div", { className: "text-xs text-muted-foreground" }, "No profiles installed.")
            : h("div", { className: "flex flex-col gap-2" },
                profiles.map(function (p) {
                  return h(ProfileDescriptionRow, {
                    key: p.name,
                    profile: p,
                    busy: busy[p.name] || null,
                    onSave: saveProfileDescription,
                    onAuto: autoGenerateDescription,
                  });
                }),
              ),
        ),
      ),
    );
  }

  function ProfileDescriptionRow(props) {
    const p = props.profile;
    const [draft, setDraft] = useState(p.description || "");
    const busy = props.busy;
    // Re-sync the local draft if the server-side description changes (e.g.
    // after auto-generate). Cheap because re-runs only happen on prop change.
    useEffect(function () {
      setDraft(p.description || "");
    }, [p.description]);

    const tag = p.description_auto && p.description ? " [auto, review]" : "";
    return h("div", { className: "flex flex-col gap-1 border-l-2 pl-2",
      style: { borderColor: p.description ? "#888" : "#cc6" } },
      h("div", { className: "flex items-center gap-2 text-xs" },
        h("span", { className: "font-medium" }, p.name),
        p.is_default ? h("span", { className: "text-[10px] text-muted-foreground" }, "(default)") : null,
        p.description_auto && p.description
          ? h("span", { className: "text-[10px] text-yellow-600" }, "auto — review")
          : null,
        !p.description
          ? h("span", { className: "text-[10px] text-yellow-600" }, "⚠ no description")
          : null,
      ),
      h("div", { className: "flex items-center gap-2" },
        h(Input, {
          value: draft,
          onChange: function (e) { setDraft(e.target.value); },
          placeholder: "What is this profile good at?",
          className: "h-7 text-xs flex-1",
        }),
        h(Button, {
          onClick: function () { props.onSave(p.name, draft); },
          size: "sm",
          disabled: !!busy || draft === (p.description || ""),
          title: "Save the description above as user-authored",
        }, busy === "save" ? "Saving…" : "Save"),
        h(Button, {
          onClick: function () { props.onAuto(p.name, true); },
          size: "sm",
          disabled: !!busy,
          title: "Auto-generate a description from this profile's skills and model",
        }, busy === "auto" ? "Generating…" : "⚗ Auto"),
      ),
    );
  }

  function BoardSwitcher(props) {
    const { t } = useI18n();
    const list = props.boardList || [];
    const current = list.find(function (b) { return b.slug === props.board; });
    const currentName = current && current.name ? current.name : props.board;
    const currentTotal = current ? current.total : 0;
    const hasMultipleBoards = list.length > 1;

    // Hide entirely when only the default board exists AND it's empty —
    // single-project users never see boards UI unless they ask for it.
    // We show the [+ New board] affordance as soon as any board has a
    // task (so the user can discover multi-project before they need it)
    // OR when any non-default board exists.
    const totalAcrossAllBoards = list.reduce(function (n, b) { return n + (b.total || 0); }, 0);
    const shouldShow = hasMultipleBoards || totalAcrossAllBoards > 0;
    if (!shouldShow) {
      return h("div", {
        className: "hermes-kanban-boardswitcher-compact",
        title: tx(t, "boardSwitcherHint", "Boards let you separate unrelated streams of work"),
      },
        h(Button, {
          onClick: props.onNewClick,
          size: "sm",
          className: "h-7 text-xs",
        }, tx(t, "newBoard", "+ New board")),
        h(Button, {
          onClick: props.onSettingsClick,
          size: "sm",
          className: "h-7 text-xs",
          title: tx(t, "boardSettingsTitle",
            "Board settings — name, description, and the default project directory new tasks inherit"),
        }, tx(t, "boardSettings", "Settings")),
        h(DocsLink, null),
      );
    }

    return h("div", { className: "hermes-kanban-boardswitcher" },
      h("div", { className: "hermes-kanban-boardswitcher-inner" },
        h("div", { className: "flex flex-col gap-0.5" },
          h("div", { className: "text-[11px] tracking-wider text-muted-foreground" },
            tx(t, "board", "Board")),
          h("div", { className: "flex items-center gap-2" },
            h(Select, Object.assign({
              value: props.board,
              className: "h-8 min-w-[220px]",
              "aria-label": "Switch kanban board",
              title: "Boards are independent work streams. Each board has its own tasks, tenants, and assignees.",
            }, selectChangeHandler(function (v) { if (v) props.onSwitch(v); })),
              list.map(function (b) {
                const label = b.total > 0
                  ? `${b.name || b.slug} · ${b.total}`
                  : (b.name || b.slug);
                return h(SelectOption, { key: b.slug, value: b.slug }, label);
              }),
            ),
            h("span", { className: "text-xs text-muted-foreground" },
              `${currentTotal || 0} task${currentTotal === 1 ? "" : "s"}`),
          ),
        ),
        h("div", { className: "flex-1" }),
        h(DocsLink, null),
        h(Button, {
          onClick: props.onSettingsClick,
          size: "sm",
          className: "h-8",
          title: tx(t, "boardSettingsTitle",
            "Board settings — name, description, and the default project directory new tasks inherit"),
        }, tx(t, "boardSettings", "Settings")),
        h(Button, {
          onClick: props.onNewClick,
          size: "sm",
          className: "h-8",
          title: "Create a new board. Useful when you want an unrelated work stream (different project, different team, isolated scratch area).",
        }, tx(t, "newBoard", "+ New board")),
        props.board !== "default"
          ? h(Button, {
            onClick: function () {
              const msg = tx(t, "archiveBoardConfirm",
                "Archive board '{name}'? It will be moved to boards/_archived/ so you can recover it later. Tasks on this board will no longer appear anywhere in the UI.",
                { name: currentName });
              if (window.confirm(msg)) props.onDeleteBoard(props.board);
            },
            size: "sm",
            className: "h-8",
            title: tx(t, "archiveBoardTitle", "Archive this board"),
          }, tx(t, "archive", "Archive"))
          : null,
      ),
    );
  }

  function NewBoardDialog(props) {
    const { t } = useI18n();
    const [slug, setSlug] = useState("");
    const [name, setName] = useState("");
    const [description, setDescription] = useState("");
    const [icon, setIcon] = useState("");
    const [projectDirectory, setProjectDirectory] = useState("");
    const [switchTo, setSwitchTo] = useState(true);
    const [submitting, setSubmitting] = useState(false);
    const [err, setErr] = useState(null);

    // Auto-derive a name from the slug if the user hasn't typed one.
    const autoName = useMemo(function () {
      if (!slug) return "";
      return slug.replace(/[-_]+/g, " ")
        .split(" ")
        .filter(Boolean)
        .map(function (w) { return w[0].toUpperCase() + w.slice(1); })
        .join(" ");
    }, [slug]);

    function onSubmit(ev) {
      if (ev) ev.preventDefault();
      if (!slug.trim()) { setErr("slug is required"); return; }
      setSubmitting(true);
      setErr(null);
      props.onCreate({
        slug: slug.trim(),
        name: name.trim() || autoName || undefined,
        description: description.trim() || undefined,
        icon: icon.trim() || undefined,
        default_workdir: projectDirectory.trim() || undefined,
        switch: switchTo,
      }).catch(function (e) {
        setErr(String(e && e.message ? e.message : e));
        setSubmitting(false);
      });
    }

    return h("div", {
      className: "hermes-kanban-dialog-backdrop",
      onClick: function (e) { if (e.target === e.currentTarget) props.onCancel(); },
    },
      h("form", {
        className: "hermes-kanban-dialog",
        onSubmit: onSubmit,
      },
        h("div", { className: "hermes-kanban-dialog-title" },
          tx(t, "newBoardTitle", "New board")),
        h("div", { className: "text-xs text-muted-foreground mb-2" },
          tx(t, "newBoardDescription",
            "Boards let you separate unrelated streams of work — one per project, repo, or domain. Workers on one board never see another board's tasks.")),
        h("div", { className: "flex flex-col gap-3" },
          h("div", { className: "flex flex-col gap-1" },
            h(Label, { className: "text-xs" }, tx(t, "slug", "Slug"), " ",
              h("span", { className: "text-muted-foreground" },
                tx(t, "slugHint", "— lowercase, hyphens, e.g. atm10-server"))),
            h(Input, {
              value: slug,
              onChange: function (e) { setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9\-_]/g, "-")); },
              placeholder: "atm10-server",
              autoFocus: true,
              className: "h-8",
            }),
          ),
          h("div", { className: "flex flex-col gap-1" },
            h(Label, { className: "text-xs" }, tx(t, "displayName", "Display name"), " ",
              h("span", { className: "text-muted-foreground" },
                tx(t, "displayNameHint", "(optional)"))),
            h(Input, {
              value: name,
              onChange: function (e) { setName(e.target.value); },
              placeholder: autoName || tx(t, "displayName", "Display name"),
              className: "h-8",
            }),
          ),
          h("div", { className: "flex flex-col gap-1" },
            h(Label, { className: "text-xs" }, tx(t, "description", "Description"), " ",
              h("span", { className: "text-muted-foreground" },
                tx(t, "descriptionHint", "(optional)"))),
            h(Input, {
              value: description,
              onChange: function (e) { setDescription(e.target.value); },
              placeholder: "What goes on this board?",
              className: "h-8",
            }),
          ),
          h("div", { className: "flex flex-col gap-1" },
            h(Label, { className: "text-xs" },
              tx(t, "projectDirectory", "Project directory"), " ",
              h("span", { className: "text-muted-foreground" },
                tx(t, "projectDirectoryHint", "(recommended)"))),
            h(Input, {
              value: projectDirectory,
              onChange: function (e) { setProjectDirectory(e.target.value); },
              placeholder: tx(t, "projectDirectoryPlaceholder",
                "Absolute path to the project folder"),
              title: tx(t, "projectDirectoryHelp",
                "Git projects use preserved worktrees. Other folders use the directory directly. Leave blank only for temporary work."),
              className: "h-8",
              autoCapitalize: "none",
              autoCorrect: "off",
              spellCheck: false,
            }),
            h("div", { className: "text-xs text-muted-foreground" },
              tx(t, "projectDirectoryExplanation",
                "Sets the default location for task files so project output is preserved.")),
          ),
          h("div", { className: "flex flex-col gap-1" },
            h(Label, { className: "text-xs" }, tx(t, "icon", "Icon"), " ",
              h("span", { className: "text-muted-foreground" },
                tx(t, "iconHint", "(single character or emoji)"))),
            h(Input, {
              value: icon,
              onChange: function (e) { setIcon(e.target.value.slice(0, 4)); },
              placeholder: "📦",
              className: "h-8 w-24",
            }),
          ),
          h("label", { className: "flex items-center gap-2 text-xs" },
            h(Checkbox, {
              checked: switchTo,
              onCheckedChange: function (checked) { setSwitchTo(checked === true); },
            }),
            tx(t, "switchAfterCreate", "Switch to this board after creating it"),
          ),
        ),
        err ? h("div", { className: "text-xs text-destructive mt-2" }, err) : null,
        h("div", { className: "hermes-kanban-dialog-actions" },
          h(Button, {
            type: "button",
            onClick: props.onCancel,
            size: "sm",
            disabled: submitting,
          }, tx(t, "cancel", "Cancel")),
          h(Button, {
            type: "submit",
            size: "sm",
            disabled: submitting || !slug.trim(),
          }, submitting ? tx(t, "creating", "Creating…") : tx(t, "createBoard", "Create board")),
        ),
      ),
    );
  }

  // Board settings dialog — edit display name, description, and the
  // board-level default project directory (default_workdir). The workdir
  // is the board-level setting every new task's workspace kind/path is
  // seeded from; task-level values in the create dialog override it.
  function BoardSettingsDialog(props) {
    const { t } = useI18n();
    const b = props.board || {};
    const [name, setName] = useState(b.name || "");
    const [description, setDescription] = useState(b.description || "");
    const [projectDirectory, setProjectDirectory] = useState(b.default_workdir || "");
    const [submitting, setSubmitting] = useState(false);
    const [err, setErr] = useState(null);

    function onSubmit(ev) {
      if (ev) ev.preventDefault();
      setSubmitting(true);
      setErr(null);
      // Send default_workdir unconditionally: "" clears it on the server,
      // a path sets it (validated server-side: absolute + existing dir).
      props.onSave({
        name: name.trim() || undefined,
        description: description.trim() || undefined,
        default_workdir: projectDirectory.trim(),
      }).catch(function (e) {
        setErr(parseApiErrorMessage(e));
        setSubmitting(false);
      });
    }

    return h("div", {
      className: "hermes-kanban-dialog-backdrop",
      onClick: function (e) { if (e.target === e.currentTarget) props.onCancel(); },
      onKeyDown: function (e) { if (e.key === "Escape") props.onCancel(); },
    },
      h("form", {
        className: "hermes-kanban-dialog",
        onSubmit: onSubmit,
      },
        h("div", { className: "hermes-kanban-dialog-title" },
          tx(t, "boardSettingsTitleFor", "Board settings — {name}",
            { name: b.name || b.slug || "default" })),
        h("div", { className: "flex flex-col gap-3" },
          h("div", { className: "flex flex-col gap-1" },
            h(Label, { className: "text-xs" }, tx(t, "displayName", "Display name")),
            h(Input, {
              value: name,
              onChange: function (e) { setName(e.target.value); },
              className: "h-8",
            }),
          ),
          h("div", { className: "flex flex-col gap-1" },
            h(Label, { className: "text-xs" }, tx(t, "description", "Description")),
            h(Input, {
              value: description,
              onChange: function (e) { setDescription(e.target.value); },
              className: "h-8",
            }),
          ),
          h("div", { className: "flex flex-col gap-1" },
            h(Label, { className: "text-xs" },
              tx(t, "projectDirectory", "Project directory")),
            h(Input, {
              value: projectDirectory,
              onChange: function (e) { setProjectDirectory(e.target.value); },
              placeholder: tx(t, "projectDirectoryPlaceholder",
                "Absolute path to the project folder"),
              title: tx(t, "projectDirectoryHelp",
                "Git projects use preserved worktrees. Other folders use the directory directly. Leave blank only for temporary work."),
              className: "h-8",
              autoCapitalize: "none",
              autoCorrect: "off",
              spellCheck: false,
            }),
            h("div", { className: "text-xs text-muted-foreground" },
              tx(t, "projectDirectoryOverrideHint",
                "New tasks inherit this as their workspace default; each task can still override it in the create dialog.")),
          ),
        ),
        err ? h("div", { className: "text-xs text-destructive mt-2" }, err) : null,
        h("div", { className: "hermes-kanban-dialog-actions" },
          h(Button, {
            type: "button",
            onClick: props.onCancel,
            size: "sm",
            disabled: submitting,
          }, tx(t, "cancel", "Cancel")),
          h(Button, {
            type: "submit",
            size: "sm",
            disabled: submitting,
          }, submitting ? tx(t, "saving", "Saving…") : tx(t, "save", "Save")),
        ),
      ),
    );
  }

  // -------------------------------------------------------------------------
  // Toolbar
  // -------------------------------------------------------------------------

  function BoardToolbar(props) {
    const { t } = useI18n();
    const tenants = (props.board && props.board.tenants) || [];
    const assignees = (props.board && props.board.assignees) || [];
    return h("div", { className: "flex flex-wrap items-end gap-3" },
      h("div", { className: "flex flex-col gap-1",
                 title: "Fuzzy-match tasks by id, title, or description. Matches across all columns." },
        h(Label, { className: "text-xs text-muted-foreground" }, tx(t, "search", "Search")),
        h(Input, {
          placeholder: tx(t, "filterCards", "Filter cards…"),
          value: props.search,
          onChange: function (e) { props.setSearch(e.target.value); },
          className: "w-56 h-8",
        }),
      ),
      h("div", { className: "flex flex-col gap-1",
                 title: "Tenants are free-form tags on a task (e.g. customer, project, team). Set them via the task drawer or kanban_create." },
        h(Label, { className: "text-xs text-muted-foreground" }, tx(t, "tenant", "Tenant")),
        h(Select, Object.assign({
          value: props.tenantFilter,
          className: "h-8",
        }, selectChangeHandler(props.setTenantFilter)),
          h(SelectOption, { value: "" }, tx(t, "allTenants", "All tenants")),
          tenants.map(function (tn) {
            return h(SelectOption, { key: tn, value: tn }, tn);
          }),
        ),
      ),
      h("div", { className: "flex flex-col gap-1",
                 title: "Filter by assigned Hermes profile. Profiles are the named agent identities that claim and work on tasks." },
        h(Label, { className: "text-xs text-muted-foreground" }, tx(t, "assignee", "Assignee")),
        h(Select, Object.assign({
          value: props.assigneeFilter,
          className: "h-8",
        }, selectChangeHandler(props.setAssigneeFilter)),
          h(SelectOption, { value: "" }, tx(t, "allProfiles", "All profiles")),
          assignees.map(function (a) {
            return h(SelectOption, { key: a, value: a }, a);
          }),
        ),
      ),
      h("label", { className: "flex items-center gap-2 text-xs",
                   title: "Include archived tasks in the board view. Archived tasks are hidden by default." },
        h(Checkbox, {
          checked: props.includeArchived,
          onCheckedChange: function (checked) { props.setIncludeArchived(checked === true); },
        }),
        tx(t, "showArchived", "Show archived"),
      ),
      h("label", { className: "flex items-center gap-2 text-xs",
                   title: "Group the Running column by assigned profile" },
        h(Checkbox, {
          checked: props.laneByProfile,
          onCheckedChange: function (checked) { props.setLaneByProfile(checked === true); },
        }),
        tx(t, "lanesByProfile", "Lanes by profile"),
      ),
      h("div", { className: "flex-1" }),
      h(Button, {
        onClick: props.onNudgeDispatch,
        size: "sm",
        title: "Wake the dispatcher to claim ready tasks now instead of waiting for the next tick. Use this after adding tasks if you want them picked up immediately.",
      }, tx(t, "nudgeDispatcher", "Nudge dispatcher")),
      h(Button, {
        onClick: props.onRefresh,
        size: "sm",
        title: "Reload the board from the database. The board auto-refreshes on task events; this is for forcing a re-read.",
      }, tx(t, "refresh", "Refresh")),
      h(Button, {
        onClick: function () {
          props.setSearch("");
          props.setTenantFilter("");
          props.setAssigneeFilter("");
          props.setIncludeArchived(false);
        },
        size: "sm",
        title: "Clear all active filters (search, tenant, assignee, archived).",
      }, tx(t, "clearFilters", "Clear filters")),
    );
  }

  // -------------------------------------------------------------------------
  // Bulk action bar (appears when >= 1 card is selected)
  // -------------------------------------------------------------------------

  function BulkActionBar(props) {
    const { t } = useI18n();
    const [assignee, setAssignee] = useState("");
    const [reclaimFirst, setReclaimFirst] = useState(false);
    const [priority, setPriority] = useState("");
    return h("div", { className: "hermes-kanban-bulk" },
      h("span", { className: "hermes-kanban-bulk-count" },
        `${props.count} ${tx(t, "selected", "selected")}`),
      h(Button, {
        onClick: function () { props.onApply({ status: "todo" }); },
        size: "sm",
        title: "Move selected tasks to Todo.",
      }, "→ todo"),
      h(Button, {
        onClick: function () { props.onApply({ status: "ready" }); },
        size: "sm",
        title: "Move selected tasks to Ready. Ready tasks are picked up by the dispatcher on the next tick.",
      }, "→ ready"),
      h(Button, {
        onClick: function () { props.onApply({ status: "blocked" },
          `Block ${props.count} task(s)?`); },
        size: "sm",
        title: "Block selected tasks. Releases any active claims.",
      }, "Block"),
      h(Button, {
        onClick: function () { props.onApply({ status: "ready" },
          `Unblock ${props.count} task(s)?`); },
        size: "sm",
        title: "Unblock selected tasks (promote to Ready).",
      }, "Unblock"),
      h(Button, {
        onClick: function () {
          props.onApply({ status: "done" },
            tx(t, "markDone", "Mark {n} task(s) as done?", { n: props.count }));
        },
        size: "sm",
        title: "Mark selected tasks as done. Releases any claims and unblocks dependent children. You'll be asked for a completion summary.",
      }, tx(t, "complete", "Complete")),
      h(Button, {
        onClick: function () {
          props.onApply({ archive: true },
            tx(t, "markArchived", "Archive {n} task(s)?", { n: props.count }));
        },
        size: "sm",
        title: "Archive selected tasks. They disappear from the default board view but remain in the database.",
      }, tx(t, "archive", "Archive")),
      h(Button, {
        onClick: function () {
          props.onDelete(props.count);
        },
        size: "sm",
        variant: "destructive",
        title: "Permanently delete selected tasks. This cannot be undone.",
      }, tx(t, "delete", "Delete")),
      h("div", { className: "hermes-kanban-bulk-priority",
                 title: "Set priority on selected tasks. Higher = claimed first." },
        h(Input, {
          type: "number",
          value: priority,
          onChange: function (e) { setPriority(e.target.value); },
          placeholder: tx(t, "priority", "pri"),
          className: "h-7 text-xs w-16",
        }),
        h(Button, {
          onClick: function () {
            if (priority === "") return;
            props.onApply({ priority: Number(priority) });
            setPriority("");
          },
          disabled: priority === "",
          size: "sm",
        }, tx(t, "setPriority", "Set priority")),
      ),
      h("div", { className: "hermes-kanban-bulk-reassign",
                 title: "Reassign selected tasks to a different Hermes profile. Pick a profile (or unassign) and click Apply." },
        h(Select, Object.assign({
          value: assignee,
          className: "h-7 text-xs",
        }, selectChangeHandler(setAssignee)),
          h(SelectOption, { value: "" }, "— reassign —"),
          h(SelectOption, { value: "__none__" }, "(unassign)"),
          props.assignees.map(function (a) {
            return h(SelectOption, { key: a, value: a }, a);
          }),
        ),
        h(Button, {
          onClick: function () {
            if (!assignee) return;
            props.onApply({ assignee: assignee === "__none__" ? "" : assignee, reclaim_first: reclaimFirst });
            setAssignee("");
          },
          disabled: !assignee,
          size: "sm",
          title: "Apply the selected assignee to all selected tasks.",
        }, tx(t, "apply", "Apply")),
      ),
      h("label", { className: "hermes-kanban-bulk-reclaim-first", title: "Reclaim any active claims before reassigning" },
        h(Checkbox, {
          checked: reclaimFirst,
          onCheckedChange: function (checked) { setReclaimFirst(checked === true); },
        }),
        "Reclaim first",
      ),
      h("div", { className: "flex-1" }),
      h(Button, {
        onClick: props.onSelectAllVisible,
        size: "sm",
        title: "Select all visible cards across columns.",
      }, "Select all visible"),
      h(Button, {
        onClick: props.onClear,
        size: "sm",
        title: "Deselect all tasks and hide this bar.",
      }, tx(t, "clear", "Clear")),
    );
  }

  // -------------------------------------------------------------------------
  // Trash Drop Zone
  // -------------------------------------------------------------------------

  function TrashDropZone(props) {
    const { t } = useI18n();
    const [dragOver, setDragOver] = useState(false);
    const zoneRef = useRef(null);

    useEffect(function () {
      if (!zoneRef.current) return undefined;
      const el = zoneRef.current;
      function onTouchDelete(e) {
        const taskId = e.detail && e.detail.taskId;
        if (taskId && props.onDelete) props.onDelete(taskId);
      }
      el.addEventListener("hermes-kanban:delete", onTouchDelete);
      return function () { el.removeEventListener("hermes-kanban:delete", onTouchDelete); };
    }, [props.onDelete]);

    const handleDragOver = function (e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      if (!dragOver) setDragOver(true);
    };
    const handleDragLeave = function () { setDragOver(false); };
    const handleDrop = function (e) {
      e.preventDefault();
      setDragOver(false);
      const taskId = e.dataTransfer.getData(MIME_TASK);
      if (!taskId) return;
      if (props.selectedIds && props.selectedIds.has(taskId) && props.selectedIds.size > 1) {
        if (window.confirm(tx(t, "trash.confirmMany", "Permanently delete {n} selected tasks? This cannot be undone.", { n: props.selectedIds.size }))) {
          const ids = Array.from(props.selectedIds);
          Promise.all(ids.map(function (id) { return props.onDelete(id); })).catch(function () {});
        }
      } else {
        props.onDelete(taskId);
      }
    };

    return h("div", {
      ref: zoneRef,
      "data-kanban-trash": "true",
      className: cn(
        "hermes-kanban-trash",
        dragOver ? "hermes-kanban-trash--drop" : "",
        props.draggingTaskId ? "hermes-kanban-trash--active" : "",
      ),
      onDragOver: handleDragOver,
      onDragLeave: handleDragLeave,
      onDrop: handleDrop,
    },
      h("span", { className: "hermes-kanban-trash-icon" }, "🗑️"),
      h("span", { className: "hermes-kanban-trash-label" },
        tx(t, "trash.dropHint", FALLBACK_TRASH.dropHint)),
    );
  }

  // -------------------------------------------------------------------------
  // Columns
  // -------------------------------------------------------------------------

  function BoardColumns(props) {
    const columnsRef = useRef(null);
    const panRef = useRef({ isPanning: false, startX: 0, scrollLeft: 0 });
    const [isPanning, setIsPanning] = useState(false);
    const [isScrollable, setIsScrollable] = useState(false);

    const checkScrollable = useCallback(function () {
      const el = columnsRef.current;
      setIsScrollable(!!el && el.scrollWidth > el.clientWidth + 1);
    }, []);

    useEffect(function () {
      checkScrollable();
      const el = columnsRef.current;
      if (!el) return undefined;
      if (typeof ResizeObserver !== "undefined") {
        const observer = new ResizeObserver(checkScrollable);
        observer.observe(el);
        return function () { observer.disconnect(); };
      }
      window.addEventListener("resize", checkScrollable);
      return function () { window.removeEventListener("resize", checkScrollable); };
    }, [checkScrollable, props.board]);

    const isPanBlockedTarget = useCallback(function (target) {
      if (!target) return true;
      if (target.closest && target.closest(".hermes-kanban-card")) return true;
      if (target.closest && target.closest(".hermes-kanban-column-add")) return true;
      if (target.closest && target.closest(".hermes-kanban-col-check")) return true;
      if (target.closest && target.closest("button,input,textarea,select,a,[role='button']")) return true;
      return false;
    }, []);

    const stopPan = useCallback(function () {
      const el = columnsRef.current;
      if (!panRef.current.isPanning) return;
      panRef.current.isPanning = false;
      setIsPanning(false);
      if (el) {
        // Keep cursor feedback instant even before React flushes the state update.
        el.classList.remove("hermes-kanban-columns--panning");
        el.style.userSelect = "";
      }
      if (panRef.current.cleanup) panRef.current.cleanup();
      panRef.current.cleanup = null;
    }, []);

    useEffect(function () {
      return function () { stopPan(); };
    }, [stopPan]);

    const handleMouseDown = useCallback(function (e) {
      if (e.button !== 0) return;
      if (isPanBlockedTarget(e.target)) return;
      const el = columnsRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      // Preserve the native horizontal scrollbar as a fallback; grab-pan starts above it.
      if (e.clientY >= rect.bottom - 20) return;
      if (el.scrollWidth <= el.clientWidth) return;

      panRef.current.isPanning = true;
      panRef.current.startX = e.clientX;
      panRef.current.scrollLeft = el.scrollLeft;
      setIsPanning(true);
      el.classList.add("hermes-kanban-columns--panning");
      el.style.userSelect = "none";

      function onMouseMove(ev) {
        if (!panRef.current.isPanning) return;
        const dx = ev.clientX - panRef.current.startX;
        el.scrollLeft = panRef.current.scrollLeft - dx;
        ev.preventDefault();
      }
      function onMouseUp() { stopPan(); }

      if (panRef.current.cleanup) panRef.current.cleanup();
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp, { once: true });
      window.addEventListener("blur", onMouseUp, { once: true });
      panRef.current.cleanup = function () {
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
        window.removeEventListener("blur", onMouseUp);
      };
      e.preventDefault();
    }, [isPanBlockedTarget, stopPan]);

    const handleDragStart = useCallback(function (e) {
      const card = e.target.closest && e.target.closest(".hermes-kanban-card");
      if (!card) return;
      const taskId = card.getAttribute("data-task-id");
      if (taskId && props.onDragStart) props.onDragStart(taskId);
    }, [props.onDragStart]);
    const handleDragEnd = useCallback(function () {
      if (props.onDragEnd) props.onDragEnd();
    }, [props.onDragEnd]);
    return h("div", {
      ref: columnsRef,
      role: "region",
      "aria-label": pt("board.region"),
      className: cn(
        "hermes-kanban-columns",
        isScrollable ? "hermes-kanban-columns--scrollable" : "",
        isPanning ? "hermes-kanban-columns--panning" : "",
      ),
      onDragStart: handleDragStart,
      onDragEnd: handleDragEnd,
      onMouseDown: handleMouseDown,
    },
      props.board.columns.map(function (col) {
        return h(Column, {
          key: col.name,
          column: col,
          boardMeta: props.boardMeta,
          laneByProfile: props.laneByProfile,
          selectedIds: props.selectedIds,
          failedIds: props.failedIds,
          draggingTaskId: props.draggingTaskId,
          toggleSelected: props.toggleSelected,
          toggleRange: props.toggleRange,
          selectAllInColumn: props.selectAllInColumn,
          onMove: props.onMove,
          onMoveSelected: props.onMoveSelected,
          onOpen: props.onOpen,
          onCreate: props.onCreate,
          onLoadMore: props.onLoadMore,
          allTasks: props.allTasks,
        });
      }),
      h(TrashDropZone, {
        draggingTaskId: props.draggingTaskId,
        selectedIds: props.selectedIds,
        onDelete: props.onDelete,
      }),
    );
  }

  function Column(props) {
    const { t } = useI18n();
    const [dragOver, setDragOver] = useState(false);
    const [showCreate, setShowCreate] = useState(false);
    const colRef = useRef(null);

    // Listen for our synthetic touch-drop events from attachTouchDrag().
    useEffect(function () {
      if (!colRef.current) return undefined;
      const el = colRef.current;
      function onTouchDrop(e) {
        if (e.detail && e.detail.status === props.column.name) {
          const taskId = e.detail.taskId;
          if (props.selectedIds && props.selectedIds.has(taskId) && props.selectedIds.size > 1 && props.onMoveSelected) {
            props.onMoveSelected(props.column.name);
          } else {
            props.onMove(taskId, props.column.name);
          }
        }
      }
      el.addEventListener("hermes-kanban:drop", onTouchDrop);
      return function () { el.removeEventListener("hermes-kanban:drop", onTouchDrop); };
    }, [props.column.name, props.onMove, props.selectedIds, props.onMoveSelected]);

    const handleDragOver = function (e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      if (!dragOver) setDragOver(true);
    };
    const handleDragLeave = function () { setDragOver(false); };
    const handleDrop = function (e) {
      e.preventDefault();
      setDragOver(false);
      const taskId = e.dataTransfer.getData(MIME_TASK);
      if (!taskId) return;
      if (props.selectedIds && props.selectedIds.has(taskId) && props.selectedIds.size > 1) {
        if (props.onMoveSelected) props.onMoveSelected(props.column.name);
      } else {
        props.onMove(taskId, props.column.name);
      }
    };

    const lanes = useMemo(function () {
      if (!props.laneByProfile || props.column.name !== "running") return null;
      const byProfile = {};
      for (const tk of props.column.tasks) {
        const key = tk.assignee || "(unassigned)";
        (byProfile[key] = byProfile[key] || []).push(tk);
      }
      return Object.keys(byProfile).sort().map(function (k) {
        return { assignee: k, tasks: byProfile[k] };
      });
    }, [props.column, props.laneByProfile]);

    const colHelp = getColumnHelp(t, props.column.name);
    const colLabel = getColumnLabel(t, props.column.name);

    return h("div", {
      ref: colRef,
      "data-kanban-column": props.column.name,
      className: cn(
        "hermes-kanban-column",
        dragOver ? "hermes-kanban-column--drop" : "",
      ),
      onDragOver: handleDragOver,
      onDragLeave: handleDragLeave,
      onDrop: handleDrop,
    },
      h("div", { className: "hermes-kanban-column-header",
                 title: colHelp || "" },
        h(Checkbox, {
          className: "hermes-kanban-col-check",
          title: "Select all tasks in this column",
          "aria-label": `Select all tasks in ${colLabel || props.column.name}`,
          checked: props.column.tasks.length > 0 && props.column.tasks.every(function (t) { return props.selectedIds.has(t.id); }),
          onCheckedChange: function () {
            if (props.selectAllInColumn) props.selectAllInColumn(props.column.name);
          },
          onClick: function (e) { e.stopPropagation(); },
        }),
        h("span", { className: cn("hermes-kanban-dot", COLUMN_DOT[props.column.name]) }),
        h("span", { className: "hermes-kanban-column-label" },
          colLabel || props.column.name),
        h("span", { className: "hermes-kanban-column-count",
                    title: `${props.column.tasks.length} task${props.column.tasks.length === 1 ? "" : "s"} in this column` },
          props.column.tasks.length),
        h("button", {
          type: "button",
          className: "hermes-kanban-column-add",
          title: tx(t, "createTask", "Create task in this column"),
          onClick: function () { setShowCreate(function (v) { return !v; }); },
        }, showCreate ? "×" : "+"),
      ),
      h("div", { className: "hermes-kanban-column-sub" },
        colHelp || ""),
      showCreate ? h(InlineCreate, {
        columnName: props.column.name,
        allTasks: props.allTasks,
        defaultWorkspaceKind: (props.boardMeta && props.boardMeta.default_workspace_kind) || "scratch",
        defaultWorkspacePath: (props.boardMeta && props.boardMeta.default_workdir) || "",
        onSubmit: function (body) {
          props.onCreate(body).then(function () { setShowCreate(false); });
        },
        onCancel: function () { setShowCreate(false); },
      }) : null,
      h("div", {
        className: "hermes-kanban-column-body",
        role: "list",
        "aria-label": colLabel,
      },
        props.column.tasks.length === 0
          ? h("div", { className: "hermes-kanban-empty" }, tx(t, "noTasks", "— no tasks —"))
          : lanes
            ? lanes.map(function (lane) {
                return h("div", { key: lane.assignee, className: "hermes-kanban-lane", role: "group", "aria-label": lane.assignee },
                  h("div", { className: "hermes-kanban-lane-head" },
                    h("span", { className: "hermes-kanban-lane-name" }, lane.assignee),
                    h("span", { className: "hermes-kanban-lane-count" }, lane.tasks.length),
                  ),
                  lane.tasks.map(function (tk) {
                    return h(TaskCard, {
                      key: tk.id, task: tk,
                      selected: props.selectedIds.has(tk.id),
                      failed: props.failedIds && props.failedIds.has(tk.id),
                      draggingTaskId: props.draggingTaskId,
                      draggingSource: props.draggingTaskId && props.selectedIds.has(props.draggingTaskId) && props.selectedIds.size > 1 && props.selectedIds.has(tk.id),
                      toggleSelected: props.toggleSelected,
                      toggleRange: props.toggleRange,
                      onOpen: props.onOpen,
                      onMove: props.onMove,
                    });
                  }),
                );
              })
            : props.column.tasks.map(function (tk) {
                return h(TaskCard, {
                  key: tk.id, task: tk,
                  selected: props.selectedIds.has(tk.id),
                  failed: props.failedIds && props.failedIds.has(tk.id),
                  draggingTaskId: props.draggingTaskId,
                  draggingSource: props.draggingTaskId && props.selectedIds.has(props.draggingTaskId) && props.selectedIds.size > 1 && props.selectedIds.has(tk.id),
                  toggleSelected: props.toggleSelected,
                  toggleRange: props.toggleRange,
                  onOpen: props.onOpen,
                  onMove: props.onMove,
                });
              }),
      ),
      props.column.has_more ? h("button", {
        type: "button",
        className: "pmo-load-more",
        onClick: function () {
          if (props.onLoadMore) props.onLoadMore(props.column.name);
        },
      }, pt("board.loadMore", Math.min(
        PMO_COLUMN_PAGE_SIZE,
        props.column.total_filtered - props.column.tasks.length,
      ))) : null,
    );
  }

  // -------------------------------------------------------------------------
  // Card
  // -------------------------------------------------------------------------

  // Staleness tiers — amber after a grace window, red when clearly stuck.
  // Values below are seconds.
  const STALENESS = {
    ready:   { amber: 1 * 60 * 60,   red: 24 * 60 * 60 },
    running: { amber: 10 * 60,       red: 60 * 60 },
    blocked: { amber: 1 * 60 * 60,   red: 24 * 60 * 60 },
    todo:    { amber: 7 * 24 * 60 * 60, red: 30 * 24 * 60 * 60 },
  };

  function stalenessClass(task) {
    if (!task || !task.age) return "";
    const age = task.status === "running"
      ? task.age.started_age_seconds
      : task.age.created_age_seconds;
    const tier = STALENESS[task.status];
    if (!tier || age == null) return "";
    if (age >= tier.red)   return "hermes-kanban-card--stale-red";
    if (age >= tier.amber) return "hermes-kanban-card--stale-amber";
    return "";
  }

  function TaskCard(props) {
    const { t: i18n } = useI18n();
    const t = props.task;
    const cardRef = useRef(null);

    useEffect(function () {
      return attachTouchDrag(cardRef.current, t.id);
    }, [t.id]);

    const handleDragStart = function (e) {
      e.dataTransfer.setData(MIME_TASK, t.id);
      e.dataTransfer.effectAllowed = "move";
      const selectedCards = document.querySelectorAll(".hermes-kanban-card--selected");
      if (selectedCards.length > 1 && props.selected) {
        const ghost = document.createElement("div");
        ghost.className = "hermes-kanban-drag-ghost";
        ghost.textContent = selectedCards.length + " cards";
        document.body.appendChild(ghost);
        e.dataTransfer.setDragImage(ghost, 0, 0);
        requestAnimationFrame(function () {
          if (ghost.parentNode) document.body.removeChild(ghost);
        });
      }
    };
    const handleClick = function (e) {
      if (e.shiftKey) {
        e.preventDefault();
        e.stopPropagation();
        if (props.toggleRange) props.toggleRange(t.id);
        return;
      }
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        e.stopPropagation();
        props.toggleSelected(t.id, true);
        return;
      }
      props.onOpen(t.id);
    };
    const handleKeyDown = function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        props.onOpen(t.id);
      }
      if (e.key === "Escape") {
        if (props.toggleSelected) props.toggleSelected(t.id, false);
      }
    };
    const handleCheckedChange = function () {
      props.toggleSelected(t.id, true);
    };

    const progress = t.progress;
    const needsAssignee = t.status === "ready" && !t.assignee;

    return h("div", { role: "listitem" },
      h("div", {
        ref: cardRef,
      "data-task-id": t.id,
      className: cn(
        "hermes-kanban-card",
        props.selected ? "hermes-kanban-card--selected" : "",
        props.failed ? "hermes-kanban-card--failed" : "",
        props.draggingSource ? "hermes-kanban-card--dragging-source" : "",
        stalenessClass(t),
      ),
      draggable: true,
      tabIndex: 0,
      role: "button",
      "aria-label": `${t.title || "untitled"} — ${t.id} — ${t.status}`,
      onDragStart: handleDragStart,
      onClick: handleClick,
      onKeyDown: handleKeyDown,
    },
      h(Card, null,
        h(CardContent, { className: "hermes-kanban-card-content" },
          h("div", { className: "hermes-kanban-card-row" },
            h("label", {
              className: "hermes-kanban-card-check-wrap",
              title: tx(i18n, "selectForBulk", "Select for bulk actions"),
              onClick: function (e) { e.stopPropagation(); },
            },
              h(Checkbox, {
                className: "hermes-kanban-card-check",
                checked: props.selected,
                onCheckedChange: handleCheckedChange,
                onClick: function (e) { e.stopPropagation(); },
                "aria-label": `Select task ${t.id}`,
              }),
            ),
            h("span", { className: "hermes-kanban-card-id",
                        title: `Task id: ${t.id}. Use this id with kanban_show, /kanban show, or hermes kanban show.` }, t.id),
            t.warnings && t.warnings.count > 0
              ? h("span", {
                  className: cn(
                    "hermes-kanban-warning-badge",
                    "hermes-kanban-warning-badge--" + (t.warnings.highest_severity || "warning"),
                  ),
                  title: (
                    `${t.warnings.count} active diagnostic` +
                    (t.warnings.count === 1 ? "" : "s") +
                    ` (severity: ${t.warnings.highest_severity || "warning"}). ` +
                    `Click to open for details.`
                  ),
                }, t.warnings.highest_severity === "critical" ? "!!!" :
                   t.warnings.highest_severity === "error" ? "!!" : "⚠")
              : null,
            t.priority > 0
              ? h(Badge, { className: "hermes-kanban-priority",
                           title: `Priority ${t.priority}. Higher-priority tasks are claimed first by the dispatcher.` }, `P${t.priority}`)
              : null,
            t.tenant
              ? h(Badge, { variant: "outline", className: "hermes-kanban-tag",
                           title: `Tenant: ${t.tenant}. Free-form tag for grouping tasks (customer, project, team).` }, t.tenant)
              : null,
            progress
              ? h("span", {
                  className: cn(
                    "hermes-kanban-progress",
                    progress.done === progress.total ? "hermes-kanban-progress--full" : "",
                  ),
                  title: `${progress.done} of ${progress.total} child tasks done`,
                }, `${progress.done}/${progress.total}`)
              : null,
            needsAssignee
              ? h(Badge, {
                  variant: "outline",
                  className: "hermes-kanban-needs-assignee",
                  title: tx(i18n, "needsAssigneeHint", "Dependencies are satisfied, but the dispatcher skips this task until you assign a profile."),
                }, tx(i18n, "needsAssignee", "Needs assignee"))
              : null,
          ),
          h("div", { className: "hermes-kanban-card-title" },
            t.title || tx(i18n, "untitled", "(untitled)")),
          h("div", { className: "hermes-kanban-card-row hermes-kanban-card-meta" },
            t.assignee
              ? h("span", { className: "hermes-kanban-assignee",
                            title: `Assigned to Hermes profile @${t.assignee}` }, "@", t.assignee)
              : h("span", { className: "hermes-kanban-unassigned",
                            title: needsAssignee
                              ? tx(i18n, "needsAssigneeHint", "Dependencies are satisfied, but the dispatcher skips this task until you assign a profile.")
                              : "No profile assigned." },
                  tx(i18n, "unassigned", "unassigned")),
            t.comment_count > 0
              ? h("span", { className: "hermes-kanban-count",
                            title: `${t.comment_count} comment${t.comment_count === 1 ? "" : "s"} on this task` }, "💬 ", t.comment_count)
              : null,
            t.link_counts && (t.link_counts.parents + t.link_counts.children) > 0
              ? h("span", { className: "hermes-kanban-count",
                            title: `${t.link_counts.parents} parent${t.link_counts.parents === 1 ? "" : "s"}, ${t.link_counts.children} child${t.link_counts.children === 1 ? "" : "ren"}. Children stay blocked until their parent is done.` },
                  "↔ ", t.link_counts.parents + t.link_counts.children)
              : null,
            h("span", { className: "hermes-kanban-ago",
                        title: t.created_at ? `Created ${t.created_at}` : "" },
              timeAgo ? timeAgo(t.created_at) : ""),
            h("select", {
              className: "pmo-card-move",
              defaultValue: "",
              "aria-label": pt("board.moveTo", t.title || t.id),
              title: pt("board.moveTo", t.title || t.id),
              onClick: function (event) { event.stopPropagation(); },
              onChange: function (event) {
                event.stopPropagation();
                const status = event.target.value;
                event.target.value = "";
                if (status && props.onMove) props.onMove(t.id, status);
              },
            },
              h("option", { value: "" }, "↪"),
              ["triage", "todo", "ready", "blocked", "done", "archived"].filter(function (status) {
                return status !== t.status;
              }).map(function (status) {
                return h("option", { key: status, value: status }, getColumnLabel(i18n, status));
              })),
          ),
        ),
      ),
      ),
    );
  }

  // -------------------------------------------------------------------------
  // Create-task dialog (modal, with parent selector)
  //
  // Launched from a column's [+] button. Was an inline form squeezed into
  // the ~280px column (8 fields, unlabeled, no room to breathe); now a
  // centered modal reusing the hermes-kanban-dialog chrome so the form is
  // resizable-window friendly and every field has a visible label.
  // -------------------------------------------------------------------------

  function InlineCreate(props) {
    const { t } = useI18n();
    const [title, setTitle] = useState("");
    const [assignee, setAssignee] = useState("");
    const [priority, setPriority] = useState(0);
    const [parent, setParent] = useState("");
    const [skills, setSkills] = useState("");
    // A board with a configured workdir defaults to a persistent workspace:
    // worktree for git repositories, dir for ordinary directories. Boards
    // without one keep scratch for disposable research and ops tasks.
    const defaultWorkspaceKind = props.defaultWorkspaceKind || "scratch";
    const defaultWorkspacePath = props.defaultWorkspacePath || "";
    const [workspaceKind, setWorkspaceKind] = useState(defaultWorkspaceKind);
    const [workspacePath, setWorkspacePath] = useState(defaultWorkspacePath);
    // Goal-mode: when on, the dispatched worker runs the Ralph-style /goal
    // loop — a judge re-checks the card after each turn and the worker keeps
    // going in the same session until done, or the turn budget runs out
    // (which blocks the card for review). goalMaxTurns is optional; blank
    // = backend default.
    const [goalMode, setGoalMode] = useState(false);
    const [goalMaxTurns, setGoalMaxTurns] = useState("");

    const submit = function () {
      const trimmed = title.trim();
      if (!trimmed) return;
      const body = {
        title: trimmed,
        assignee: assignee.trim() || null,
        priority: Number(priority) || 0,
        triage: props.columnName === "triage",
      };
      if (parent) body.parents = [parent];
      // Parse comma-separated skills into a clean list. Blank = no
      // extras (omit key so backend leaves it null). The dispatcher
      // always auto-loads kanban-worker; these are extras on top.
      const skillList = skills
        .split(",")
        .map(function (s) { return s.trim(); })
        .filter(function (s) { return s.length > 0; });
      if (skillList.length > 0) body.skills = skillList;
      // Only send workspace_kind when it's non-default. Keeps the request
      // shape small and interoperable with older dispatcher versions.
      if (workspaceKind && workspaceKind !== "scratch") {
        body.workspace_kind = workspaceKind;
      }
      const wpTrim = workspacePath.trim();
      if (wpTrim) body.workspace_path = wpTrim;
      // Goal-mode toggle. Only send the keys when enabled so the request
      // shape stays small and old dispatchers ignore it cleanly.
      if (goalMode) {
        body.goal_mode = true;
        const gmt = parseInt(goalMaxTurns, 10);
        if (Number.isFinite(gmt) && gmt > 0) body.goal_max_turns = gmt;
      }
      props.onSubmit(body);
      setTitle(""); setAssignee(""); setPriority(0); setParent(""); setSkills("");
      setWorkspaceKind(defaultWorkspaceKind); setWorkspacePath(defaultWorkspacePath);
      setGoalMode(false); setGoalMaxTurns("");
    };

    const showPathInput = workspaceKind !== "scratch";
    const pathPlaceholder = workspaceKind === "dir"
      ? tx(t, "workspacePathDir", "workspace path (required without a board workdir)")
      : tx(t, "workspacePathOptional",
          "repository path (optional when the board has a workdir)");

    const fieldLabel = function (text, hint) {
      return h(Label, { className: "text-xs" }, text,
        hint ? h("span", { className: "text-muted-foreground" }, " ", hint) : null);
    };

    return h("div", {
      className: "hermes-kanban-dialog-backdrop",
      onClick: function (e) { if (e.target === e.currentTarget) props.onCancel(); },
      onKeyDown: function (e) { if (e.key === "Escape") props.onCancel(); },
    },
      h("form", {
        className: "hermes-kanban-dialog hermes-kanban-create-dialog",
        onSubmit: function (e) { e.preventDefault(); submit(); },
      },
        h("div", { className: "hermes-kanban-dialog-title" },
          tx(t, "newTaskTitle", "New task — {column}",
            { column: getColumnLabel(t, props.columnName) || props.columnName })),
        h("div", { className: "flex flex-col gap-3" },
          h("div", { className: "flex flex-col gap-1" },
            fieldLabel(tx(t, "taskTitleLabel", "Title")),
            h("textarea", {
              value: title,
              onChange: function (e) { setTitle(e.target.value); },
              onKeyDown: function (e) {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
              },
              placeholder: props.columnName === "triage"
                ? tx(t, "triagePlaceholder", "Rough idea — AI will spec it…")
                : tx(t, "taskTitlePlaceholder", "New task title…"),
              autoFocus: true,
              className: "text-sm min-h-[3rem] max-h-48 resize-y w-full border border-input bg-transparent px-2 py-1 rounded-md focus:outline-none focus:ring-2 focus:ring-ring",
              rows: 3,
            }),
          ),
          h("div", { className: "flex gap-2" },
            h("div", { className: "flex flex-col gap-1 flex-1" },
              fieldLabel(props.columnName === "triage"
                ? tx(t, "specifier", "specifier")
                : tx(t, "assigneeLabel", "Assignee"),
                tx(t, "assigneeLabelHint", "(blank = dispatcher picks)")),
              h(Input, {
                value: assignee,
                onChange: function (e) { setAssignee(e.target.value); },
                placeholder: props.columnName === "triage"
                  ? tx(t, "specifier", "specifier")
                  : tx(t, "assigneePlaceholder", "assignee"),
                className: "h-8 text-sm",
                title: props.columnName === "triage"
                  ? "Hermes profile that will spec this task (default: the dispatcher's configured specifier). Leave blank to let the dispatcher pick."
                  : "Hermes profile to assign. Leave blank and the dispatcher will pick from available profiles when the task is Ready.",
                style: { textTransform: "none" },
                autoCapitalize: "none",
                autoCorrect: "off",
                spellCheck: false,
              }),
            ),
            h("div", { className: "flex flex-col gap-1 w-20" },
              fieldLabel(tx(t, "priority", "Priority")),
              h(Input, {
                type: "number",
                value: priority,
                onChange: function (e) { setPriority(e.target.value); },
                placeholder: "pri",
                className: "h-8 text-sm",
                title: "Priority. Higher-priority tasks are claimed first by the dispatcher. 0 = default.",
              }),
            ),
          ),
          h("div", { className: "flex flex-col gap-1" },
            fieldLabel(tx(t, "skillsLabel", "Skills"),
              tx(t, "skillsLabelHint", "(optional, comma-separated)")),
            h(Input, {
              value: skills,
              onChange: function (e) { setSkills(e.target.value); },
              placeholder: tx(t, "skillsPlaceholder",
                "skills (optional, comma-separated): translation, github-code-review"),
              title: "Force-load these skills into the worker (in addition to the built-in kanban-worker).",
              className: "h-8 text-sm",
            }),
          ),
          h("div", { className: "flex flex-col gap-1" },
            fieldLabel(tx(t, "workspace", "Workspace")),
            h("div", { className: "flex gap-2" },
              h(Select, Object.assign({
                value: workspaceKind,
                title: "Choose whether task files are temporary or preserved after completion.",
                className: "h-8 text-sm flex-1",
              }, selectChangeHandler(setWorkspaceKind)),
                h(SelectOption, { value: "scratch" },
                  tx(t, "workspaceScratch", "Temporary — deleted on completion")),
                h(SelectOption, { value: "worktree" },
                  tx(t, "workspaceWorktree", "Git worktree — preserved")),
                h(SelectOption, { value: "dir" },
                  tx(t, "workspaceDir", "Directory — preserved")),
              ),
              showPathInput ? h(Input, {
                value: workspacePath,
                onChange: function (e) { setWorkspacePath(e.target.value); },
                placeholder: pathPlaceholder,
                className: "h-8 text-sm flex-1",
              }) : null,
            ),
            workspaceKind === "scratch" ? h("div", {
              className: "text-xs text-destructive",
              role: "alert",
            }, tx(t, "workspaceScratchWarning",
              "This workspace and any files left in it are deleted when the task completes.")) : null,
          ),
          h("div", { className: "flex flex-col gap-1" },
            fieldLabel(tx(t, "parentLabel", "Parent task"),
              tx(t, "parentLabelHint", "(child stays blocked until the parent is done)")),
            h(Select, Object.assign({
              value: parent,
              className: "h-8 text-sm",
              title: "Optional parent task. A child stays blocked in its current column until the parent is marked done.",
            }, selectChangeHandler(setParent)),
              h(SelectOption, { value: "" }, tx(t, "noParent", "— no parent —")),
              (props.allTasks || []).map(function (task) {
                return h(SelectOption, { key: task.id, value: task.id },
                  `${task.id} — ${(task.title || "").slice(0, 50)}`);
              }),
            ),
          ),
          h("div", { className: "flex gap-2 items-center" },
            h("label", {
              className: "flex items-center gap-1.5 text-xs cursor-pointer select-none",
              title: "Goal mode: the worker keeps going in the same session until a judge agrees the card is done (or the turn budget runs out, which blocks it for review). Best for open-ended cards one shot rarely finishes.",
            },
              h("input", {
                type: "checkbox",
                checked: goalMode,
                onChange: function (e) { setGoalMode(!!e.target.checked); },
                className: "h-3.5 w-3.5 accent-current",
              }),
              tx(t, "goalMode", "goal mode"),
            ),
            goalMode ? h(Input, {
              type: "number",
              value: goalMaxTurns,
              onChange: function (e) { setGoalMaxTurns(e.target.value); },
              placeholder: tx(t, "goalMaxTurns", "max turns (default 20)"),
              className: "h-8 text-sm w-44",
              title: "Turn budget for the goal loop. Blank = backend default (20).",
              min: 1,
            }) : null,
          ),
        ),
        h("div", { className: "hermes-kanban-dialog-actions" },
          h(Button, {
            type: "button",
            onClick: props.onCancel,
            size: "sm",
          }, tx(t, "cancel", "Cancel")),
          h(Button, {
            type: "submit",
            size: "sm",
            disabled: !title.trim(),
          }, tx(t, "create", "Create")),
        ),
      ),
    );
  }

  // -------------------------------------------------------------------------
  // Task drawer
  // -------------------------------------------------------------------------

  function TaskDrawer(props) {
    const { t } = useI18n();
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [err, setErr] = useState(null);
    // Surface PATCH failures (e.g. 409 "parent not done") right next to
    // the drawer's action row — without it, the drawer's only error
    // surface (``err``) is hidden behind the loaded ``data`` and the
    // Ready/Block/Complete buttons feel like no-ops.  See #26744.
    const [patchErr, setPatchErr] = useState(null);
    const [newComment, setNewComment] = useState("");
    const [uploadBusy, setUploadBusy] = useState(false);
    const [uploadErr, setUploadErr] = useState(null);
    const [editing, setEditing] = useState(false);
    // Home-channel notification toggles. homeChannels is the list of platforms
    // the user has a /sethome on; each entry has a `subscribed` bool telling
    // us whether this task is currently subscribed via that platform's home.
    const [homeChannels, setHomeChannels] = useState([]);
    const [homeBusy, setHomeBusy] = useState({});
    const boardSlug = props.boardSlug;
    const drawerRef = useRef(null);

    const load = useCallback(function () {
      return SDK.fetchJSON(withBoard(`${API}/tasks/${encodeURIComponent(props.taskId)}`, boardSlug))
        .then(function (d) { setData(d); setErr(null); setPatchErr(null); })
        .catch(function (e) { setErr(String(e.message || e)); })
        .finally(function () { setLoading(false); });
    }, [props.taskId, boardSlug]);

    const loadHomeChannels = useCallback(function () {
      const qs = new URLSearchParams({ task_id: props.taskId });
      const url = withBoard(`${API}/home-channels?${qs}`, boardSlug);
      return SDK.fetchJSON(url)
        .then(function (d) { setHomeChannels(d.home_channels || []); })
        .catch(function () { /* silent — endpoint optional on older gateways */ });
    }, [props.taskId, boardSlug]);

    // Reload when the WS stream reports new events for this task id
    // (completion, block, crash, etc. — anything that'd make the drawer
    // show stale data if we only loaded on mount).
    useEffect(function () { load(); }, [load, props.eventTick]);
    useEffect(function () { loadHomeChannels(); }, [loadHomeChannels]);
    useEffect(function () {
      const drawer = drawerRef.current;
      if (drawer) {
        const first = drawer.querySelector("button, [href], input, textarea, select, [tabindex]:not([tabindex='-1'])");
        if (first) first.focus();
      }
      function onKey(e) {
        if (e.key === "Escape" && !editing) { props.onClose(); return; }
        if (e.key !== "Tab" || !drawerRef.current) return;
        const controls = Array.from(drawerRef.current.querySelectorAll(
          "button:not(:disabled), [href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex='-1'])"
        )).filter(function (item) { return item.offsetParent !== null; });
        if (!controls.length) { e.preventDefault(); drawerRef.current.focus(); return; }
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
      window.addEventListener("keydown", onKey);
      return function () { window.removeEventListener("keydown", onKey); };
    }, [props.onClose, editing]);

    const handleComment = function () {
      const body = newComment.trim();
      if (!body) return;
      SDK.fetchJSON(withBoard(`${API}/tasks/${encodeURIComponent(props.taskId)}/comments`, boardSlug), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body }),
      }).then(function () {
        setNewComment("");
        load();
        props.onRefresh();
      }).catch(function (e) { setErr(String(e.message || e)); });
    };

    // File upload uses raw fetch (not SDK.fetchJSON, which JSON-encodes)
    // so the browser sets the multipart boundary. Auth rides the session
    // cookie + bearer token, matching the rest of the dashboard.
    const handleUpload = function (fileList) {
      const files = Array.prototype.slice.call(fileList || []);
      if (!files.length) return;
      setUploadBusy(true);
      setUploadErr(null);
      const url = withBoard(`${API}/tasks/${encodeURIComponent(props.taskId)}/attachments`, boardSlug);
      // Upload sequentially so a partial failure leaves a clear state.
      let chain = Promise.resolve();
      files.forEach(function (f) {
        chain = chain.then(function () {
          const fd = new FormData();
          fd.append("file", f, f.name);
          // SDK.authedFetch handles auth in BOTH modes (loopback token header /
          // gated cookie) and applies the dashboard base-path prefix. The old
          // hand-rolled Authorization:Bearer + credentials:'same-origin' sent
          // an empty token and 401'd in gated mode.
          return SDK.authedFetch(url, { method: "POST", body: fd })
            .then(function (resp) {
              if (!resp.ok) {
                return resp.text().then(function (txt) {
                  throw new Error(parseApiErrorMessage(new Error(resp.status + ": " + txt)));
                });
              }
            });
        });
      });
      chain.then(function () {
        load();
        props.onRefresh();
      }).catch(function (e) {
        setUploadErr(String(e.message || e));
      }).finally(function () {
        setUploadBusy(false);
      });
    };

    const handleDeleteAttachment = function (attachmentId) {
      return SDK.fetchJSON(withBoard(`${API}/attachments/${attachmentId}`, boardSlug), { method: "DELETE" })
        .then(function () { load(); props.onRefresh(); })
        .catch(function (e) { setUploadErr(String(e.message || e)); });
    };

    const doPatch = function (patch, opts) {
      if (opts && opts.confirm && !window.confirm(opts.confirm)) {
        return Promise.resolve();
      }
      const finalPatch = withCompletionSummary(patch, 1);
      if (!finalPatch) return Promise.resolve();
      setPatchErr(null);
      return SDK.fetchJSON(withBoard(`${API}/tasks/${encodeURIComponent(props.taskId)}`, boardSlug), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(finalPatch),
      }).then(function () { load(); props.onRefresh(); })
        .catch(function (e) { setPatchErr(parseApiErrorMessage(e)); });
    };

    // Triage specifier — calls the auxiliary LLM to flesh out a rough
    // idea in the Triage column into a concrete spec (title + body with
    // goal, approach, acceptance criteria) and promotes it to todo.
    // Not a PATCH: runs through a dedicated POST endpoint because the
    // LLM call can take tens of seconds, and its outcome is richer than
    // a status flip (may update title AND body AND emit an audit
    // comment — or fail with a human-readable reason that the UI
    // surfaces inline without treating it as an HTTP error).
    const doSpecify = function () {
      return SDK.fetchJSON(
        withBoard(`${API}/tasks/${encodeURIComponent(props.taskId)}/specify`, boardSlug),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        }
      ).then(function (res) {
        load();
        props.onRefresh();
        return res;
      });
    };

    // POST /tasks/:id/decompose — fan a triage task out into a graph
    // of child tasks routed to specialist profiles by description.
    // Refreshes both the drawer (so the user sees the root flip to
    // todo) and the board (so the new children appear in the columns).
    const doDecompose = function () {
      return SDK.fetchJSON(
        withBoard(`${API}/tasks/${encodeURIComponent(props.taskId)}/decompose`, boardSlug),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        }
      ).then(function (res) {
        load();
        props.onRefresh();
        return res;
      });
    };

    const addLink = function (parentId) {
      return SDK.fetchJSON(withBoard(`${API}/links`, boardSlug), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parent_id: parentId, child_id: props.taskId }),
      }).then(function () { load(); props.onRefresh(); })
        .catch(function (e) { setErr(String(e.message || e)); });
    };
    const removeLink = function (parentId) {
      const qs = new URLSearchParams({ parent_id: parentId, child_id: props.taskId });
      return SDK.fetchJSON(withBoard(`${API}/links?${qs}`, boardSlug), { method: "DELETE" })
        .then(function () { load(); props.onRefresh(); })
        .catch(function (e) { setErr(String(e.message || e)); });
    };
    const addChild = function (childId) {
      return SDK.fetchJSON(withBoard(`${API}/links`, boardSlug), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parent_id: props.taskId, child_id: childId }),
      }).then(function () { load(); props.onRefresh(); })
        .catch(function (e) { setErr(String(e.message || e)); });
    };
    const removeChild = function (childId) {
      const qs = new URLSearchParams({ parent_id: props.taskId, child_id: childId });
      return SDK.fetchJSON(withBoard(`${API}/links?${qs}`, boardSlug), { method: "DELETE" })
        .then(function () { load(); props.onRefresh(); })
        .catch(function (e) { setErr(String(e.message || e)); });
    };

    const toggleHomeSubscription = function (platform, currentlySubscribed) {
      // Optimistic flip + busy flag to keep double-clicks idempotent.
      setHomeBusy(function (b) { return Object.assign({}, b, { [platform]: true }); });
      setHomeChannels(function (list) {
        return list.map(function (h) {
          return h.platform === platform
            ? Object.assign({}, h, { subscribed: !currentlySubscribed })
            : h;
        });
      });
      const method = currentlySubscribed ? "DELETE" : "POST";
      const url = withBoard(
        `${API}/tasks/${encodeURIComponent(props.taskId)}/home-subscribe/${encodeURIComponent(platform)}`,
        boardSlug,
      );
      return SDK.fetchJSON(url, { method: method })
        .then(function () { return loadHomeChannels(); })
        .catch(function (e) {
          // Revert optimistic flip on failure.
          setHomeChannels(function (list) {
            return list.map(function (h) {
              return h.platform === platform
                ? Object.assign({}, h, { subscribed: currentlySubscribed })
                : h;
            });
          });
          setErr(String(e.message || e));
        })
        .finally(function () {
          setHomeBusy(function (b) {
            const next = Object.assign({}, b);
            delete next[platform];
            return next;
          });
        });
    };

    return h("div", { className: "hermes-kanban-drawer-shade", onClick: props.onClose, role: "presentation" },
      h("div", {
        ref: drawerRef,
        className: "hermes-kanban-drawer",
        role: "dialog",
        "aria-modal": "true",
        "aria-labelledby": "pmo-task-drawer-title",
        tabIndex: -1,
        onClick: function (e) { e.stopPropagation(); },
      },
        h("div", { className: "hermes-kanban-drawer-head" },
          h("span", { id: "pmo-task-drawer-title", className: "text-xs text-muted-foreground" }, props.taskId),
          h("button", {
            type: "button",
            onClick: props.onClose,
            className: "hermes-kanban-drawer-close",
            title: tx(t, "close", "Close (Esc)"),
          }, "×"),
        ),
        loading ? h("div", { className: "p-4 text-sm text-muted-foreground" },
          tx(t, "loadingDetail", "Loading…")) :
        err ? h("div", { className: "p-4 text-sm text-destructive" }, err) :
        data ? h(TaskDetail, {
          data, editing, setEditing,
          renderMarkdown: props.renderMarkdown,
          allTasks: props.allTasks,
          assignees: props.assignees || [],
          boardSlug: boardSlug,
          onPatch: doPatch,
          onSpecify: doSpecify,
          onDecompose: doDecompose,
          onAddParent: addLink,
          onRemoveParent: removeLink,
          onAddChild: addChild,
          onRemoveChild: removeChild,
          homeChannels: homeChannels,
          homeBusy: homeBusy,
          onToggleHomeSub: toggleHomeSubscription,
          onRefresh: props.onRefresh,
          onUpload: handleUpload,
          onDeleteAttachment: handleDeleteAttachment,
          uploadBusy: uploadBusy,
          uploadErr: uploadErr,
          onOpenTask: function (taskId) {
            props.onClose();
            if (props.onOpenTask) props.onOpenTask(taskId);
          },
        }) : null,
        data ? h("div", { className: "hermes-kanban-drawer-comment-foot" },
          h("div", {
            className: "hermes-kanban-comment-hint text-xs text-muted-foreground",
            title: tx(t, "commentHintTitle",
              "Comments are the channel for talking to a task's worker. They land on the thread immediately — no need to block the task first. A running worker picks the thread up on its next kanban_show() or respawn; blocking is only for when you want the worker to STOP and wait for your input."),
          },
            "ⓘ ",
            tx(t, "commentHint",
              "Comments reach the worker on its next run or kanban_show() — no need to block the task first."),
          ),
          h("div", { className: "hermes-kanban-drawer-comment-row" },
            h(Input, {
              value: newComment,
              onChange: function (e) { setNewComment(e.target.value); },
              onKeyDown: function (e) {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault(); handleComment();
                }
              },
              placeholder: tx(t, "addComment", "Add a comment… (Enter to submit)"),
              className: "h-8 text-sm flex-1",
            }),
            h(Button, {
              onClick: handleComment,
              size: "sm",
            }, tx(t, "comment", "Comment")),
          ),
        ) : null,
      ),
    );
  }

  function _fmtBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  // Attachments section in the task drawer (#35338). Upload button +
  // list with download links and a delete (×) per row. The download
  // link hits GET /attachments/:id which streams the file; the worker
  // context surfaces the same files' absolute paths so a kanban worker
  // can read them with the file/terminal tools.
  function AttachmentsSection(props) {
    const i18n = props.i18n;
    const atts = props.attachments || [];
    const fileRef = useRef(null);
    const [dlErr, setDlErr] = useState(null);
    // Download via authenticated fetch → blob → synthetic anchor click.
    // A plain <a href> can't carry the auth the dashboard middleware requires,
    // so fetch authenticated and hand the browser a blob URL instead.
    function downloadAttachment(a) {
      // SDK.authedFetch handles auth in BOTH modes (loopback token header /
      // gated cookie) and applies the dashboard base-path prefix. The old
      // hand-rolled Authorization:Bearer + credentials:'same-origin' sent an
      // empty token and 401'd in gated mode.
      const url = withBoard(`${API}/attachments/${a.id}`, props.boardSlug);
      setDlErr(null);
      SDK.authedFetch(url)
        .then(function (resp) {
          if (!resp.ok) {
            return resp.text().then(function (txt) {
              throw new Error(parseApiErrorMessage(new Error(resp.status + ": " + txt)));
            });
          }
          return resp.blob();
        })
        .then(function (blob) {
          const objUrl = URL.createObjectURL(blob);
          const link = document.createElement("a");
          link.href = objUrl;
          link.download = a.filename || "attachment";
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
          setTimeout(function () { URL.revokeObjectURL(objUrl); }, 10000);
        })
        .catch(function (e) { setDlErr(String(e.message || e)); });
    }
    return h("div", { className: "hermes-kanban-section" },
      h("div", { className: "hermes-kanban-section-head" },
        `${tx(i18n, "attachments", "Attachments")} (${atts.length})`),
      h("input", {
        ref: fileRef,
        type: "file",
        multiple: true,
        style: { display: "none" },
        onChange: function (e) {
          if (props.onUpload) props.onUpload(e.target.files);
          // Reset so selecting the same file again re-triggers onChange.
          try { e.target.value = ""; } catch (_e) { /* ignore */ }
        },
      }),
      h("div", { className: "flex items-center gap-2 mb-2" },
        h(Button, {
          size: "sm",
          variant: "outline",
          disabled: !!props.uploadBusy,
          onClick: function () { if (fileRef.current) fileRef.current.click(); },
        }, props.uploadBusy
            ? tx(i18n, "uploading", "Uploading…")
            : tx(i18n, "uploadFile", "Upload file")),
      ),
      (props.uploadErr || dlErr)
        ? h("div", { className: "text-xs text-destructive mb-2" }, props.uploadErr || dlErr)
        : null,
      atts.length === 0
        ? h("div", { className: "text-xs text-muted-foreground" },
            tx(i18n, "noAttachments", "— no attachments —"))
        : atts.map(function (a) {
            return h("div", {
              key: a.id,
              className: "flex items-center justify-between gap-2 py-1 text-sm",
            },
              h("button", {
                type: "button",
                className: "hermes-kanban-attachment-link truncate",
                title: a.filename,
                onClick: function () { downloadAttachment(a); },
              }, a.filename),
              h("span", { className: "text-xs text-muted-foreground whitespace-nowrap" },
                _fmtBytes(a.size)),
              h("button", {
                type: "button",
                className: "hermes-kanban-drawer-close",
                title: tx(i18n, "removeAttachment", "Remove attachment"),
                onClick: function () {
                  if (window.confirm(tx(i18n, "confirmRemoveAttachment",
                      "Remove this attachment?"))) {
                    if (props.onDelete) props.onDelete(a.id);
                  }
                },
              }, "×"),
            );
          }),
    );
  }

  function TaskDetail(props) {
    const { t: i18n } = useI18n();
    const t = props.data.task;
    // Routing, delivery, and question lifecycle markers remain durable native
    // comments for restart safety, but they are transport metadata rather than
    // part of the human/agent ticket conversation.
    const comments = (props.data.comments || []).filter(function (comment) {
      return String(comment && comment.author || "") !== "pmo-system";
    });
    const events = props.data.events || [];
    const attachments = props.data.attachments || [];
    const links = props.data.links || { parents: [], children: [] };
    const childResults = props.data.child_results || [];

    return h("div", { className: "hermes-kanban-drawer-body" },
      h("div", { className: "hermes-kanban-drawer-title" },
        h("span", { className: cn("hermes-kanban-dot", COLUMN_DOT[t.status]) }),
        props.editing
          ? h(TitleEditor, {
              initial: t.title || "",
              onSave: function (newTitle) {
                return props.onPatch({ title: newTitle }).then(function () { props.setEditing(false); });
              },
              onCancel: function () { props.setEditing(false); },
            })
          : h("span", {
              className: "hermes-kanban-drawer-title-text",
              title: tx(i18n, "clickToEdit", "Click to edit"),
              onClick: function () { props.setEditing(true); },
            }, t.title || tx(i18n, "untitled", "(untitled)")),
      ),
      h("div", { className: "hermes-kanban-drawer-meta" },
        h(MetaRow, { label: tx(i18n, "status", "Status"), value: t.status }),
        h(AssigneeEditor, { task: t, onPatch: props.onPatch }),
        h(PriorityEditor, { task: t, onPatch: props.onPatch }),
        h(ModelEditor, { task: t, onPatch: props.onPatch }),
        t.tenant ? h(MetaRow, { label: tx(i18n, "tenant", "Tenant"), value: t.tenant }) : null,
        h(MetaRow, {
          label: tx(i18n, "workspace", "Workspace"),
          value: `${t.workspace_kind}${t.workspace_path ? ": " + t.workspace_path : ""}`,
        }),
        (t.skills && t.skills.length > 0) ? h(MetaRow, {
          label: tx(i18n, "skills", "Skills"),
          value: t.skills.join(", "),
        }) : null,
        t.goal_mode ? h(MetaRow, {
          label: tx(i18n, "goalMode", "Goal mode"),
          value: t.goal_max_turns
            ? `on (max ${t.goal_max_turns} turns)`
            : "on",
        }) : null,
        t.created_by ? h(MetaRow, { label: tx(i18n, "createdBy", "Created by"), value: t.created_by }) : null,
      ),
      h(StatusActions, {
        task: t,
        onPatch: props.onPatch,
        onSpecify: props.onSpecify,
        onDecompose: props.onDecompose,
      }),
      h(DiagnosticsSection, {
        task: t,
        boardSlug: props.boardSlug,
        assignees: props.assignees,
        diagnostics: t.diagnostics || [],
        onRefresh: props.onRefresh,
      }),
      h(HomeSubsSection, {
        homeChannels: props.homeChannels || [],
        homeBusy: props.homeBusy || {},
        onToggle: props.onToggleHomeSub,
      }),
      h(BodyEditor, {
        task: t,
        renderMarkdown: props.renderMarkdown,
        onPatch: props.onPatch,
      }),
      h(DependencyEditor, {
        task: t,
        links, allTasks: props.allTasks,
        onAddParent: props.onAddParent,
        onRemoveParent: props.onRemoveParent,
        onAddChild: props.onAddChild,
        onRemoveChild: props.onRemoveChild,
      }),
      (function () {
        var finalResult = t.result || t.latest_summary || null;
        var isDone = t.status === "done";
        var isParent = links.children.length > 0;
        if (finalResult) {
          var label = t.result
            ? tx(i18n, "result", "Result")
            : tx(i18n, "finalResult", "Final Result (run summary)");
          return h("div", { className: "hermes-kanban-section" },
            h("div", { className: "hermes-kanban-section-head" }, label),
            h(MarkdownBlock, { source: finalResult, enabled: props.renderMarkdown }),
          );
        }
        if (isDone && isParent) {
          return h("div", { className: "hermes-kanban-section" },
            h("div", { className: "hermes-kanban-section-head" }, tx(i18n, "result", "Result")),
            h("div", { className: "hermes-kanban-done-no-result hermes-kanban-done-parent-note" },
              tx(i18n, "doneParentNote",
                "This card is an orchestrator / parent task. Review the child results section for the substantive work."),
            ),
          );
        }
        if (isDone) {
          return h("div", { className: "hermes-kanban-section" },
            h("div", { className: "hermes-kanban-section-head" }, tx(i18n, "result", "Result")),
            h("div", { className: "hermes-kanban-done-no-result" },
              tx(i18n, "doneNoResult",
                "No final result was recorded. Check Run History, Logs, or Child Tasks for the worker output."),
            ),
          );
        }
        return null;
      })(),
      childResults.length > 0 ? h("div", { className: "hermes-kanban-section" },
        h("div", { className: "hermes-kanban-section-head" },
          `${tx(i18n, "childResults", "Child Results")} (${childResults.length})`),
        childResults.map(function (child) {
          var childResult = child.result || child.latest_summary || null;
          return h("div", { key: child.id, className: "hermes-kanban-comment" },
            h("div", { className: "hermes-kanban-comment-head" },
              h("span", { className: "hermes-kanban-comment-author" },
                `${child.id} · ${child.title || tx(i18n, "untitled", "(untitled)")}`),
              h(Badge, { variant: "outline" }, child.status),
              h("button", {
                type: "button",
                className: "hermes-kanban-diag-action-btn",
                onClick: function () { if (props.onOpenTask) props.onOpenTask(child.id); },
              }, tx(i18n, "open", "Open")),
            ),
            childResult
              ? h(MarkdownBlock, { source: childResult, enabled: props.renderMarkdown })
              : h("div", { className: "text-xs text-muted-foreground" },
                  tx(i18n, "noChildResult", "No result recorded yet.")),
          );
        }),
      ) : null,
      h(AttachmentsSection, {
        attachments: attachments,
        boardSlug: props.boardSlug,
        onUpload: props.onUpload,
        onDelete: props.onDeleteAttachment,
        uploadBusy: props.uploadBusy,
        uploadErr: props.uploadErr,
        i18n: i18n,
      }),
      h("div", { className: "hermes-kanban-section" },
        h("div", { className: "hermes-kanban-section-head" },
          `${tx(i18n, "comments", "Comments")} (${comments.length})`),
        comments.length === 0
          ? h("div", { className: "text-xs text-muted-foreground" },
              tx(i18n, "noComments", "— no comments —"))
          : comments.map(function (c) {
              return h("div", { key: c.id, className: "hermes-kanban-comment" },
                h("div", { className: "hermes-kanban-comment-head" },
                  h("span", { className: "hermes-kanban-comment-author" }, c.author || "anon"),
                  h("span", { className: "hermes-kanban-comment-ago" },
                    timeAgo ? timeAgo(c.created_at) : ""),
                ),
                h(MarkdownBlock, { source: pmWorkflowVisibleBody(c.body), enabled: props.renderMarkdown }),
              );
            }),
      ),
      h("div", { className: "hermes-kanban-section" },
        h("div", { className: "hermes-kanban-section-head" },
          `${tx(i18n, "events", "Events")} (${events.length})`),
        events.slice().reverse().slice(0, 20).map(function (e) {
          const isDiag = isDiagnosticEvent(e.kind);
          const phantoms = isDiag ? phantomIdsFromEvent(e) : [];
          return h("div", {
            key: e.id,
            className: cn(
              "hermes-kanban-event",
              isDiag ? "hermes-kanban-event--hallucination" : "",
            ),
          },
            isDiag
              ? h("div", { className: "hermes-kanban-event-header" },
                  h("span", { className: "hermes-kanban-event-warning-icon" }, "⚠"),
                  h("span", { className: "hermes-kanban-event-warning-label" },
                    getDiagnosticEventLabel(i18n, e.kind) || e.kind),
                  h("span", { className: "hermes-kanban-event-ago" },
                    timeAgo ? timeAgo(e.created_at) : ""),
                )
              : h("div", { className: "hermes-kanban-event-header-plain" },
                  h("span", { className: "hermes-kanban-event-kind" }, e.kind),
                  h("span", { className: "hermes-kanban-event-ago" },
                    timeAgo ? timeAgo(e.created_at) : ""),
                ),
            isDiag && phantoms.length > 0
              ? h("div", { className: "hermes-kanban-event-phantom-row" },
                  h("span", { className: "hermes-kanban-event-phantom-label" },
                    tx(i18n, "phantomIds", "Phantom ids:")),
                  phantoms.map(function (pid) {
                    return h("code", {
                      key: pid,
                      className: "hermes-kanban-event-phantom-chip",
                    }, pid);
                  }),
                )
              : null,
            e.payload && !isDiag
              ? h("code", { className: "hermes-kanban-event-payload" },
                  JSON.stringify(e.payload))
              : null,
          );
        }),
      ),
      h(WorkerLogSection, { taskId: t.id, boardSlug: props.boardSlug }),
      h(RunHistorySection, { runs: props.data.runs || [] }),
    );
  }

  // Per-attempt history. Closed runs first (most recent last), then the
  // active run if any. Each row shows profile / outcome / elapsed /
  // summary. Collapsed by default when there are more than three runs.
  function RunHistorySection(props) {
    const { t } = useI18n();
    const runs = props.runs || [];
    const [expanded, setExpanded] = useState(false);
    if (runs.length === 0) return null;
    const showAll = expanded || runs.length <= 3;
    const visible = showAll ? runs : runs.slice(-3);

    const fmtElapsed = function (run) {
      if (!run || !run.started_at) return "";
      const end = run.ended_at || Math.floor(Date.now() / 1000);
      const secs = Math.max(0, end - run.started_at);
      if (secs < 60) return `${secs}s`;
      if (secs < 3600) return `${Math.round(secs / 60)}m`;
      return `${(secs / 3600).toFixed(1)}h`;
    };

    return h("div", { className: "hermes-kanban-section" },
      h("div", { className: "hermes-kanban-section-head-row" },
        h("span", { className: "hermes-kanban-section-head" },
          `${tx(t, "runHistory", "Run history")} (${runs.length})`),
        !showAll
          ? h("button", {
              type: "button",
              onClick: function () { setExpanded(true); },
              className: "hermes-kanban-edit-link",
              title: tx(t, "showAllAttempts", "Show all attempts"),
            }, `+${runs.length - 3} earlier`)
          : null,
      ),
      visible.map(function (r) {
        const outcomeClass = r.ended_at
          ? `hermes-kanban-run--${r.outcome || r.status || "ended"}`
          : "hermes-kanban-run--active";
        return h("div", { key: r.id, className: cn("hermes-kanban-run", outcomeClass) },
          h("div", { className: "hermes-kanban-run-head" },
            h("span", { className: "hermes-kanban-run-outcome" },
              r.ended_at ? (r.outcome || r.status || tx(t, "ended", "ended")) : tx(t, "active", "active")),
            h("span", { className: "hermes-kanban-run-profile" },
              r.profile ? `@${r.profile}` : tx(t, "noProfile", "(no profile)")),
            h("span", { className: "hermes-kanban-run-elapsed" }, fmtElapsed(r)),
            h("span", { className: "hermes-kanban-run-ago" },
              timeAgo ? timeAgo(r.started_at) : ""),
          ),
          r.summary
            ? h("div", { className: "hermes-kanban-run-summary" }, r.summary)
            : null,
          r.error
            ? h("div", { className: "hermes-kanban-run-error" }, r.error)
            : null,
          (r.metadata && Object.keys(r.metadata).length > 0)
            ? (function () {
                var json = JSON.stringify(r.metadata, null, 2);
                var collapsed = json.length > 300;
                return h("details", {
                    className: "hermes-kanban-run-meta-block",
                    open: !collapsed,
                  },
                  h("summary", { className: "hermes-kanban-run-meta-label" }, "Metadata"),
                  h("code", { className: "hermes-kanban-run-meta" }, json),
                );
              })()
            : null,
        );
      }),
    );
  }

  // Worker log: loads lazily (one GET on mount), refresh button, tail cap.
  function WorkerLogSection(props) {
    const { t } = useI18n();
    const [state, setState] = useState({ loading: false, data: null, err: null });
    const load = useCallback(function () {
      setState({ loading: true, data: null, err: null });
      SDK.fetchJSON(withBoard(`${API}/tasks/${encodeURIComponent(props.taskId)}/log?tail=100000`, props.boardSlug))
        .then(function (d) { setState({ loading: false, data: d, err: null }); })
        .catch(function (e) { setState({ loading: false, data: null, err: String(e.message || e) }); });
    }, [props.taskId, props.boardSlug]);

    // Auto-load when the section mounts; the user opened the drawer so the
    // cost is one small HTTP round-trip.
    useEffect(function () { load(); }, [load]);

    const data = state.data;
    let body;
    if (state.loading) {
      body = h("div", { className: "text-xs text-muted-foreground" },
        tx(t, "loadingLog", "Loading log…"));
    } else if (state.err) {
      body = h("div", { className: "text-xs text-destructive" }, state.err);
    } else if (!data || !data.exists) {
      body = h("div", { className: "text-xs text-muted-foreground italic" },
        tx(t, "noWorkerLog",
          "— no worker log yet (task hasn't spawned or log was rotated away) —"));
    } else {
      body = h("pre", { className: "hermes-kanban-pre hermes-kanban-log" },
        data.content || "(empty)");
    }

    return h("div", { className: "hermes-kanban-section" },
      h("div", { className: "hermes-kanban-section-head-row" },
        h("span", { className: "hermes-kanban-section-head" },
          tx(t, "workerLog", "Worker log") + (data && data.size_bytes ? ` (${data.size_bytes} B)` : "")),
        h("button", {
          type: "button",
          onClick: load,
          className: "hermes-kanban-edit-link",
          title: "Refresh log",
        }, "refresh"),
      ),
      body,
      data && data.truncated
        ? h("div", { className: "text-xs text-muted-foreground" },
            tx(t, "logTruncated", "(showing last 100 KB — full log at "),
            data.path,
            tx(t, "logAt", ")"))
        : null,
    );
  }

  function MetaRow(props) {
    return h("div", { className: "hermes-kanban-meta-row" },
      h("span", { className: "hermes-kanban-meta-label" }, props.label),
      h("span", { className: "hermes-kanban-meta-value" }, props.value),
    );
  }

  function TitleEditor(props) {
    const { t } = useI18n();
    const [v, setV] = useState(props.initial);
    const save = function () {
      const trimmed = v.trim();
      if (!trimmed) return;
      props.onSave(trimmed);
    };
    return h("div", { className: "hermes-kanban-edit-row" },
      h(Input, {
        value: v, autoFocus: true,
        onChange: function (e) { setV(e.target.value); },
        onKeyDown: function (e) {
          if (e.key === "Enter") { e.preventDefault(); save(); }
          if (e.key === "Escape") props.onCancel();
        },
        className: "h-8 text-sm flex-1",
      }),
      h(Button, { onClick: save,
        size: "sm",
      }, tx(t, "save", "Save")),
      h(Button, { onClick: props.onCancel,
        size: "sm",
      }, tx(t, "cancel", "Cancel")),
    );
  }

  function AssigneeEditor(props) {
    const { t } = useI18n();
    const [editing, setEditing] = useState(false);
    const [v, setV] = useState(props.task.assignee || "");
    useEffect(function () { setV(props.task.assignee || ""); }, [props.task.assignee]);
    if (!editing) {
      return h("div", { className: "hermes-kanban-meta-row" },
        h("span", { className: "hermes-kanban-meta-label" }, tx(t, "assignee", "Assignee")),
        h("span", {
          className: "hermes-kanban-meta-value hermes-kanban-editable",
          onClick: function () { setEditing(true); },
          title: tx(t, "clickToEditAssignee", "Click to edit assignee"),
        }, props.task.assignee || tx(t, "unassigned", "unassigned")),
      );
    }
    const save = function () {
      props.onPatch({ assignee: v.trim() || "" }).then(function () { setEditing(false); });
    };
    return h("div", { className: "hermes-kanban-meta-row" },
      h("span", { className: "hermes-kanban-meta-label" }, tx(t, "assignee", "Assignee")),
      h(Input, {
        value: v, autoFocus: true,
        onChange: function (e) { setV(e.target.value); },
        onKeyDown: function (e) {
          if (e.key === "Enter") { e.preventDefault(); save(); }
          if (e.key === "Escape") setEditing(false);
        },
        placeholder: tx(t, "emptyAssignee", "(empty = unassign)"),
        className: "h-7 text-xs flex-1",
        style: { textTransform: "none" },
        autoCapitalize: "none",
        autoCorrect: "off",
        spellCheck: false,
      }),
    );
  }

  function PriorityEditor(props) {
    const { t } = useI18n();
    const [editing, setEditing] = useState(false);
    const [v, setV] = useState(String(props.task.priority || 0));
    useEffect(function () { setV(String(props.task.priority || 0)); }, [props.task.priority]);
    if (!editing) {
      return h("div", { className: "hermes-kanban-meta-row" },
        h("span", { className: "hermes-kanban-meta-label" }, tx(t, "priority", "Priority")),
        h("span", {
          className: "hermes-kanban-meta-value hermes-kanban-editable",
          onClick: function () { setEditing(true); },
          title: tx(t, "clickToEdit", "Click to edit"),
        }, String(props.task.priority)),
      );
    }
    const save = function () {
      props.onPatch({ priority: Number(v) || 0 }).then(function () { setEditing(false); });
    };
    return h("div", { className: "hermes-kanban-meta-row" },
      h("span", { className: "hermes-kanban-meta-label" }, tx(t, "priority", "Priority")),
      h(Input, {
        type: "number", value: v, autoFocus: true,
        onChange: function (e) { setV(e.target.value); },
        onKeyDown: function (e) {
          if (e.key === "Enter") { e.preventDefault(); save(); }
          if (e.key === "Escape") setEditing(false);
        },
        className: "h-7 text-xs w-20",
      }),
    );
  }

  // Module-level cache for the model-options catalog so opening several
  // task drawers doesn't refetch. { providers: [{slug,label,models}] }
  let _modelCatalogCache = null;
  let _modelCatalogPromise = null;
  function fetchModelCatalog() {
    if (_modelCatalogCache) return Promise.resolve(_modelCatalogCache);
    if (_modelCatalogPromise) return _modelCatalogPromise;
    _modelCatalogPromise = SDK.fetchJSON(`${API}/model-options`)
      .then(function (data) {
        _modelCatalogCache = data && Array.isArray(data.providers) ? data : { providers: [] };
        return _modelCatalogCache;
      })
      .catch(function () {
        _modelCatalogPromise = null; // allow retry on next open
        return { providers: [] };
      });
    return _modelCatalogPromise;
  }

  // Per-task model override dropdown. Value encoding: "" = profile
  // default; "<slug>\u0000<model>" = provider+model pair (the separator
  // can't appear in either half). A catalog fetch failure degrades to a
  // free-text input so the override is still settable.
  function ModelEditor(props) {
    const { t } = useI18n();
    const task = props.task;
    const [editing, setEditing] = useState(false);
    const [catalog, setCatalog] = useState(_modelCatalogCache);
    const [busy, setBusy] = useState(false);
    const [freeText, setFreeText] = useState("");

    useEffect(function () {
      if (!editing || catalog) return;
      let alive = true;
      fetchModelCatalog().then(function (data) {
        if (alive) setCatalog(data);
      });
      return function () { alive = false; };
    }, [editing, catalog]);

    const current = task.model_override
      ? (task.provider_override
          ? `${task.provider_override}: ${task.model_override}`
          : task.model_override)
      : tx(t, "modelProfileDefault", "profile default");

    if (!editing) {
      return h("div", { className: "hermes-kanban-meta-row" },
        h("span", { className: "hermes-kanban-meta-label" }, tx(t, "model", "Model")),
        h("span", {
          className: cn(
            "hermes-kanban-meta-value hermes-kanban-editable",
            !task.model_override ? "text-muted-foreground" : "",
          ),
          onClick: function () { setEditing(true); },
          title: tx(t, "clickToEditModel",
            "Click to override the model for this task's next run"),
        }, current),
      );
    }

    const apply = function (patch) {
      setBusy(true);
      props.onPatch(patch).then(function () {
        setEditing(false);
      }).catch(function () {
        // onPatch surfaces its own toast; just re-enable the control.
      }).then(function () { setBusy(false); });
    };

    const onPick = function (value) {
      if (value === "") {
        apply({ clear_model_override: true });
        return;
      }
      const sep = value.indexOf("\u0000");
      if (sep === -1) {
        apply({ model_override: value });
        return;
      }
      apply({
        provider_override: value.slice(0, sep),
        model_override: value.slice(sep + 1),
      });
    };

    const providers = (catalog && catalog.providers) || [];
    const loading = editing && !catalog;
    const currentValue = task.model_override
      ? (task.provider_override
          ? `${task.provider_override}\u0000${task.model_override}`
          : task.model_override)
      : "";

    // Free-text fallback when the catalog is empty (inventory unavailable
    // or zero authenticated providers).
    if (!loading && providers.length === 0) {
      const saveFree = function () {
        const v = freeText.trim();
        if (!v) { apply({ clear_model_override: true }); return; }
        apply({ model_override: v });
      };
      return h("div", { className: "hermes-kanban-meta-row" },
        h("span", { className: "hermes-kanban-meta-label" }, tx(t, "model", "Model")),
        h(Input, {
          value: freeText, autoFocus: true, disabled: busy,
          placeholder: tx(t, "modelFreeTextPlaceholder", "model name (empty = profile default)"),
          onChange: function (e) { setFreeText(e.target.value); },
          onKeyDown: function (e) {
            if (e.key === "Enter") { e.preventDefault(); saveFree(); }
            if (e.key === "Escape") setEditing(false);
          },
          className: "h-7 text-xs flex-1",
          style: { textTransform: "none" },
          autoCapitalize: "none", autoCorrect: "off", spellCheck: false,
        }),
      );
    }

    // Ensure the current override is selectable even when it's not in the
    // catalog (e.g. set from the CLI with a model the catalog doesn't list).
    let currentInCatalog = currentValue === "";
    for (let i = 0; i < providers.length && !currentInCatalog; i++) {
      const p = providers[i];
      for (let j = 0; j < p.models.length; j++) {
        const enc = `${p.slug}\u0000${p.models[j]}`;
        if (enc === currentValue || p.models[j] === currentValue) {
          currentInCatalog = true;
          break;
        }
      }
    }

    return h("div", { className: "hermes-kanban-meta-row" },
      h("span", { className: "hermes-kanban-meta-label" }, tx(t, "model", "Model")),
      loading
        ? h("span", { className: "hermes-kanban-meta-value text-muted-foreground" },
            tx(t, "modelLoading", "loading models…"))
        : h("select", {
            className: "hermes-kanban-recovery-select",
            value: currentValue,
            disabled: busy,
            autoFocus: true,
            onChange: function (e) { onPick(e.target.value); },
            onKeyDown: function (e) {
              if (e.key === "Escape") setEditing(false);
            },
          },
            h("option", { value: "" },
              tx(t, "modelProfileDefaultOption", "(profile default)")),
            !currentInCatalog
              ? h("option", { value: currentValue }, current)
              : null,
            providers.map(function (p) {
              return h("optgroup", { key: p.slug, label: p.label || p.slug },
                p.models.map(function (m) {
                  return h("option", {
                    key: `${p.slug}\u0000${m}`,
                    value: `${p.slug}\u0000${m}`,
                  }, m);
                }),
              );
            }),
          ),
    );
  }

  function BodyEditor(props) {
    const { t } = useI18n();
    const [editing, setEditing] = useState(false);
    const [v, setV] = useState(props.task.body || "");
    useEffect(function () { setV(props.task.body || ""); }, [props.task.body]);
    const save = function () {
      props.onPatch({ body: v }).then(function () { setEditing(false); });
    };
    return h("div", { className: "hermes-kanban-section" },
      h("div", { className: "hermes-kanban-section-head-row" },
        h("span", { className: "hermes-kanban-section-head" }, tx(t, "description", "Description")),
        editing
          ? h("div", { className: "flex gap-1" },
              h(Button, { onClick: save,
                size: "sm",
              }, tx(t, "save", "Save")),
              h(Button, { onClick: function () { setEditing(false); setV(props.task.body || ""); },
                size: "sm",
              }, tx(t, "cancel", "Cancel")),
            )
          : h("button", {
              type: "button",
              onClick: function () { setEditing(true); },
              className: "hermes-kanban-edit-link",
              title: "Edit description",
            }, tx(t, "edit", "edit")),
      ),
      editing
        ? h("textarea", {
            className: "hermes-kanban-textarea",
            value: v,
            rows: 8,
            onChange: function (e) { setV(e.target.value); },
          })
        : props.task.body
          ? h(MarkdownBlock, { source: props.task.body, enabled: props.renderMarkdown })
          : h("div", { className: "text-xs text-muted-foreground italic" },
              tx(t, "noDescription", "— no description —")),
    );
  }

  function DependencyEditor(props) {
    const { t } = useI18n();
    const { task, links, allTasks } = props;
    const [newParent, setNewParent] = useState("");
    const [newChild, setNewChild] = useState("");
    // Filter out self + existing links when offering the "add" dropdown.
    const candidatesFor = function (excludeSet) {
      return (allTasks || []).filter(function (tk) {
        return tk.id !== task.id && !excludeSet.has(tk.id);
      });
    };
    const parentExclude = new Set([task.id, ...(links.parents || [])]);
    const childExclude  = new Set([task.id, ...(links.children || [])]);

    return h("div", { className: "hermes-kanban-section" },
      h("div", { className: "hermes-kanban-section-head" }, tx(t, "dependencies", "Dependencies")),
      h("div", { className: "hermes-kanban-deps-row" },
        h("span", { className: "hermes-kanban-deps-label" }, tx(t, "parents", "Parents:")),
        h("div", { className: "hermes-kanban-deps-chips" },
          (links.parents || []).length === 0
            ? h("span", { className: "hermes-kanban-deps-empty" }, tx(t, "none", "none"))
            : (links.parents || []).map(function (id) {
                return h("span", { key: id, className: "hermes-kanban-dep-chip" },
                  id,
                  h("button", {
                    type: "button",
                    className: "hermes-kanban-dep-chip-x",
                    onClick: function () { props.onRemoveParent(id); },
                    title: tx(t, "removeDependency", "Remove dependency"),
                  }, "×"),
                );
              }),
        ),
      ),
      h("div", { className: "hermes-kanban-deps-row" },
        h(Select, Object.assign({
          value: newParent,
          className: "h-7 text-xs flex-1",
        }, selectChangeHandler(setNewParent)),
          h(SelectOption, { value: "" }, tx(t, "addParent", "— add parent —")),
          candidatesFor(parentExclude).map(function (tk) {
            return h(SelectOption, { key: tk.id, value: tk.id },
              `${tk.id} — ${(tk.title || "").slice(0, 50)}`);
          }),
        ),
        h(Button, {
          onClick: function () {
            if (!newParent) return;
            props.onAddParent(newParent).then(function () { setNewParent(""); });
          },
          disabled: !newParent,
          size: "sm",
        }, "+ parent"),
      ),
      h("div", { className: "hermes-kanban-deps-row" },
        h("span", { className: "hermes-kanban-deps-label" }, tx(t, "children", "Children:")),
        h("div", { className: "hermes-kanban-deps-chips" },
          (links.children || []).length === 0
            ? h("span", { className: "hermes-kanban-deps-empty" }, tx(t, "none", "none"))
            : (links.children || []).map(function (id) {
                return h("span", { key: id, className: "hermes-kanban-dep-chip" },
                  id,
                  h("button", {
                    type: "button",
                    className: "hermes-kanban-dep-chip-x",
                    onClick: function () { props.onRemoveChild(id); },
                    title: tx(t, "removeDependency", "Remove dependency"),
                  }, "×"),
                );
              }),
        ),
      ),
      h("div", { className: "hermes-kanban-deps-row" },
        h(Select, Object.assign({
          value: newChild,
          className: "h-7 text-xs flex-1",
        }, selectChangeHandler(setNewChild)),
          h(SelectOption, { value: "" }, tx(t, "addChild", "— add child —")),
          candidatesFor(childExclude).map(function (tk) {
            return h(SelectOption, { key: tk.id, value: tk.id },
              `${tk.id} — ${(tk.title || "").slice(0, 50)}`);
          }),
        ),
        h(Button, {
          onClick: function () {
            if (!newChild) return;
            props.onAddChild(newChild).then(function () { setNewChild(""); });
          },
          disabled: !newChild,
          size: "sm",
        }, "+ child"),
      ),
    );
  }

  function StatusActions(props) {
    const { t } = useI18n();
    const task = props.task;
    const [specifyBusy, setSpecifyBusy] = useState(false);
    const [specifyMsg, setSpecifyMsg] = useState(null);
    const [decomposeBusy, setDecomposeBusy] = useState(false);
    const [decomposeMsg, setDecomposeMsg] = useState(null);
    const b = function (label, patch, enabled, confirmMsg) {
      return h(Button, {
        onClick: function () { if (enabled !== false) props.onPatch(patch, { confirm: confirmMsg }); },
        disabled: enabled === false,
        size: "sm",
      }, label);
    };

    // "Specify" appears only when the task is in the Triage column — the
    // one column where an auxiliary LLM pass is meaningful. Elsewhere
    // the backend would return ok:false with "not in triage" anyway,
    // so hiding the button keeps the action row uncluttered.
    const specifyButton = (task.status === "triage" && props.onSpecify)
      ? h(Button, {
          onClick: function () {
            if (specifyBusy) return;
            setSpecifyBusy(true);
            setSpecifyMsg(null);
            props.onSpecify().then(function (res) {
              if (res && res.ok) {
                const suffix = res.new_title
                  ? ` — retitled: ${res.new_title}`
                  : "";
                setSpecifyMsg({ ok: true, text: `Specified${suffix}` });
              } else {
                setSpecifyMsg({
                  ok: false,
                  text: "Specify failed: " + ((res && res.reason) || "unknown error"),
                });
              }
            }).catch(function (err) {
              setSpecifyMsg({
                ok: false,
                text: "Specify failed: " + (err.message || String(err)),
              });
            }).then(function () {
              setSpecifyBusy(false);
            });
          },
          disabled: specifyBusy,
          size: "sm",
        }, specifyBusy ? "Specifying…" : "✨ Specify")
      : null;

    // "Decompose" is the built-in decomposer fan-out. Like Specify, only
    // makes sense on triage-column tasks — elsewhere the backend short-
    // circuits with ok:false. When the decomposer returns fanout:false
    // we render the same single-task message as Specify; when it fans
    // out we report the child count for quick at-a-glance verification.
    const decomposeButton = (task.status === "triage" && props.onDecompose)
      ? h(Button, {
          onClick: function () {
            if (decomposeBusy) return;
            setDecomposeBusy(true);
            setDecomposeMsg(null);
            props.onDecompose().then(function (res) {
              if (res && res.ok) {
                if (res.fanout && res.child_ids && res.child_ids.length) {
                  setDecomposeMsg({
                    ok: true,
                    text: `Decomposed into ${res.child_ids.length} children: ${res.child_ids.join(", ")}`,
                  });
                } else {
                  const suffix = res.new_title
                    ? ` — retitled: ${res.new_title}`
                    : "";
                  setDecomposeMsg({
                    ok: true,
                    text: `Single task (no fanout)${suffix}`,
                  });
                }
              } else {
                setDecomposeMsg({
                  ok: false,
                  text: "Decompose failed: " + ((res && res.reason) || "unknown error"),
                });
              }
            }).catch(function (err) {
              setDecomposeMsg({
                ok: false,
                text: "Decompose failed: " + (err.message || String(err)),
              });
            }).then(function () {
              setDecomposeBusy(false);
            });
          },
          disabled: decomposeBusy,
          size: "sm",
        }, decomposeBusy ? "Decomposing…" : "⚗ Decompose")
      : null;

    return h("div", null,
      h("div", { className: "hermes-kanban-actions" },
        specifyButton,
        decomposeButton,
        b("→ triage",  { status: "triage" },   task.status !== "triage"),
        b("→ ready",   { status: "ready" },    task.status !== "ready"),
        // No direct → running button: /tasks/:id PATCH rejects status=running
        // with 400 (issue #19535). Tasks enter running only through the
        // dispatcher's claim_task path, which atomically creates the run row,
        // claim lock, and worker process metadata.
        b(tx(t, "block", "Block"),     { status: "blocked" },
          task.status === "running" || task.status === "ready",
          getDestructiveConfirm(t, "blocked")),
        b(tx(t, "unblock", "Unblock"),   { status: "ready" },    task.status === "blocked"),
        b(tx(t, "complete", "Complete"),  { status: "done" },
          task.status === "running" || task.status === "ready" || task.status === "blocked",
          getDestructiveConfirm(t, "done")),
        b(tx(t, "archive", "Archive"),   { status: "archived" }, task.status !== "archived",
          getDestructiveConfirm(t, "archived")),
      ),
      specifyMsg ? h("div", {
        className: specifyMsg.ok
          ? "hermes-kanban-msg-ok"
          : "hermes-kanban-msg-err",
      }, specifyMsg.text) : null,
      decomposeMsg ? h("div", {
        className: decomposeMsg.ok
          ? "hermes-kanban-msg-ok"
          : "hermes-kanban-msg-err",
      }, decomposeMsg.text) : null,
    );
  }


  // One toggle per gateway platform the user has a home channel set on
  // (telegram, discord, slack, etc.). Toggling on creates a kanban_notify_subs
  // row routed to that platform's home; toggling off removes it. Nothing
  // renders when no platforms have a home configured — this section stays
  // invisible for users who haven't set one up.
  function HomeSubsSection(props) {
    const { t } = useI18n();
    const channels = props.homeChannels || [];
    if (channels.length === 0) return null;
    const busy = props.homeBusy || {};
    return h("div", { className: "hermes-kanban-section" },
      h("div", { className: "hermes-kanban-section-head" },
        tx(t, "notifyHomeChannels", "Notify home channels")),
      h("div", { className: "hermes-kanban-home-subs" },
        channels.map(function (hc) {
          const isBusy = !!busy[hc.platform];
          const label = hc.subscribed ? "✓ " + hc.platform : hc.platform;
          const target = `${hc.name} (${hc.chat_id}${hc.thread_id ? " / " + hc.thread_id : ""})`;
          const title = hc.subscribed
            ? `${tx(t, "sendingUpdates", "Sending updates to")} ${target}. Click to stop.`
            : `${tx(t, "sendNotifications", "Send completed / blocked / gave_up notifications to")} ${target}.`;
          return h(Button, {
            key: hc.platform,
            size: "sm",
            title: title,
            disabled: isBusy || !props.onToggle,
            onClick: function () {
              if (props.onToggle) props.onToggle(hc.platform, hc.subscribed);
            },
            className: hc.subscribed
              ? "hermes-kanban-home-sub hermes-kanban-home-sub--on"
              : "hermes-kanban-home-sub",
          }, label);
        })
      )
    );
  }

  // PMO-DASHBOARD-EXTENSION:START
  // -------------------------------------------------------------------------
  // Datansh PM-OS shell. The copied KanbanPage above is mounted unchanged as
  // the Board section; these components only add project-management views.
  // React renders every API value as text. PM views never use raw HTML.
  // -------------------------------------------------------------------------

  const PM_STRINGS = {
    en: {
      appName: "Datansh PM-OS",
      subtitle: "Project delivery, conversations, approvals, and the complete Hermes Kanban board.",
      sections: {
        portfolio: "Portfolio", onboarding: "Project onboarding", overview: "Project overview", board: "Board",
        founders: "Founder's Office", approvals: "Approvals", timeline: "Timeline",
        notifications: "Notifications", access: "Project access", decisions: "Decisions & context",
        humanTasks: "Human tasks", spend: "Spend", administration: "Administration",
      },
      board: { region: "Kanban delivery board", moveTo: function (title) { return "Move " + title + " to another column"; }, loadMore: function (count) { return "Load " + count + " more tickets"; } },
      states: { loading: "Loading…", empty: "Nothing to show yet.", retry: "Retry", error: "Could not load this view: " },
      metrics: { active: "Active projects", flight: "Tickets in flight", blocked: "Blocked", needs: "Needs you", spend: "Spend MTD" },
      portfolio: { project: "Project", status: "Status", progress: "Progress", blocked: "Blocked", needs: "Needs you", spend: "Spend MTD", activity: "Last activity", progressHelp: "Done divided by all non-cancelled delivery tickets.", open: "Open project" },
      overview: { attention: "Needs attention", decisions: "Recent decisions", team: "Team", activity: "Activity", openBoard: "Open board", founders: "Open Founder's Office", access: "Manage project access", noAttention: "No project exceptions need attention.", searchActivity: "Search activity", allActivity: "All activity", noActivity: "No matching activity yet.", previous: "Previous", next: "Next", activityPage: function (from, to, total) { return "Showing " + from + "–" + to + " of " + total; } },
      repositories: {
        title: "Repository connections", subtitle: "Codebases available to this project and its agents.",
        connect: "Connect checkout", clone: "Clone repository", name: "Repository name",
        path: "Local path", remote: "Remote", branch: "Default branch", access: "Access",
        primary: "Primary repository", disconnect: "Disconnect & delete", confirmDisconnect: "Disconnect and permanently delete this local repository folder?",
        primaryProtected: "Choose another repository as primary to disconnect this one.", makePrimary: "Make primary", confirmPrimary: "Make this the primary project repository?", promote: "Yes, make primary",
        confirm: "Yes, delete local clone", cancel: "Cancel", empty: "No repositories connected yet.",
        add: "Add repository", addAnother: "+ Add repository", saving: "Saving...", destination: "Destination path",
        optional: "Optional", read: "Read only", write: "Read and write",
        github: "GitHub App", local: "Local checkout", connectGithub: "Connect GitHub",
        addGithub: "Authorize more repositories", installation: "GitHub account", repository: "Repository",
        cloneNew: "Clone new checkout", connectSelected: "Connect existing checkout",
        chooseInstallation: "Choose an authorized account", chooseRepository: "Choose a repository",
        githubConsent: "GitHub opens a consent screen where you choose an account and the repositories this project may use.",
        githubAccess: "Hermes uses short-lived GitHub App access for selected repositories. Tokens, passwords, and SSH keys are never entered here.",
        githubUnconfigured: "GitHub App connections are not configured for this workspace.",
        githubUnconfiguredHint: "Ask a workspace administrator to enable the GitHub App. Local checkouts can still be connected below.",
        noInstallations: "No GitHub accounts are authorized for this project yet.",
        noGithubRepositories: "This installation has no selectable repositories.",
        loadingAccounts: "Loading authorized accounts...", loadingRepositories: "Loading repositories...",
        authorizationWaiting: "Waiting for GitHub authorization. You can finish in the GitHub window; this page checks automatically.",
        authorizationReady: "GitHub authorization detected. Repositories are ready to select.",
        authorizationSelection: "GitHub found multiple recently updated accounts. Finish the selection in GitHub; this page will keep checking.",
        openGithub: "Open GitHub authorization", close: "Close",
        private: "Private", public: "Public", installationId: "Installation",
        localHint: "Attach a checkout already present on this machine. Authentication stays outside this form.",
      },
      onboarding: {
        title: "Project onboarding", subtitle: "Create an isolated project, provision its dedicated PM, and select every repository through GitHub App consent.",
        projectStep: "Project", githubStep: "GitHub access", reviewStep: "Review & create",
        projectName: "Project name", projectSlug: "Project slug", workspace: "Workspace path",
        boardSlug: "Board slug", dedicatedPm: "Dedicated project manager", pmHint: "This PM profile is created for this project only and cannot be shared with another project.",
        workspaceHint: "The primary repository is created here when you finish onboarding.",
        createDraft: "Continue to GitHub", draftReady: "Project plan ready", appRequired: "GitHub App access is required to select project repositories.",
        selectedTitle: "Selected repositories", selectedEmpty: "Select at least one GitHub repository to continue.",
        addRepository: "Add repository", removeRepository: "Remove", primary: "Primary repository",
        primaryHint: "Exactly one repository must be primary. It will use the project workspace path.",
        secondaryPath: "Local path", secondaryPathHint: "Optional; secondary repositories default to a safe sibling path.",
        finish: "Create project", finishing: "Creating project...", completed: "Project created",
        openProject: "Open project", startOver: "Start over", accountRepo: "GitHub account and repository",
      },
      chat: {
        threads: "Conversation folders", message: "Message",
        placeholder: "Message the team; use @ to tag a person or agent…",
        send: "Send message", approvedReply: "Send approved client reply",
        noMessages: "No messages yet.", loadOlder: "Load older messages",
        gateway: "A shared workspace for the founder team and each project's dedicated manager. Agent progress and tool activity appear in the conversation as they happen.",
        suggestions: "Mention suggestions", you: "You", agent: "Agent",
        messageCount: function (count) { return count === 1 ? "1 message" : count + " messages"; },
        external: "External", externalNote: "Untrusted external channel. Nothing here instructs the project manager, and replies need human approval before sending.",
        person: "Person", groupAgents: "Agents", groupPeople: "People",
        global: "Global", globalHint: "Talk with every project manager in one place.",
        newConversation: "New conversation", conversationTitle: "Conversation name",
        create: "Create", cancel: "Cancel", allManagers: "All project managers",
        participants: "Participants", allProjects: "All projects",
        thinking: "Thinking", working: "Working", toolCall: "Tool call",
        status: "Status", waiting: "Waiting for agent activity…",
        createError: "Could not create the conversation: ",
      },
      empty: { board: "No tickets yet. Ask the PM in Founder's Office — that's the only way work starts.", founders: "Start by telling @pm what you need." },
      approval: { title: "Approval inbox", approve: "Approve", reject: "Reject", pending: "Pending", decided: "Decided", requires: function (rank) { return "Requires rank " + rank; }, note: "Decision note (optional)", confirmApprove: "Confirm approval", confirmReject: "Confirm rejection", cancel: "Cancel", none: "No approvals in this project.", escalate: "Escalate ↑", escalateHint: "Raise the rank required to decide this approval", targetRank: "Required rank after escalation", targetApprover: "Route to", escalateReason: "Why this needs a higher sign-off (required)", confirmEscalate: "Confirm escalation", needsRank: function (rank) { return "Escalating requires rank " + rank + " or higher"; } },
      timeline: { ticket: "Ticket ID", load: "Load timeline", none: "No timeline entries." },
      notification: { unread: "Unread only", markRead: "Mark read", none: "No notifications in this inbox.", autocomplete: "Project handles" },
      access: { members: "Human project members", ranks: "Organization ranks", agents: "Project agents", principal: "Principal", role: "Role", rank: "Rank", handle: "Handle", profile: "Hermes profile", actor: "Signed-in actor", manageHint: "Membership and roles apply only to this project. The same identity may belong to other projects.", addMember: "Add or update member", memberPlaceholder: "name@example.com", password: "Project login password (optional)", passwordPlaceholder: "At least 8 characters", passwordHint: "Stored as a hash in this project's .datansh/credentials.yaml. It never appears in the project policy or UI.", credentialed: "Password set", noCredential: "No password set", save: "Save member", remove: "Remove", readOnly: "Read-only for this role", roleHint: "Role" },
      humanTask: { title: "Delegate a human task", note: "Human tasks stay in native Kanban triage until the project manager finalizes them. They are never dispatched to an agent.", taskTitle: "Task title", outcome: "Outcome", assignee: "Human assignee", acceptance: "Acceptance criterion", evidence: "Evidence required to close", create: "Create human draft", created: "Human draft created", delegated: "Delegated human tasks", none: "No human tasks delegated in this project yet.", colTask: "Task", colAssignee: "Assignee", colStatus: "Status" },
      decision: { decisions: "Active and superseded decisions", knowledge: "Shared project knowledge", context: "Bounded agent context", none: "No project decisions or knowledge yet." },
      spend: { title: "Portfolio spend", note: "Derived from native Hermes task runs and project-scoped gateway sessions. Subscription-priced Codex turns still report model input/output analytics even when billed cost is $0.", project: "Project", amount: "MTD spend", perTicket: "Spend per completed ticket", input: "Input", cached: "Cached input", output: "Output", modelUsage: "Model usage", runs: function (count) { return count === 1 ? "1 run" : count + " runs"; }, tokens: function (count) { return count === 1 ? "1 token" : count + " tokens"; } },
      actions: { refresh: "Refresh", close: "Close" },
      announce: { loaded: function (name) { return name + " loaded"; }, sent: "Message sent", read: "Notification marked read", decided: "Approval decision recorded", saved: "Access policy updated", escalated: "Approval escalated" },
    },
  };

  function pmLocale() {
    const host = (SDK.i18n && SDK.i18n.locale) || document.documentElement.lang || navigator.language || "en";
    return String(host).toLowerCase();
  }

  function pt(path) {
    const args = Array.prototype.slice.call(arguments, 1);
    const locale = pmLocale().split("-")[0];
    let node = PM_STRINGS[locale] || PM_STRINGS.en;
    path.split(".").forEach(function (part) { node = node && node[part]; });
    if (node == null && locale !== "en") {
      node = PM_STRINGS.en;
      path.split(".").forEach(function (part) { node = node && node[part]; });
    }
    return typeof node === "function" ? node.apply(null, args) : String(node == null ? path : node);
  }

  function formatPmMoney(value, currency) {
    return new Intl.NumberFormat(pmLocale(), { style: "currency", currency: currency || "USD" }).format(Number(value || 0));
  }

  function formatPmCount(value) {
    return new Intl.NumberFormat(pmLocale()).format(Number(value || 0));
  }

  function formatPmTime(timestamp) {
    const seconds = Math.max(0, Math.floor(Date.now() / 1000) - Number(timestamp || 0));
    const units = seconds < 60 ? [0, "second"] : seconds < 3600 ? [1, "minute"] : seconds < 86400 ? [2, "hour"] : [3, "day"];
    const divisors = [1, 60, 3600, 86400];
    const amount = -Math.max(1, Math.floor(seconds / divisors[units[0]]));
    return new Intl.RelativeTimeFormat(pmLocale(), { numeric: "auto" }).format(amount, units[1]);
  }

  function absolutePmTime(timestamp) {
    return new Intl.DateTimeFormat(pmLocale(), { dateStyle: "medium", timeStyle: "short" }).format(new Date(Number(timestamp || 0) * 1000));
  }

  function safePmoHref(value) {
    const href = String(value || "");
    return href.startsWith("/pmo/") || href.startsWith("#pmo/") ? href : "#pmo/portfolio";
  }

  function usePmResource(url, refreshKey) {
    const [state, setState] = useState({ loading: true, data: null, error: null });
    const load = useCallback(function () {
      if (!url) { setState({ loading: false, data: null, error: null }); return Promise.resolve(); }
      setState(function (old) { return { loading: true, data: old.data, error: null }; });
      return SDK.fetchJSON(url).then(function (data) {
        setState({ loading: false, data: data, error: null });
        return data;
      }).catch(function (error) {
        setState({ loading: false, data: null, error: parseApiErrorMessage(error) });
      });
    }, [url, refreshKey]);
    useEffect(function () { load(); }, [load]);
    return [state, load];
  }

  function PmState(props) {
    if (props.loading) return h("div", { className: "pmo-state", role: "status" }, pt("states.loading"));
    if (props.error) return h("div", { className: "pmo-state pmo-state--error", role: "alert" },
      h("span", null, pt("states.error"), props.error),
      h(Button, { size: "sm", onClick: props.retry }, pt("states.retry")));
    return h("div", { className: "pmo-state" }, pt("states.empty"));
  }

  function PmMetric(props) {
    return h("div", { className: "pmo-metric" },
      h("span", { className: "pmo-metric__value" }, props.value),
      h("span", { className: "pmo-metric__label" }, props.label));
  }

  function PortfolioView(props) {
    const url = withBoard(`${API}/portfolio`, props.board);
    const pair = usePmResource(url, props.refreshKey);
    const state = pair[0];
    if (!state.data) return h(PmState, { loading: state.loading, error: state.error, retry: pair[1] });
    const data = state.data;
    return h("section", { className: "pmo-view", role: "region", "aria-labelledby": "pmo-portfolio-heading" },
      h("h2", { id: "pmo-portfolio-heading", tabIndex: -1 }, pt("sections.portfolio")),
      h("div", { className: "pmo-metrics" },
        h(PmMetric, { label: pt("metrics.active"), value: data.metrics.active }),
        h(PmMetric, { label: pt("metrics.flight"), value: data.metrics.in_flight }),
        h(PmMetric, { label: pt("metrics.blocked"), value: data.metrics.blocked }),
        h(PmMetric, { label: pt("metrics.needs"), value: data.metrics.needs_you }),
        h(PmMetric, { label: pt("metrics.spend"), value: formatPmMoney(data.metrics.spend_usd) })),
      data.projects.length ? h("div", { className: "pmo-table-wrap" }, h("table", { className: "pmo-table" },
        h("thead", null, h("tr", null,
          ["project", "status", "progress", "blocked", "needs", "spend", "activity"].map(function (key) {
            return h("th", { key: key, scope: "col", title: key === "progress" ? pt("portfolio.progressHelp") : undefined }, pt("portfolio." + key));
          }))),
        h("tbody", null, data.projects.map(function (project) {
          return h("tr", { key: project.project_id },
            h("th", { scope: "row" }, h("button", { type: "button", className: "pmo-link-button", onClick: function () { props.openProject(project); } }, project.name)),
            h("td", null, h("span", { className: "pmo-status" }, project.status)),
            h("td", { title: pt("portfolio.progressHelp") }, project.progress),
            h("td", null, project.blocked), h("td", null, project.needs_you),
            h("td", null, formatPmMoney(project.spend_usd)),
            h("td", { title: absolutePmTime(project.last_activity) }, formatPmTime(project.last_activity)));
        })))) : h(PmState, null));
  }

  function pmRepositoryList(payload) {
    const source = payload && typeof payload === "object" ? payload : {};
    const rows = Array.isArray(payload) ? payload
      : (Array.isArray(source.repositories) ? source.repositories
      : (Array.isArray(source.items) ? source.items : []));
    return rows.map(function (item, index) {
      const repository = item && typeof item === "object" ? item : {};
      const github = repository.github_app && typeof repository.github_app === "object" ? repository.github_app : null;
      const localPath = String(repository.path || repository.local_path || "");
      const fallbackName = localPath.replace(/[\\/]+$/, "").split(/[\\/]/).pop();
      let validation = repository.validation_status || repository.validation || repository.status;
      if (!validation && repository.valid === true) validation = "valid";
      if (!validation && repository.valid === false) validation = "invalid";
      const accessValue = String(repository.access || repository.access_mode || repository.permission || (repository.read_only ? "read" : "write")).toLowerCase();
      return {
        name: String(repository.name || repository.slug || fallbackName || ("Repository " + (index + 1))),
        path: localPath,
        remote: String(repository.remote || repository.remote_url || repository.url || ""),
        default_branch: String(repository.default_branch || repository.branch || ""),
        access: ["read", "read-only", "readonly"].includes(accessValue) ? "read" : "write",
        primary: !!(repository.primary || repository.is_primary),
        validation_status: String(validation || "unknown").toLowerCase(),
        validation_detail: String(repository.validation_detail || repository.detail || repository.message || ""),
        github_app: github ? {
          provider: String(github.provider || "github_app"),
          installation_id: String(github.installation_id || ""),
          installation_account: String(github.installation_account || ""),
          repository_id: String(github.repository_id || ""),
          full_name: String(github.full_name || ""),
        } : null,
      };
    });
  }

  function pmGithubInstallations(payload) {
    const source = payload && typeof payload === "object" ? payload : {};
    const rows = Array.isArray(payload) ? payload : (Array.isArray(source.installations) ? source.installations : []);
    return rows.map(function (item) {
      const installation = item && typeof item === "object" ? item : {};
      const account = installation.installation_account || installation.account_login || (installation.account && installation.account.login);
      return {
        installation_id: String(installation.installation_id || installation.id || ""),
        installation_account: String(account || "GitHub account"),
        repository_selection: String(installation.repository_selection || "selected"),
        contents_permission: String(installation.contents_permission || "").toLowerCase(),
      };
    }).filter(function (item) { return !!item.installation_id; });
  }

  function pmGithubRepositories(payload) {
    const source = payload && typeof payload === "object" ? payload : {};
    const rows = Array.isArray(payload) ? payload : (Array.isArray(source.repositories) ? source.repositories : []);
    return rows.map(function (item) {
      const repository = item && typeof item === "object" ? item : {};
      return {
        repository_id: String(repository.repository_id || repository.id || ""),
        full_name: String(repository.full_name || repository.name || ""),
        private: !!repository.private,
        default_branch: String(repository.default_branch || "main"),
      };
    }).filter(function (item) { return !!item.repository_id && !!item.full_name; });
  }

  function pmGithubRequest(projectRef, action, values, board) {
    const ref = encodeURIComponent(String(projectRef || ""));
    const input = values && typeof values === "object" ? values : {};
    if (action === "status") return { url: `${API}/github-app/status`, options: { method: "GET" } };
    const base = `${API}/projects/${ref}/github-app`;
    if (action === "install") return { url: withBoard(base + "/install", board), options: { method: "POST" } };
    if (action === "poll") return { url: withBoard(base + "/poll", board), options: { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state: String(input.state || "") }) } };
    if (action === "installations") return { url: withBoard(base + "/installations", board), options: { method: "GET" } };
    if (action === "repositories") {
      return { url: withBoard(base + "/installations/" + encodeURIComponent(String(input.installation_id || "")) + "/repositories", board), options: { method: "GET" } };
    }
    return { url: base, options: { method: "GET" } };
  }

  function pmOnboardingRequest(onboardingId, action, values) {
    const input = values && typeof values === "object" ? values : {};
    const id = encodeURIComponent(String(onboardingId || ""));
    const base = `${API}/project-onboarding` + (id ? "/" + id : "");
    if (action === "create") {
      const payload = {
        slug: String(input.slug || "").trim(),
        name: String(input.name || "").trim(),
        workspace_path: String(input.workspace_path || "").trim(),
      };
      if (String(input.board_slug || "").trim()) payload.board_slug = String(input.board_slug).trim();
      return { url: `${API}/project-onboarding`, options: { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) } };
    }
    if (action === "install") return { url: base + "/github-app/install", options: { method: "POST" } };
    if (action === "poll") return { url: base + "/github-app/poll", options: { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state: String(input.state || "") }) } };
    if (action === "installations") return { url: base + "/github-app/installations", options: { method: "GET" } };
    if (action === "available") {
      return { url: base + "/github-app/installations/" + encodeURIComponent(String(input.installation_id || "")) + "/repositories", options: { method: "GET" } };
    }
    if (action === "add") {
      const payload = {
        installation_id: Number(input.installation_id),
        repository_id: Number(input.repository_id),
        full_name: String(input.full_name || "").trim(),
        default_branch: String(input.default_branch || "main").trim() || "main",
        access: input.access === "read" ? "read" : "write",
        primary: !!input.primary,
      };
      if (String(input.name || "").trim()) payload.name = String(input.name).trim();
      if (String(input.local_path || "").trim()) payload.local_path = String(input.local_path).trim();
      return { url: base + "/repositories", options: { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) } };
    }
    if (action === "remove") return { url: base + "/repositories/" + encodeURIComponent(String(input.name || "")), options: { method: "DELETE" } };
    if (action === "complete") return { url: base + "/complete", options: { method: "POST" } };
    return { url: base, options: { method: "GET" } };
  }

  function pmOnboardingRepositories(payload) {
    const source = payload && typeof payload === "object" ? payload : {};
    const rows = Array.isArray(source.repositories) ? source.repositories : [];
    return rows.map(function (item, index) {
      const repository = item && typeof item === "object" ? item : {};
      const github = repository.github_app && typeof repository.github_app === "object" ? repository.github_app : repository;
      const fullName = String(github.full_name || repository.full_name || "");
      return {
        name: String(repository.name || fullName.split("/").pop() || ("repository-" + (index + 1))),
        full_name: fullName,
        installation_id: String(github.installation_id || repository.installation_id || ""),
        installation_account: String(github.installation_account || repository.installation_account || "GitHub account"),
        default_branch: String(repository.default_branch || "main"),
        access: String(repository.access || "write"),
        primary: !!repository.primary,
        local_path: String(repository.local_path || repository.path || ""),
      };
    });
  }

  function pmRepositoryRequest(projectRef, action, values) {
    const ref = encodeURIComponent(String(projectRef || ""));
    const input = values && typeof values === "object" ? values : {};
    const base = `${API}/projects/${ref}/repositories`;
    if (action === "promote") {
      return { url: base + "/" + encodeURIComponent(String(input.name || "")) + "/primary", options: { method: "POST" } };
    }
    if (action === "remove") {
      return { url: base + "/" + encodeURIComponent(String(input.name || "")), options: { method: "DELETE" } };
    }
    const payload = {};
    if (action === "connect") {
      payload.path = String(input.path || "").trim();
      if (String(input.url || "").trim()) payload.url = String(input.url).trim();
    } else if (action === "clone") {
      if (String(input.url || "").trim()) payload.url = String(input.url).trim();
      if (String(input.path || "").trim()) payload.path = String(input.path).trim();
    }
    if (String(input.name || "").trim()) payload.name = String(input.name).trim();
    payload.default_branch = String(input.default_branch || "main").trim() || "main";
    payload.access = input.access === "read" ? "read" : "write";
    if (input.primary) payload.primary = true;
    if (input.installation_id && input.repository_id && String(input.full_name || "").trim()) {
      payload.installation_id = Number(input.installation_id);
      payload.repository_id = Number(input.repository_id);
      payload.full_name = String(input.full_name).trim();
    }
    return {
      url: base + "/" + action,
      options: { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
    };
  }

  function pmRepositoryStatusTone(status) {
    const value = String(status || "unknown").toLowerCase();
    if (["valid", "ready", "connected", "ok"].includes(value)) return "good";
    if (["invalid", "error", "missing", "unavailable"].includes(value)) return "bad";
    return "neutral";
  }

  function RepositoryCard(props) {
    const repository = props.repository;
    const github = repository.github_app;
    const confirmingDisconnect = props.confirming === "remove:" + repository.name;
    const confirmingPrimary = props.confirming === "promote:" + repository.name;
    const tone = pmRepositoryStatusTone(repository.validation_status);
    let actions;
    if (repository.primary) {
      actions = h("span", null, pt("repositories.primaryProtected"));
    } else if (confirmingPrimary) {
      actions = h(React.Fragment, null,
        h("span", null, pt("repositories.confirmPrimary")),
        h(Button, { size: "sm", disabled: props.busy, onClick: function () { props.promote(repository.name); } }, pt("repositories.promote")),
        h(Button, { size: "sm", disabled: props.busy, onClick: function () { props.setConfirming(""); } }, pt("repositories.cancel")));
    } else if (confirmingDisconnect) {
      actions = h(React.Fragment, null,
        h("span", null, pt("repositories.confirmDisconnect")),
        h(Button, { size: "sm", disabled: props.busy, onClick: function () { props.remove(repository.name); } }, pt("repositories.confirm")),
        h(Button, { size: "sm", disabled: props.busy, onClick: function () { props.setConfirming(""); } }, pt("repositories.cancel")));
    } else {
      actions = h(React.Fragment, null,
        h(Button, { size: "sm", disabled: props.busy || !repository.name, onClick: function () { props.setConfirming("promote:" + repository.name); } }, pt("repositories.makePrimary")),
        h(Button, { size: "sm", disabled: props.busy || !repository.name, onClick: function () { props.setConfirming("remove:" + repository.name); } }, pt("repositories.disconnect")));
    }
    return h("article", { className: "pmo-repository-card" },
      h("div", { className: "pmo-repository-card__head" },
        h("span", { className: "pmo-repository-mark", "aria-hidden": "true" }, "</>"),
        h("div", { className: "pmo-repository-card__identity" },
          h("h4", null, repository.name),
          h("code", { title: repository.path || undefined }, repository.path || "Path unavailable")),
        repository.primary ? h("span", { className: "pmo-repository-primary" }, pt("repositories.primary")) : null,
        h("span", { className: "pmo-repository-validation pmo-repository-validation--" + tone, title: repository.validation_detail || undefined },
          h("span", { "aria-hidden": "true" }), repository.validation_status)),
      github ? h("div", { className: "pmo-repository-github", "aria-label": "GitHub App repository " + (github.full_name || repository.name) },
        h("span", { className: "pmo-repository-github__brand" }, pt("repositories.github")),
        h("strong", null, github.installation_account),
        h("span", null, github.full_name || repository.remote, " · ", pt("repositories.installationId"), " #", github.installation_id)) : null,
      h("dl", { className: "pmo-repository-meta" },
        h("div", null, h("dt", null, pt("repositories.remote")), h("dd", { title: repository.remote || undefined }, repository.remote || "\u2014")),
        h("div", null, h("dt", null, pt("repositories.branch")), h("dd", null, repository.default_branch || "\u2014")),
        h("div", null, h("dt", null, pt("repositories.access")), h("dd", null, repository.access === "read" ? pt("repositories.read") : (repository.access === "write" ? pt("repositories.write") : (repository.access || "\u2014"))))),
      h("div", { className: "pmo-repository-card__actions", "aria-live": "polite" }, actions));
  }

  function GithubRepositoryComposer(props) {
    const statusRequest = pmGithubRequest(props.projectRef, "status", {}, props.board);
    const statusPair = usePmResource(statusRequest.url, props.refreshKey);
    const statusState = statusPair[0];
    const status = statusState.data || {};
    const [githubRefresh, setGithubRefresh] = useState(0);
    const installationsRequest = pmGithubRequest(props.projectRef, "installations", {}, props.board);
    const installationsPair = usePmResource(status.configured ? installationsRequest.url : null, String(props.refreshKey || "") + ":github-installations:" + githubRefresh);
    const installationsState = installationsPair[0];
    const installations = pmGithubInstallations(installationsState.data);
    const [installationId, setInstallationId] = useState("");
    const [repositoryId, setRepositoryId] = useState("");
    const [destination, setDestination] = useState("");
    const [access, setAccess] = useState("write");
    const [operation, setOperation] = useState("clone");
    const [consentBusy, setConsentBusy] = useState(false);
    const [consentError, setConsentError] = useState("");
    const [consentState, setConsentState] = useState("");
    const [consentNotice, setConsentNotice] = useState("");
    const [consentUrl, setConsentUrl] = useState("");

    useEffect(function () {
      const refreshOnFocus = function () { setGithubRefresh(function (value) { return value + 1; }); };
      window.addEventListener("focus", refreshOnFocus);
      return function () { window.removeEventListener("focus", refreshOnFocus); };
    }, [props.projectRef]);

    useEffect(function () {
      if (!consentState) return undefined;
      let stopped = false;
      let timer = null;
      const poll = function () {
        const request = pmGithubRequest(props.projectRef, "poll", { state: consentState }, props.board);
        SDK.fetchJSON(request.url, request.options).then(function (result) {
          if (stopped) return;
          if (result && result.status === "available") {
            setConsentState("");
            setConsentUrl("");
            setConsentNotice("ready");
            setGithubRefresh(function (value) { return value + 1; });
            if (props.announce) props.announce(pt("repositories.authorizationReady"));
            return;
          }
          setConsentNotice(result && result.status === "needs_selection" ? "selection" : "waiting");
          timer = window.setTimeout(poll, Math.max(3, Number(result && result.poll_after_seconds || 5)) * 1000);
        }).catch(function (error) {
          if (stopped) return;
          const message = parseApiErrorMessage(error);
          if (/expired|invalid|already used/i.test(message)) {
            setConsentState("");
            setConsentNotice("");
            setConsentError(message);
            return;
          }
          timer = window.setTimeout(poll, 5000);
        });
      };
      poll();
      return function () { stopped = true; if (timer) window.clearTimeout(timer); };
    }, [consentState, props.projectRef, props.board]);

    useEffect(function () {
      if (!installations.length) { setInstallationId(""); return; }
      if (!installations.some(function (item) { return item.installation_id === installationId; })) {
        setInstallationId(installations[0].installation_id);
        setRepositoryId("");
      }
    }, [installationsState.data]);

    const repositoriesRequest = pmGithubRequest(props.projectRef, "repositories", { installation_id: installationId }, props.board);
    const repositoriesPair = usePmResource(installationId ? repositoriesRequest.url : null, String(props.refreshKey || "") + ":github-repositories:" + installationId);
    const repositoriesState = repositoriesPair[0];
    const availableRepositories = pmGithubRepositories(repositoriesState.data);
    useEffect(function () {
      if (!availableRepositories.length) { setRepositoryId(""); return; }
      if (!availableRepositories.some(function (item) { return item.repository_id === repositoryId; })) {
        setRepositoryId(availableRepositories[0].repository_id);
      }
    }, [repositoriesState.data]);

    const selectedInstallation = installations.find(function (item) { return item.installation_id === installationId; });
    const selectedRepository = availableRepositories.find(function (item) { return item.repository_id === repositoryId; });
    const writeAllowed = !selectedInstallation || selectedInstallation.contents_permission !== "read";
    useEffect(function () { if (!writeAllowed) setAccess("read"); }, [writeAllowed]);

    const startConsent = function () {
      if (consentBusy) return;
      const request = pmGithubRequest(props.projectRef, "install", {}, props.board);
      let consentWindow = null;
      try { consentWindow = window.open("about:blank", "pmo-github-consent"); if (consentWindow) consentWindow.opener = null; } catch (_error) { consentWindow = null; }
      setConsentBusy(true); setConsentError(""); setConsentNotice(""); setConsentUrl("");
      SDK.fetchJSON(request.url, request.options).then(function (result) {
        const consentUrl = String(result && result.consent_url || "");
        if (!consentUrl) throw new Error("GitHub consent URL was not returned.");
        setConsentState(String(result && result.state || ""));
        setConsentNotice("waiting");
        setConsentUrl(consentUrl);
        if (consentWindow) consentWindow.location.replace(consentUrl);
      }).catch(function (error) {
        if (consentWindow) consentWindow.close();
        setConsentError(parseApiErrorMessage(error));
      }).finally(function () { setConsentBusy(false); });
    };

    const submit = function (event) {
      event.preventDefault();
      if (!selectedInstallation || !selectedRepository) return;
      props.mutate(operation, {
        path: destination,
        default_branch: selectedRepository.default_branch,
        access: access,
        installation_id: selectedInstallation.installation_id,
        repository_id: selectedRepository.repository_id,
        full_name: selectedRepository.full_name,
      }).then(function (result) { if (result !== false) setDestination(""); });
    };

    if (!statusState.data) return h(PmState, { loading: statusState.loading, error: statusState.error, retry: statusPair[1] });
    if (!status.configured) return h("div", { className: "pmo-github-unconfigured", role: "status" },
      h("span", { className: "pmo-github-mark", "aria-hidden": "true" }, "GH"),
      h("div", null, h("strong", null, pt("repositories.githubUnconfigured")), h("p", null, pt("repositories.githubUnconfiguredHint"))));

    return h("div", { className: "pmo-github-connect" },
      h("div", { className: "pmo-github-consent" },
        h("span", { className: "pmo-github-mark", "aria-hidden": "true" }, "GH"),
        h("div", null, h("strong", null, status.app_slug || pt("repositories.github")), h("p", null, pt("repositories.githubConsent"))),
        h(Button, { size: "sm", type: "button", disabled: consentBusy || props.busy, onClick: startConsent }, consentBusy ? pt("repositories.saving") : (installations.length ? pt("repositories.addGithub") : pt("repositories.connectGithub")))),
      h("p", { className: "pmo-github-access" }, pt("repositories.githubAccess")),
      consentNotice ? h("div", { className: "pmo-github-authorization pmo-github-authorization--" + consentNotice, role: "status", "aria-live": "polite" },
        h("span", { className: "pmo-github-authorization__pulse", "aria-hidden": "true" }),
        h("strong", null, consentNotice === "ready" ? pt("repositories.authorizationReady") : (consentNotice === "selection" ? pt("repositories.authorizationSelection") : pt("repositories.authorizationWaiting"))),
        consentUrl ? h("a", { href: consentUrl, target: "_blank", rel: "noopener noreferrer" }, pt("repositories.openGithub")) : null) : null,
      consentError ? h("p", { className: "pmo-state pmo-state--error", role: "alert" }, consentError) : null,
      installationsState.loading ? h("p", { className: "pmo-github-progress", role: "status" }, pt("repositories.loadingAccounts")) : null,
      installationsState.error ? h(PmState, { error: installationsState.error, retry: installationsPair[1] }) : null,
      !installationsState.loading && !installationsState.error && !installations.length ? h("p", { className: "pmo-github-progress" }, pt("repositories.noInstallations")) : null,
      installations.length ? h("form", { className: "pmo-repository-form", onSubmit: submit },
        h("div", { className: "pmo-repository-tabs pmo-github-operation", role: "group", "aria-label": pt("repositories.add") },
          h("button", { type: "button", className: operation === "clone" ? "is-active" : "", "aria-pressed": operation === "clone", onClick: function () { setOperation("clone"); } }, pt("repositories.cloneNew")),
          h("button", { type: "button", className: operation === "connect" ? "is-active" : "", "aria-pressed": operation === "connect", onClick: function () { setOperation("connect"); } }, pt("repositories.connectSelected"))),
        h("div", { className: "pmo-repository-fields pmo-github-fields" },
          h("label", { htmlFor: "pmo-github-installation" }, h("span", null, pt("repositories.installation")),
            h("select", { id: "pmo-github-installation", value: installationId, disabled: props.busy, onChange: function (event) { setInstallationId(event.target.value); setRepositoryId(""); } },
              h("option", { value: "", disabled: true }, pt("repositories.chooseInstallation")),
              installations.map(function (item) { return h("option", { key: item.installation_id, value: item.installation_id }, item.installation_account, " · #", item.installation_id); }))),
          h("label", { htmlFor: "pmo-github-repository" }, h("span", null, pt("repositories.repository")),
            h("select", { id: "pmo-github-repository", value: repositoryId, required: true, disabled: props.busy || repositoriesState.loading || !installationId, onChange: function (event) { setRepositoryId(event.target.value); } },
              h("option", { value: "", disabled: true }, repositoriesState.loading ? pt("repositories.loadingRepositories") : pt("repositories.chooseRepository")),
              availableRepositories.map(function (item) { return h("option", { key: item.repository_id, value: item.repository_id }, item.full_name, " · ", item.private ? pt("repositories.private") : pt("repositories.public")); }))),
          h("label", { htmlFor: "pmo-github-destination" }, h("span", null, operation === "clone" ? pt("repositories.destination") : pt("repositories.path"), operation === "clone" ? h("small", null, pt("repositories.optional")) : null),
            h(Input, { id: "pmo-github-destination", required: operation === "connect", disabled: props.busy, value: destination, placeholder: operation === "clone" ? "Projects/repository" : "C:\\Projects\\repository", autoComplete: "off", onChange: function (event) { setDestination(event.target.value); } })),
          h("label", { htmlFor: "pmo-github-access" }, h("span", null, pt("repositories.access")),
            h("select", { id: "pmo-github-access", disabled: props.busy, value: access, onChange: function (event) { setAccess(event.target.value); } },
              h("option", { value: "write", disabled: !writeAllowed }, pt("repositories.write")),
              h("option", { value: "read" }, pt("repositories.read"))))),
        repositoriesState.error ? h(PmState, { error: repositoriesState.error, retry: repositoriesPair[1] }) : null,
        !repositoriesState.loading && repositoriesState.data && !availableRepositories.length ? h("p", { className: "pmo-github-progress" }, pt("repositories.noGithubRepositories")) : null,
        h("div", { className: "pmo-repository-form__footer" },
          h("span", { className: "pmo-github-selection" }, selectedRepository ? selectedRepository.default_branch : ""),
          h(Button, { size: "sm", type: "submit", disabled: props.busy || !selectedInstallation || !selectedRepository || (operation === "connect" && !destination.trim()) }, props.busy ? pt("repositories.saving") : (operation === "clone" ? pt("repositories.clone") : pt("repositories.connect"))))) : null);
  }

  function RepositoryPanel(props) {
    const project = props.project;
    const projectRef = project && (project.project_id || project.slug);
    const url = projectRef ? withBoard(`${API}/projects/${encodeURIComponent(projectRef)}/repositories`, props.board) : null;
    const pair = usePmResource(url, props.refreshKey);
    const state = pair[0];
    const [mode, setMode] = useState("github");
    const [name, setName] = useState("");
    const [localPath, setLocalPath] = useState("");
    const [defaultBranch, setDefaultBranch] = useState("main");
    const [access, setAccess] = useState("write");
    const [primary, setPrimary] = useState(false);
    const [busy, setBusy] = useState("");
    const [error, setError] = useState("");
    const [confirming, setConfirming] = useState("");
    const [adding, setAdding] = useState(false);
    const repositories = pmRepositoryList(state.data);

    const mutate = function (action, values) {
      if (!projectRef || busy) return Promise.resolve(false);
      const request = pmRepositoryRequest(projectRef, action, values);
      setBusy(action); setError("");
      return SDK.fetchJSON(withBoard(request.url, props.board), request.options)
        .then(function () {
          setName(""); setLocalPath(""); setDefaultBranch("main"); setAccess("write"); setPrimary(false); setConfirming("");
          if (action !== "remove") setAdding(false);
          if (props.announce) props.announce(action === "remove" ? "Repository disconnected and local folder deleted" : (action === "promote" ? "Primary repository changed" : "Repository connected"));
          return pair[1]();
        })
        .catch(function (err) { setError(parseApiErrorMessage(err)); return false; })
        .finally(function () { setBusy(""); });
    };
    const submitLocal = function (event) {
      event.preventDefault();
      return mutate("connect", { name: name, path: localPath, default_branch: defaultBranch, access: access, primary: primary });
    };

    return h("section", { className: "pmo-panel pmo-panel--wide pmo-repositories", "aria-labelledby": "pmo-repositories-heading" },
      h("div", { className: "pmo-repositories__heading" }, h("div", null,
        h("h3", { id: "pmo-repositories-heading" }, pt("repositories.title")),
        h("p", { className: "pmo-muted" }, pt("repositories.subtitle"))),
        h("div", { className: "pmo-repositories__actions" },
          state.data ? h("span", { className: "pmo-repository-count" }, String(repositories.length), repositories.length === 1 ? " repository" : " repositories") : null,
          h(Button, { size: "sm", type: "button", disabled: !state.data, onClick: function () { setAdding(true); setError(""); } }, pt("repositories.addAnother")))),
      !state.data ? h(PmState, { loading: state.loading, error: state.error, retry: pair[1] }) :
        h(React.Fragment, null,
          repositories.length ? h("div", { className: "pmo-repository-list" }, repositories.map(function (repository) {
            return h(RepositoryCard, { key: repository.name, repository: repository, confirming: confirming, setConfirming: setConfirming, promote: function (repositoryName) { return mutate("promote", { name: repositoryName }); }, remove: function (repositoryName) { return mutate("remove", { name: repositoryName }); }, busy: !!busy });
          })) : h("div", { className: "pmo-repository-empty" }, h("span", { "aria-hidden": "true" }, "</>"), h("p", null, pt("repositories.empty")))),
      adding ? h("div", { className: "hermes-kanban-dialog-backdrop pmo-repository-dialog-backdrop", role: "presentation", onClick: function (event) { if (event.target === event.currentTarget && !busy) setAdding(false); } },
        h("section", { className: "hermes-kanban-dialog pmo-repository-dialog", role: "dialog", "aria-modal": "true", "aria-labelledby": "pmo-add-repository-title" },
          h("div", { className: "pmo-repository-dialog__head" },
            h("div", null, h("h3", { id: "pmo-add-repository-title" }, pt("repositories.add")), h("p", null, "Choose a GitHub repository to clone, attach an existing checkout, or connect a local repository.")),
            h("button", { type: "button", className: "pmo-repository-dialog__close", disabled: !!busy, "aria-label": pt("repositories.close"), onClick: function () { setAdding(false); } }, "\u00d7")),
          h("div", { className: "pmo-repository-composer" },
            h("div", { className: "pmo-repository-tabs", role: "group", "aria-label": pt("repositories.add") },
              h("button", { type: "button", className: mode === "github" ? "is-active" : "", "aria-pressed": mode === "github", onClick: function () { setMode("github"); setError(""); } }, pt("repositories.github")),
              h("button", { type: "button", className: mode === "local" ? "is-active" : "", "aria-pressed": mode === "local", onClick: function () { setMode("local"); setError(""); } }, pt("repositories.local"))),
            mode === "github" ? h(GithubRepositoryComposer, { projectRef: projectRef, board: props.board, refreshKey: props.refreshKey, busy: !!busy, mutate: mutate }) :
              h("form", { className: "pmo-repository-form", onSubmit: submitLocal },
                h("p", { className: "pmo-local-hint" }, pt("repositories.localHint")),
                h("div", { className: "pmo-repository-fields" },
                  h("label", { htmlFor: "pmo-repository-path" }, h("span", null, pt("repositories.path")), h(Input, { id: "pmo-repository-path", required: true, disabled: !!busy, value: localPath, placeholder: "C:\\Projects\\repository", autoComplete: "off", onChange: function (event) { setLocalPath(event.target.value); } })),
                  h("label", { htmlFor: "pmo-repository-name" }, h("span", null, pt("repositories.name"), h("small", null, pt("repositories.optional"))), h(Input, { id: "pmo-repository-name", disabled: !!busy, value: name, pattern: "[a-z0-9][a-z0-9_-]{0,63}", placeholder: "repository", autoComplete: "off", onChange: function (event) { setName(event.target.value); } })),
                  h("label", { htmlFor: "pmo-repository-branch" }, h("span", null, pt("repositories.branch")), h(Input, { id: "pmo-repository-branch", required: true, disabled: !!busy, value: defaultBranch, placeholder: "main", autoComplete: "off", onChange: function (event) { setDefaultBranch(event.target.value); } })),
                  h("label", { htmlFor: "pmo-repository-access" }, h("span", null, pt("repositories.access")), h("select", { id: "pmo-repository-access", disabled: !!busy, value: access, onChange: function (event) { setAccess(event.target.value); } }, h("option", { value: "write" }, pt("repositories.write")), h("option", { value: "read" }, pt("repositories.read"))))),
                h("div", { className: "pmo-repository-form__footer" },
                  h("label", { className: "pmo-check" }, h(Checkbox, { checked: primary, disabled: !!busy, onCheckedChange: function (checked) { setPrimary(!!checked); } }), h("span", null, pt("repositories.primary"))),
                  h(Button, { size: "sm", type: "submit", disabled: !!busy || !localPath.trim() }, busy ? pt("repositories.saving") : pt("repositories.connect")))),
            error ? h("p", { className: "pmo-state pmo-state--error", role: "alert" }, error) : null))) : null);
  }

  function pmProjectSlug(value) {
    return String(value || "").toLowerCase().trim().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
  }

  function pmOnboardingErrorMessage(error) {
    const message = parseApiErrorMessage(error);
    if (/winerror\s*2|no such file|cannot find (the )?(file|path)|workspace[_ ]path.*parent|parent folder.*(missing|exist)/i.test(message)) {
      return "Workspace parent folder does not exist. Choose an existing parent folder.";
    }
    return message;
  }

  function ProjectOnboardingView(props) {
    const [name, setName] = useState("");
    const [slug, setSlug] = useState("");
    const [slugEdited, setSlugEdited] = useState(false);
    const [workspacePath, setWorkspacePath] = useState("");
    const [boardSlug, setBoardSlug] = useState("");
    const [draft, setDraft] = useState(null);
    const [completed, setCompleted] = useState(null);
    const [busy, setBusy] = useState("");
    const [error, setError] = useState("");
    const [showGithubSetup, setShowGithubSetup] = useState(false);
    const [githubRefresh, setGithubRefresh] = useState(0);
    const [githubConsentState, setGithubConsentState] = useState("");
    const [githubAccessNotice, setGithubAccessNotice] = useState(null);
    const [installationId, setInstallationId] = useState("");
    const [repositoryId, setRepositoryId] = useState("");
    const [secondaryPath, setSecondaryPath] = useState("");
    const [access, setAccess] = useState("write");
    const [primary, setPrimary] = useState(true);

    const statusPair = usePmResource(`${API}/github-app/status`, props.refreshKey);
    const statusState = statusPair[0];
    const status = statusState.data || {};
    const draftId = draft && draft.onboarding_id;
    const installationsRequest = pmOnboardingRequest(draftId, "installations", {});
    const installationsPair = usePmResource(draftId && status.configured ? installationsRequest.url : null, String(githubRefresh) + ":onboarding-installations");
    const installationsState = installationsPair[0];
    const installations = pmGithubInstallations(installationsState.data);
    const repositories = pmOnboardingRepositories(draft || {});
    const visibleRepositories = completed ? pmOnboardingRepositories(completed) : repositories;

    useEffect(function () {
      if (!draftId) return undefined;
      const refreshOnFocus = function () { setGithubRefresh(function (value) { return value + 1; }); };
      window.addEventListener("focus", refreshOnFocus);
      return function () { window.removeEventListener("focus", refreshOnFocus); };
    }, [draftId]);
    useEffect(function () {
      if (!draftId || !githubConsentState) return undefined;
      let stopped = false;
      let timer = null;
      const poll = function () {
        const request = pmOnboardingRequest(draftId, "poll", { state: githubConsentState });
        SDK.fetchJSON(request.url, request.options).then(function (result) {
          if (stopped) return;
          setGithubAccessNotice(result || { status: "waiting" });
          if (result && result.status === "available") {
            setGithubConsentState("");
            setGithubRefresh(function (value) { return value + 1; });
            if (props.announce) props.announce("GitHub access is available.");
            return;
          }
          timer = window.setTimeout(poll, 10000);
        }).catch(function (reason) {
          if (stopped) return;
          const message = parseApiErrorMessage(reason);
          setGithubAccessNotice({ status: "error", detail: message });
          if (!/expired|invalid|already used/i.test(message)) timer = window.setTimeout(poll, 10000);
        });
      };
      poll();
      return function () { stopped = true; if (timer) window.clearTimeout(timer); };
    }, [draftId, githubConsentState]);
    useEffect(function () {
      if (!installations.length) { setInstallationId(""); return; }
      if (!installations.some(function (item) { return item.installation_id === installationId; })) {
        setInstallationId(installations[0].installation_id); setRepositoryId("");
      }
    }, [installationsState.data]);

    const availableRequest = pmOnboardingRequest(draftId, "available", { installation_id: installationId });
    const availablePair = usePmResource(draftId && installationId ? availableRequest.url : null, String(githubRefresh) + ":onboarding-repositories:" + installationId);
    const availableState = availablePair[0];
    const availableRepositories = pmGithubRepositories(availableState.data);
    useEffect(function () {
      if (!availableRepositories.length) { setRepositoryId(""); return; }
      if (!availableRepositories.some(function (item) { return item.repository_id === repositoryId; })) setRepositoryId(availableRepositories[0].repository_id);
    }, [availableState.data]);
    useEffect(function () { setPrimary(repositories.length === 0); }, [draft && draft.repositories]);

    const selectedInstallation = installations.find(function (item) { return item.installation_id === installationId; });
    const selectedRepository = availableRepositories.find(function (item) { return item.repository_id === repositoryId; });
    const writeAllowed = !selectedInstallation || selectedInstallation.contents_permission !== "read";
    useEffect(function () {
      if (!writeAllowed) setAccess("read");
      else if (primary) setAccess("write");
    }, [writeAllowed, primary]);

    const createDraft = function (event) {
      event.preventDefault();
      if (busy) return;
      const request = pmOnboardingRequest("", "create", { slug: slug, name: name, workspace_path: workspacePath, board_slug: boardSlug });
      setBusy("draft"); setError("");
      SDK.fetchJSON(request.url, request.options).then(function (result) {
        setDraft(result);
        if (props.announce) props.announce(pt("onboarding.draftReady"));
      }).catch(function (reason) { setError(pmOnboardingErrorMessage(reason)); })
        .finally(function () { setBusy(""); });
    };

    const beginConsent = function () {
      if (!draftId || busy) return;
      const request = pmOnboardingRequest(draftId, "install", {});
      let consentWindow = null;
      try { consentWindow = window.open("about:blank", "pmo-github-consent"); if (consentWindow) consentWindow.opener = null; } catch (_error) { consentWindow = null; }
      setBusy("consent"); setError("");
      SDK.fetchJSON(request.url, request.options).then(function (result) {
        const consentUrl = String(result && result.consent_url || "");
        if (!consentUrl) throw new Error("GitHub consent URL was not returned.");
        setGithubConsentState(String(result.state || ""));
        setGithubAccessNotice({ status: "waiting" });
        if (consentWindow) consentWindow.location.replace(consentUrl);
        else window.location.assign(consentUrl);
      }).catch(function (reason) {
        if (consentWindow) consentWindow.close();
        setError(parseApiErrorMessage(reason));
      }).finally(function () { setBusy(""); });
    };

    const connectGithub = function () {
      if (status.configured) { beginConsent(); return; }
      setError("");
      setShowGithubSetup(true);
    };

    const addRepository = function (event) {
      event.preventDefault();
      if (!draftId || !selectedInstallation || !selectedRepository || busy) return;
      const request = pmOnboardingRequest(draftId, "add", {
        installation_id: selectedInstallation.installation_id,
        repository_id: selectedRepository.repository_id,
        full_name: selectedRepository.full_name,
        local_path: primary ? "" : secondaryPath,
        default_branch: selectedRepository.default_branch,
        access: access,
        primary: primary,
      });
      setBusy("repository"); setError("");
      SDK.fetchJSON(request.url, request.options).then(function (result) {
        setDraft(result); setSecondaryPath(""); setRepositoryId("");
        if (props.announce) props.announce("Repository selected");
      }).catch(function (reason) { setError(parseApiErrorMessage(reason)); })
        .finally(function () { setBusy(""); });
    };

    const removeRepository = function (repositoryName) {
      if (!draftId || busy) return;
      const request = pmOnboardingRequest(draftId, "remove", { name: repositoryName });
      setBusy("remove"); setError("");
      SDK.fetchJSON(request.url, request.options).then(setDraft)
        .catch(function (reason) { setError(parseApiErrorMessage(reason)); })
        .finally(function () { setBusy(""); });
    };

    const primaryRepositoryCount = repositories.filter(function (item) { return item.primary; }).length;
    const canCompleteProject = !!draftId && primaryRepositoryCount === 1 && busy !== "complete";
    const completeProject = function () {
      // Repository-list refreshes and a completed "add repository" request
      // must not strand the final action in a disabled state. Completion is
      // independently protected on the server and only conflicts with an
      // actual completion request.
      if (!draftId || busy === "complete" || primaryRepositoryCount !== 1) return;
      const request = pmOnboardingRequest(draftId, "complete", {});
      setBusy("complete"); setError("");
      SDK.fetchJSON(request.url, request.options).then(function (result) {
        setCompleted(result);
        if (props.announce) props.announce(pt("onboarding.completed"));
      }).catch(function (reason) { setError(parseApiErrorMessage(reason)); })
        .finally(function () { setBusy(""); });
    };

    const stage = completed ? 4 : (!draft ? 1 : (repositories.length ? 3 : 2));
    return h("section", { className: "pmo-view pmo-onboarding", role: "region", "aria-labelledby": "pmo-onboarding-heading" },
      h("div", { className: "pmo-heading-row" }, h("div", null,
        h("h2", { id: "pmo-onboarding-heading", tabIndex: -1 }, pt("onboarding.title")),
        h("p", { className: "pmo-muted" }, pt("onboarding.subtitle")))),
      h("ol", { className: "pmo-onboarding-steps", "aria-label": pt("onboarding.title") },
        [pt("onboarding.projectStep"), pt("onboarding.githubStep"), pt("onboarding.reviewStep")].map(function (label, index) {
          const number = index + 1;
          return h("li", { key: label, className: stage > number ? "is-complete" : (stage === number ? "is-active" : ""), "aria-current": stage === number ? "step" : undefined },
            h("span", null, stage > number ? "✓" : String(number)), h("strong", null, label));
        })),
      !draft ? h("form", { className: "pmo-panel pmo-onboarding-card", onSubmit: createDraft },
        h("div", { className: "pmo-onboarding-card__title" }, h("span", null, "1"), h("div", null, h("h3", null, pt("onboarding.projectStep")), h("p", null, pt("onboarding.workspaceHint")))),
        h("div", { className: "pmo-onboarding-fields" },
          h("label", { htmlFor: "pmo-onboarding-name" }, h("span", null, pt("onboarding.projectName")), h(Input, { id: "pmo-onboarding-name", required: true, disabled: !!busy, value: name, autoComplete: "organization", onChange: function (event) { const next = event.target.value; setName(next); if (!slugEdited) setSlug(pmProjectSlug(next)); } })),
          h("label", { htmlFor: "pmo-onboarding-slug" }, h("span", null, pt("onboarding.projectSlug")), h(Input, { id: "pmo-onboarding-slug", required: true, disabled: !!busy, value: slug, pattern: "[a-z0-9][a-z0-9_-]{0,63}", autoComplete: "off", onChange: function (event) { setSlugEdited(true); setSlug(pmProjectSlug(event.target.value)); } })),
          h("label", { htmlFor: "pmo-onboarding-workspace" }, h("span", null, pt("onboarding.workspace")), h(Input, { id: "pmo-onboarding-workspace", required: true, disabled: !!busy, value: workspacePath, placeholder: "C:\\Projects\\new-project", autoComplete: "off", onChange: function (event) { setWorkspacePath(event.target.value); } })),
          h("label", { htmlFor: "pmo-onboarding-board" }, h("span", null, pt("onboarding.boardSlug"), h("small", null, pt("repositories.optional"))), h(Input, { id: "pmo-onboarding-board", disabled: !!busy, value: boardSlug, pattern: "[a-z0-9][a-z0-9_-]{0,63}", autoComplete: "off", onChange: function (event) { setBoardSlug(pmProjectSlug(event.target.value)); } }))),
        h("div", { className: "pmo-onboarding-pm" },
          h("span", { className: "pmo-onboarding-avatar", "aria-hidden": "true" }, "PM"),
          h("div", null, h("span", null, pt("onboarding.dedicatedPm")), h("strong", null, "pm-", slug || "project"), h("p", null, pt("onboarding.pmHint")))),
        h("div", { className: "pmo-onboarding-actions" }, h(Button, { size: "sm", type: "submit", disabled: !!busy || !name.trim() || !slug || !workspacePath.trim() }, busy ? pt("repositories.saving") : pt("onboarding.createDraft"))),
        error ? h("p", { className: "pmo-state pmo-state--error", role: "alert" }, error) : null) :
        h(React.Fragment, null,
          h("article", { className: "pmo-panel pmo-onboarding-summary" },
            h("div", null, h("span", null, pt("onboarding.projectName")), h("strong", null, draft.name)),
            h("div", null, h("span", null, pt("onboarding.workspace")), h("code", null, draft.workspace_path)),
            h("div", null, h("span", null, pt("onboarding.dedicatedPm")), h("strong", null, draft.pm_profile))),
          h("article", { className: "pmo-panel pmo-onboarding-card" },
            h("div", { className: "pmo-onboarding-card__title" }, h("span", null, "2"), h("div", null, h("h3", null, pt("onboarding.accountRepo")), h("p", null, pt("repositories.githubAccess")))),
            !statusState.data ? h(PmState, { loading: statusState.loading, error: statusState.error, retry: statusPair[1] }) :
              !status.configured ? h("div", { className: "pmo-github-unconfigured", role: "status" }, h("span", { className: "pmo-github-mark", "aria-hidden": "true" }, "GH"), h("div", null,
                h("strong", null, pt("repositories.githubUnconfigured")), h("p", null, pt("onboarding.appRequired")),
                h("div", { className: "pmo-github-consent" }, h(Button, { size: "sm", type: "button", disabled: !!busy, onClick: connectGithub }, pt("repositories.connectGithub"))),
                showGithubSetup ? h("div", { className: "pmo-github-setup", role: "note" },
                  h("p", null, "Repository consent will start here once the Datansh GitHub App is configured for this workspace. No GitHub account credentials are requested or stored by Hermes.")) : null)) :
                h(React.Fragment, null,
                  h("div", { className: "pmo-github-consent" }, h("span", { className: "pmo-github-mark", "aria-hidden": "true" }, "GH"), h("div", null, h("strong", null, status.app_slug || pt("repositories.github")), h("p", null, pt("repositories.githubConsent"))), h(Button, { size: "sm", type: "button", disabled: !!busy, onClick: connectGithub }, busy === "consent" ? pt("repositories.saving") : (installations.length ? pt("repositories.addGithub") : pt("repositories.connectGithub")))),
                  githubAccessNotice ? (githubAccessNotice.status === "available" ? h("p", { className: "pmo-github-progress pmo-github-progress--ready", role: "status" }, "GitHub access available: ", githubAccessNotice.installation && githubAccessNotice.installation.installation_account || "selected account", " · ", String(githubAccessNotice.repository_count || 0), " repository", Number(githubAccessNotice.repository_count || 0) === 1 ? "" : "ies", ".") :
                    githubAccessNotice.status === "needs_selection" ? h("p", { className: "pmo-github-progress", role: "status" }, "GitHub found more than one recently updated account. Keep this tab open; choose the account in GitHub, then save its repository selection.") :
                    githubAccessNotice.status === "error" ? h("p", { className: "pmo-state pmo-state--error", role: "alert" }, githubAccessNotice.detail || "GitHub access check failed.") :
                    h("p", { className: "pmo-github-progress", role: "status" }, "Checking GitHub access every 10 seconds. This page will show the account and repository count when access is available.")) : null,
                  installationsState.loading ? h("p", { className: "pmo-github-progress", role: "status" }, pt("repositories.loadingAccounts")) : null,
                  installationsState.error ? h(PmState, { error: installationsState.error, retry: installationsPair[1] }) : null,
                  installations.length ? h("form", { className: "pmo-repository-form", onSubmit: addRepository },
                    h("div", { className: "pmo-onboarding-fields" },
                      h("label", { htmlFor: "pmo-onboarding-installation" }, h("span", null, pt("repositories.installation")), h("select", { id: "pmo-onboarding-installation", value: installationId, disabled: !!busy, onChange: function (event) { setInstallationId(event.target.value); setRepositoryId(""); } }, installations.map(function (item) { return h("option", { key: item.installation_id, value: item.installation_id }, item.installation_account, " · #", item.installation_id); }))),
                      h("label", { htmlFor: "pmo-onboarding-repository" }, h("span", null, pt("repositories.repository")), h("select", { id: "pmo-onboarding-repository", required: true, value: repositoryId, disabled: !!busy || availableState.loading, onChange: function (event) { setRepositoryId(event.target.value); } }, h("option", { value: "", disabled: true }, availableState.loading ? pt("repositories.loadingRepositories") : pt("repositories.chooseRepository")), availableRepositories.map(function (item) { return h("option", { key: item.repository_id, value: item.repository_id }, item.full_name, " · ", item.private ? pt("repositories.private") : pt("repositories.public")); }))),
                      h("label", { htmlFor: "pmo-onboarding-repo-access" }, h("span", null, pt("repositories.access")), h("select", { id: "pmo-onboarding-repo-access", value: access, disabled: !!busy, onChange: function (event) { setAccess(event.target.value); } }, h("option", { value: "write", disabled: !writeAllowed }, pt("repositories.write")), h("option", { value: "read" }, pt("repositories.read")))),
                      !primary ? h("label", { htmlFor: "pmo-onboarding-repo-path" }, h("span", null, pt("onboarding.secondaryPath"), h("small", null, pt("repositories.optional"))), h(Input, { id: "pmo-onboarding-repo-path", value: secondaryPath, disabled: !!busy, placeholder: "../repository", autoComplete: "off", onChange: function (event) { setSecondaryPath(event.target.value); } })) : null),
                    h("div", { className: "pmo-repository-form__footer" },
                      h("label", { className: "pmo-check" }, h(Checkbox, { checked: primary, disabled: !!busy || repositories.some(function (item) { return item.primary; }), onCheckedChange: function (checked) { setPrimary(!!checked); } }), h("span", null, pt("onboarding.primary"))),
                      h(Button, { size: "sm", type: "submit", disabled: !!busy || !selectedInstallation || !selectedRepository || (primary && !writeAllowed) }, busy === "repository" ? pt("repositories.saving") : pt("onboarding.addRepository"))),
                    h("p", { className: "pmo-github-access" }, primary ? pt("onboarding.primaryHint") : pt("onboarding.secondaryPathHint")),
                    availableState.error ? h(PmState, { error: availableState.error, retry: availablePair[1] }) : null) :
                    !installationsState.loading && !installationsState.error ? h("p", { className: "pmo-github-progress" }, pt("repositories.noInstallations")) : null)),
            error ? h("p", { className: "pmo-state pmo-state--error", role: "alert" }, error) : null),
          h("article", { className: "pmo-panel pmo-onboarding-card" },
            h("div", { className: "pmo-onboarding-card__title" }, h("span", null, "3"), h("div", null, h("h3", null, completed ? pt("onboarding.completed") : pt("onboarding.selectedTitle")), h("p", null, pt("onboarding.primaryHint")))),
            visibleRepositories.length ? h("div", { className: "pmo-onboarding-repositories" }, visibleRepositories.map(function (repository) {
              return h("div", { key: repository.name, className: "pmo-onboarding-repository" },
                h("span", { className: "pmo-github-mark", "aria-hidden": "true" }, "GH"),
                h("div", null, h("strong", null, repository.full_name || repository.name), h("span", null, repository.installation_account, " · #", repository.installation_id)),
                h("code", null, repository.primary ? draft.workspace_path : (repository.local_path || "Safe sibling path")),
                repository.primary ? h("span", { className: "pmo-repository-primary" }, pt("onboarding.primary")) : null,
                !completed ? h(Button, { size: "sm", type: "button", disabled: !!busy, onClick: function () { removeRepository(repository.name); } }, pt("onboarding.removeRepository")) : null);
            })) : h("p", { className: "pmo-repository-empty" }, pt("onboarding.selectedEmpty")),
            completed ? h("div", { className: "pmo-onboarding-actions" }, h(Button, { size: "sm", type: "button", onClick: function () { if (props.onComplete) props.onComplete(completed); } }, pt("onboarding.openProject"))) :
              h("div", { className: "pmo-onboarding-actions" }, h(Button, { size: "sm", type: "button", disabled: !canCompleteProject, onClick: completeProject }, busy === "complete" ? pt("onboarding.finishing") : pt("onboarding.finish")))));
  }

  function ProjectContextBar(props) {
    const projects = props.projects || [];
    if (!props.project || projects.length < 2) return null;
    return h("div", { className: "pmo-project-context", role: "region", "aria-label": "Project context" },
      h("span", { className: "pmo-project-context__label" }, "Project context"),
      h("label", { className: "pmo-project-picker", htmlFor: "pmo-global-project-switch" },
        h("span", null, "Project"),
        h("select", { id: "pmo-global-project-switch", value: props.project.project_id, onChange: function (event) {
          const next = projects.find(function (item) { return item.project_id === event.target.value; });
          if (next && props.switchProject) props.switchProject(next);
        } }, projects.map(function (item) { return h("option", { key: item.project_id, value: item.project_id }, item.name); }))),
      h("span", { className: "pmo-project-context__hint" }, "All actions stay within the selected project."));
  }

  function OverviewView(props) {
    const project = props.project;
    const projects = props.projects || [];
    const activityPageSize = 8;
    const activityState = useState({ query: "", filter: "all", offset: 0 });
    const activityControls = activityState[0];
    const setActivityControls = activityState[1];
    const activityParams = "activity_limit=" + activityPageSize + "&activity_offset=" + activityControls.offset + "&activity_filter=" + encodeURIComponent(activityControls.filter) + "&activity_query=" + encodeURIComponent(activityControls.query);
    const url = project ? withBoard(`${API}/projects/${encodeURIComponent(project.project_id)}/overview?${activityParams}`, props.board) : null;
    const pair = usePmResource(url, props.refreshKey);
    const state = pair[0];
    if (!project || !state.data) return h(PmState, { loading: state.loading, error: state.error, retry: pair[1] });
    const data = state.data;
    return h("section", { className: "pmo-view", role: "region", "aria-labelledby": "pmo-overview-heading" },
      h("div", { className: "pmo-heading-row" }, h("div", null,
        h("h2", { id: "pmo-overview-heading", tabIndex: -1 }, data.project.name),
        h("p", { className: "pmo-muted" }, data.project.client || "", data.project.client ? " · " : "", "PM: @", data.project.pm_profile)),
        h("div", { className: "pmo-actions" },
          h("label", { className: "pmo-project-picker" },
            h("span", null, "Project"),
            h("select", { value: project.project_id, "aria-label": "Switch project", onChange: function (event) {
              const next = projects.find(function (item) { return item.project_id === event.target.value; });
              if (next && props.openProject) props.openProject(next);
            } }, projects.map(function (item) { return h("option", { key: item.project_id, value: item.project_id }, item.name); }))),
          h(Button, { size: "sm", onClick: function () { props.navigate("board"); } }, pt("overview.openBoard")),
          h(Button, { size: "sm", onClick: function () { props.navigate("founders"); } }, pt("overview.founders")),
          props.canViewAccess ? h(Button, { size: "sm", onClick: function () { props.navigate("access"); } }, pt("overview.access")) : null)),
      h("div", { className: "pmo-metrics" },
        h(PmMetric, { label: pt("portfolio.progress"), value: data.metrics.progress }),
        h(PmMetric, { label: pt("metrics.flight"), value: data.metrics.in_flight }),
        h(PmMetric, { label: pt("metrics.blocked"), value: data.metrics.blocked }),
        h(PmMetric, { label: pt("metrics.needs"), value: data.metrics.needs_you }),
        h(PmMetric, { label: pt("metrics.spend"), value: formatPmMoney(data.metrics.spend_usd) })),
      h(RepositoryPanel, { project: project, board: props.board, refreshKey: props.refreshKey, announce: props.announce }),
      h("div", { className: "pmo-grid" },
        h("article", { className: "pmo-panel pmo-panel--attention" }, h("h3", null, pt("overview.attention")),
          data.needs_attention.length ? data.needs_attention.map(function (item) { return h("button", { key: item.kind + item.id, className: "pmo-attention", type: "button", title: item.detail || undefined, onClick: function () { props.navigate(item.kind === "approval" ? "approvals" : "board"); } }, h("span", null, "⚠ ", item.title), h("span", { className: "pmo-status" }, item.status)); }) : h("p", { className: "pmo-muted" }, pt("overview.noAttention"))),
        h("article", { className: "pmo-panel" }, h("h3", null, pt("overview.decisions")), data.recent_decisions.length ? data.recent_decisions.map(function (item) { return h("div", { key: item.id, className: "pmo-row" }, h("strong", null, item.title), h("span", null, item.decision)); }) : h(PmState, null)),
        h("article", { className: "pmo-panel" }, h("h3", null, pt("overview.team")), data.team.map(function (item) { return h("div", { key: item.handle, className: "pmo-row" }, h("strong", null, "@", item.handle), h("span", null, item.role)); })),
        h("article", { className: "pmo-panel pmo-activity-panel" },
          h("div", { className: "pmo-activity-panel__heading" }, h("h3", null, pt("overview.activity")), h("span", { className: "pmo-muted" }, data.activity_total || 0)),
          h("div", { className: "pmo-activity-controls" },
            h("input", { type: "search", value: activityControls.query, placeholder: pt("overview.searchActivity"), "aria-label": pt("overview.searchActivity"), onChange: function (event) { setActivityControls({ query: event.target.value, filter: activityControls.filter, offset: 0 }); } }),
            h("select", { value: activityControls.filter, "aria-label": "Filter activity", onChange: function (event) { setActivityControls({ query: activityControls.query, filter: event.target.value, offset: 0 }); } }, (data.activity_filters || ["all"]).map(function (filter) { return h("option", { key: filter, value: filter }, filter === "all" ? pt("overview.allActivity") : filter.charAt(0).toUpperCase() + filter.slice(1)); }))),
          data.activity.length ? data.activity.map(function (item) {
            const ticketHref = item.ticket_href || pmTicketHref(data.project.board_slug || props.board, item.task_id);
            return h("div", { key: item.id, className: "pmo-activity-row", title: absolutePmTime(item.timestamp) },
              h("a", { className: "pmo-activity-row__link", href: ticketHref, onClick: function (event) { pmNavigateToTicket(event, data.project.board_slug || props.board, item.task_id); } }, item.summary),
              h("span", null, "By ", item.actor || item.source || "Delivery workflow", " · ", item.context),
              h("time", { dateTime: new Date(item.timestamp * 1000).toISOString() }, formatPmTime(item.timestamp)));
          }) : h("p", { className: "pmo-muted" }, pt("overview.noActivity")),
          data.activity_total > activityPageSize ? h("div", { className: "pmo-activity-pagination" },
            h("span", { className: "pmo-muted" }, pt("overview.activityPage", data.activity_total ? data.activity_offset + 1 : 0, Math.min(data.activity_offset + data.activity.length, data.activity_total), data.activity_total)),
            h("div", { className: "pmo-actions" },
              h(Button, { size: "sm", disabled: data.activity_offset === 0, onClick: function () { setActivityControls({ query: activityControls.query, filter: activityControls.filter, offset: Math.max(0, data.activity_offset - activityPageSize) }); } }, pt("overview.previous")),
              h(Button, { size: "sm", disabled: data.activity_offset + data.activity.length >= data.activity_total, onClick: function () { setActivityControls({ query: activityControls.query, filter: activityControls.filter, offset: data.activity_offset + activityPageSize }); } }, pt("overview.next")))) : null)));
  }

  // ── Group-chat identity helpers ────────────────────────────────────────
  // Message authors arrive as storage principals: "human:ceo@datansh.local"
  // for people, a bare profile name ("pm-acme") for agents. Rendering those
  // verbatim leaked the storage key into the conversation and made the one
  // non-human participant indistinguishable from the people.

  function pmIdentity(author, roster) {
    const raw = String(author || "").trim();
    const isHuman = /^(?:user:|staff:|dashboard:|human:)/i.test(raw);
    // Some gateway writers prefix the authenticated principal with `user:`
    // (for example `user:human:ceo@example.com`). Peel transport and identity
    // prefixes independently so a human can never acquire an Agent badge.
    const bare = isHuman ? raw.replace(/^(?:user:|staff:|dashboard:)/i, "").replace(/^human:/i, "") : raw;
    let name = bare.includes("@") ? bare.split("@")[0] : bare;
    // Show the handle people actually type, not the storage key. An agent's
    // author field is its Hermes *profile* ("pm-acme") while it is addressed
    // by its project *handle* ("@pm") — displaying the profile meant the name
    // in the transcript never matched the name in the composer.
    const entry = roster && (roster.byProfile[raw] || roster.byPrincipal[raw]);
    if (entry) name = entry.handle;
    return {
      principal: raw,
      isHuman: isHuman,
      handle: entry ? entry.handle : null,
      role: entry ? entry.role : null,
      name: name || raw || "unknown",
      // Two letters is enough to tell six participants apart at 32px.
      initials: (name || "?").replace(/[^a-z0-9]/gi, "").slice(0, 2) || "?",
    };
  }

  // Build profile/principal -> roster-entry lookups once per render pass.
  function pmRosterIndex(items) {
    const byProfile = {};
    const byPrincipal = {};
    (items || []).forEach(function (item) {
      if (item.profile) byProfile[item.profile] = item;
      if (item.principal) byPrincipal[item.principal] = item;
    });
    return { byProfile: byProfile, byPrincipal: byPrincipal };
  }

  // Founder’s Office receives plain task ids in both a PM's reply and its
  // tool stream.  Keep those references local to the PMO shell so opening a
  // task never leaks a board slug into another project or a new browser tab.
  const PM_TICKET_REFERENCE_RE = /\bt_[a-z0-9][a-z0-9_-]*\b/gi;

  function pmTicketHref(boardSlug, taskId) {
    const board = String(boardSlug || "").trim();
    const ticket = String(taskId || "").trim();
    if (!board || !ticket) return "#pmo/board";
    return "#pmo/board?board=" + encodeURIComponent(board) + "&task=" + encodeURIComponent(ticket);
  }

  function pmTicketIds(value) {
    const result = [];
    const seen = new Set();
    const source = String(value || "");
    PM_TICKET_REFERENCE_RE.lastIndex = 0;
    let match;
    while ((match = PM_TICKET_REFERENCE_RE.exec(source)) !== null) {
      const ticket = match[0];
      if (!seen.has(ticket)) { seen.add(ticket); result.push(ticket); }
    }
    return result;
  }

  function pmLinkedMarkdown(source, boardSlug) {
    const text = String(source || "");
    if (!boardSlug) return text;
    PM_TICKET_REFERENCE_RE.lastIndex = 0;
    return text.replace(PM_TICKET_REFERENCE_RE, function (ticket, offset, fullText) {
      // Do not disturb an author-supplied Markdown link or an explicit URL.
      const before = fullText.slice(Math.max(0, offset - 2), offset);
      if (before.indexOf("[") >= 0 || before.indexOf("(") >= 0 || before.indexOf("/") >= 0) return ticket;
      return "[" + ticket + "](" + pmTicketHref(boardSlug, ticket) + ")";
    });
  }

  const PM_DETAIL_REQUEST_MARKER = "[pmo:founder-detail-request:v1]";
  const PM_DETAIL_RESPONSE_MARKER = "[pmo:founder-detail-response:v1]";
  const PM_TICKET_CONFIRMATION_MARKER = "[pmo:founder-ticket-confirmation:v1]";
  const PM_TICKET_DECISION_MARKER = "[pmo:founder-ticket-decision:v1]";
  const PM_PLAN_TICKET_MARKER = "[pmo:founder-plan-ticket:v1]";
  const PM_CONSULT_MARKER = "[pmo:founder-consult:v1]";
  const PM_CONSULT_RESPONSE_MARKER = "[pmo:founder-consult-response:v1]";

  function pmWorkflowEnvelope(source) {
    const text = String(source || "");
    const lines = text.split(/\r?\n/);
    if (lines.length < 2 || !/^\[pmo:founder-[a-z0-9-]+:v\d+\]$/i.test(lines[0].trim())) return null;
    let data;
    try { data = JSON.parse(lines[1]); } catch (_error) { return null; }
    if (!data || typeof data !== "object" || Array.isArray(data)) return null;
    return {
      marker: lines[0].trim(),
      data: data,
      prose: lines.slice(2).join("\n").trim(),
    };
  }

  function pmWorkflowVisibleBody(source) {
    const envelope = pmWorkflowEnvelope(source);
    return envelope ? envelope.prose : String(source || "");
  }

  function pmWorkflowResponses(messages) {
    const details = new Map();
    const confirmations = new Set();
    (messages || []).forEach(function (message) {
      const envelope = pmWorkflowEnvelope(message && message.body);
      if (!envelope) return;
      if (envelope.marker === PM_DETAIL_RESPONSE_MARKER && envelope.data.request_id) {
        // Keep the durable response payload, not only a boolean. Reopened
        // detail cards must show what the founder submitted after a refresh.
        details.set(String(envelope.data.request_id), Object.assign({}, envelope.data, {
          author: message.author || "", message_id: message.id || "",
        }));
      }
      if (envelope.marker === PM_TICKET_DECISION_MARKER && envelope.data.confirmation_id) confirmations.add(String(envelope.data.confirmation_id));
    });
    return { details: details, confirmations: confirmations };
  }

  function pmWorkflowSubmittedAnswer(response, question) {
    const answers = response && Array.isArray(response.answers) ? response.answers : [];
    const saved = answers.find(function (answer) {
      return String(answer && answer.question_id || "") === String(question && question.id || "");
    });
    if (!saved) return null;
    const options = question && Array.isArray(question.options) ? question.options : [];
    const choiceId = String(saved.choice_id || "");
    if (choiceId === "__other__") return { choiceId: choiceId, answer: String(saved.answer || "") };
    if (options.some(function (option) { return String(option.id || "") === choiceId; })) {
      return { choiceId: choiceId, answer: String(saved.answer || "") };
    }
    const matchingOption = options.find(function (option) {
      return String(option.label || "") === String(saved.answer || "");
    });
    return matchingOption
      ? { choiceId: String(matchingOption.id || ""), answer: String(saved.answer || "") }
      : { choiceId: "__other__", answer: String(saved.answer || "") };
  }

  function FounderWorkflowCard(props) {
    const envelope = pmWorkflowEnvelope(props.message && props.message.body);
    const [answers, setAnswers] = useState({});
    const [otherText, setOtherText] = useState({});
    const [decision, setDecision] = useState("");
    const [feedback, setFeedback] = useState("");
    const [submitted, setSubmitted] = useState(false);
    if (!envelope || (envelope.marker !== PM_DETAIL_REQUEST_MARKER && envelope.marker !== PM_TICKET_CONFIRMATION_MARKER)) return null;
    const planId = String(envelope.data.plan_id || "");
    const messageId = String((props.message && props.message.id) || "");
    const locked = Boolean(props.busy || props.resolved || submitted);

    if (envelope.marker === PM_DETAIL_REQUEST_MARKER) {
      const questions = Array.isArray(envelope.data.questions) ? envelope.data.questions.slice(0, 3) : [];
      const submittedAnswer = function (question) { return pmWorkflowSubmittedAnswer(props.response, question); };
      const valid = questions.length > 0 && questions.every(function (question) {
        const stored = submittedAnswer(question);
        const selected = stored ? stored.choiceId : answers[question.id];
        return Boolean(selected) && (selected !== "__other__" || String(otherText[question.id] || "").trim());
      });
      const submit = function (event) {
        event.preventDefault();
        if (!valid || locked) return;
        const payload = questions.map(function (question) {
          const selected = answers[question.id];
          const option = (question.options || []).find(function (item) { return item.id === selected; });
          return {
            question_id: question.id,
            choice_id: selected,
            answer: selected === "__other__" ? String(otherText[question.id] || "").trim() : String(option && option.label || selected),
          };
        });
        const body = PM_DETAIL_RESPONSE_MARKER + "\n" + JSON.stringify({
          schema: "datansh-pmo-founder-detail-response.v1",
          plan_id: planId,
          request_id: messageId,
          answers: payload,
        }) + "\n\n@pm I submitted the requested ticket details. Please draft the contextual ticket for my approval.";
        Promise.resolve(props.send(body)).then(function (result) { if (result !== false) setSubmitted(true); });
      };
      return h("form", { className: "pmo-workflow-card", onSubmit: submit },
        h("header", { className: "pmo-workflow-card__head" },
          h("span", { "aria-hidden": "true" }, "?"),
          h("div", null, h("strong", null, "Ticket details"), h("p", null, "Choose one answer per question. Select Other to enter a custom response."))),
        questions.map(function (question, questionIndex) {
          const options = (question.options || []).concat([{ id: "__other__", label: "Other", description: "Enter a different answer." }]);
          const stored = submittedAnswer(question);
          const selected = stored ? stored.choiceId : answers[question.id];
          const customAnswer = stored ? stored.answer : otherText[question.id] || "";
          return h("fieldset", { key: question.id || questionIndex, disabled: locked },
            h("legend", null, (questionIndex + 1) + ". " + String(question.question || "")),
            h("div", { className: "pmo-workflow-options" }, options.map(function (option) {
              const checked = selected === option.id;
              return h("label", { key: option.id, className: "pmo-workflow-option" + (checked ? " is-selected" : "") },
                h("input", { type: "radio", name: "workflow-" + messageId + "-" + question.id, value: option.id, checked: checked,
                  onChange: function () { setAnswers(Object.assign({}, answers, { [question.id]: option.id })); } }),
                h("span", null, h("strong", null, option.label), h("small", null, option.description || "")));
            })),
            selected === "__other__" ? h("textarea", { className: "pmo-workflow-other", rows: 2,
              placeholder: "Type your answer", value: customAnswer,
              onChange: function (event) { setOtherText(Object.assign({}, otherText, { [question.id]: event.target.value })); } }) : null);
        }),
        h("footer", null,
          props.resolved || submitted ? h("span", { className: "pmo-workflow-done", role: "status" }, "Details submitted") : h("span", null, "The PM will use these answers in the proposal."),
          h("button", { type: "submit", className: "pmo-workflow-primary", disabled: !valid || locked }, submitted || props.resolved ? "Submitted" : "Send details to @pm")));
    }

    const proposal = envelope.data.proposal || {};
    const humanOwned = /^(human:|dashboard:)/i.test(String(proposal.assignee || ""));
    const needsFeedback = decision === "changes" || decision === "other";
    const canSubmit = decision && (!needsFeedback || feedback.trim());
    const submitDecision = function (event) {
      event.preventDefault();
      if (!canSubmit || locked) return;
      const body = PM_TICKET_DECISION_MARKER + "\n" + JSON.stringify({
        schema: "datansh-pmo-founder-ticket-decision.v1",
        plan_id: planId,
        confirmation_id: messageId,
        decision: decision === "approve" ? "approved" : decision,
        feedback: feedback.trim(),
      }) + "\n\n@pm " + (decision === "approve"
        ? "I approve this ticket. Create it with the proposed owner and attached context."
        : "I am not approving this proposal yet. Revise it using my feedback and ask for confirmation again.");
      Promise.resolve(props.send(body)).then(function (result) { if (result !== false) setSubmitted(true); });
    };
    return h("form", { className: "pmo-workflow-card pmo-workflow-card--approval", onSubmit: submitDecision },
      h("header", { className: "pmo-workflow-card__head" }, h("span", { "aria-hidden": "true" }, "✓"),
        h("div", null, h("strong", null, "Approve ticket creation"), h("p", null, "Nothing is added to the board until you approve this exact proposal."))),
      h("div", { className: "pmo-ticket-proposal" },
        h("div", null, h("small", null, "Ticket"), h("strong", null, proposal.title || "Untitled ticket")),
        h("div", null, h("small", null, "Owner"), h("strong", null,
          humanOwned ? String(proposal.assignee || "Human collaborator").replace(/^(human:|dashboard:)/i, "") : "@" + String(proposal.assignee || "unassigned"))),
        h("p", null, proposal.outcome || ""),
        h("details", null, h("summary", null, "Review delivery context and criteria"),
          h("p", null, proposal.context_summary || "No context summary supplied."),
          h("ul", null, (proposal.acceptance_criteria || []).map(function (item, index) { return h("li", { key: index }, item); })),
          humanOwned ? h("div", { className: "pmo-human-handoff" }, h("strong", null, "Human hand-off steps"),
            h("ol", null, (proposal.human_steps || []).map(function (item, index) { return h("li", { key: index }, item); }))) : null)),
      h("fieldset", { disabled: locked }, h("legend", null, "Your decision"),
        h("div", { className: "pmo-workflow-options pmo-workflow-options--decision" }, [
          { id: "approve", label: "Approve ticket", description: "Create and assign this exact ticket." },
          { id: "changes", label: "Request changes", description: "Ask the PM to revise the proposal." },
          { id: "other", label: "Other", description: "Give another instruction." },
        ].map(function (option) { return h("label", { key: option.id, className: "pmo-workflow-option" + (decision === option.id ? " is-selected" : "") },
          h("input", { type: "radio", name: "decision-" + messageId, value: option.id, checked: decision === option.id, onChange: function () { setDecision(option.id); } }),
          h("span", null, h("strong", null, option.label), h("small", null, option.description))); })),
        needsFeedback ? h("textarea", { className: "pmo-workflow-other", rows: 3, placeholder: "Tell the PM what to change", value: feedback,
          onChange: function (event) { setFeedback(event.target.value); } }) : null),
      h("footer", null,
        props.resolved || submitted ? h("span", { className: "pmo-workflow-done", role: "status" }, "Decision sent") : h("span", null, "Approval is recorded in this project conversation."),
        h("button", { type: "submit", className: "pmo-workflow-primary", disabled: !canSubmit || locked }, submitted || props.resolved ? "Sent" : "Send decision to @pm")));
  }

  // Two hash forms exist: the bare "#pmo/<view>" that navigate() writes, and
  // the "#pmo/board?board=<slug>&task=<id>" that pmTicketHref puts on ticket
  // references rendered inside Markdown. Splitting the query string off is not
  // cosmetic — matching the whole string against the view list fails for the
  // second form, so a ticket link landed on Portfolio instead of the ticket.
  function pmParseHash() {
    const raw = String(window.location.hash || "").replace(/^#pmo\//, "");
    if (!raw) return null;
    const cut = raw.indexOf("?");
    const params = new URLSearchParams(cut >= 0 ? raw.slice(cut + 1) : "");
    return {
      view: cut >= 0 ? raw.slice(0, cut) : raw,
      board: params.get("board") || "",
      task: params.get("task") || "",
    };
  }

  function pmNavigateToTicket(event, boardSlug, taskId) {
    if (event) event.preventDefault();
    if (boardSlug) writeSelectedBoard(boardSlug);
    try { window.localStorage.setItem("pmo:open-task", String(taskId || "")); } catch (_error) { /* best effort */ }
    window.dispatchEvent(new CustomEvent("pmo:board-selected", { detail: { board: boardSlug } }));
    window.dispatchEvent(new CustomEvent("pmo:navigate", { detail: { view: "board" } }));
  }

  // Normalise both the portfolio-wide folders API and the older per-project
  // conversation response into one UI model. This keeps Founder's Office
  // usable during rolling upgrades where the dashboard and API processes may
  // briefly be on different versions.
  function pmConversationFolders(payload, projects) {
    const source = payload || {};
    const knownProjects = Array.isArray(projects) ? projects : [];
    const sourceFolders = Array.isArray(source.folders)
      ? source.folders
      : (Array.isArray(source.projects) ? source.projects : []);
    const byProject = {};
    sourceFolders.forEach(function (folder) {
      const projectId = String(folder.project_id || folder.id || "");
      if (projectId) byProject[projectId] = folder;
    });
    const projectFolders = [];
    const seen = new Set();
    knownProjects.concat(sourceFolders).forEach(function (project) {
      const projectId = String(project.project_id || project.id || "");
      if (!projectId || seen.has(projectId)) return;
      seen.add(projectId);
      const folder = byProject[projectId] || project;
      const rows = Array.isArray(folder.conversations) ? folder.conversations.filter(function (item) {
        return item && item.kind !== "global_founders_office" && item.scope !== "global" && !item.is_global;
      }) : [];
      projectFolders.push({
        key: "project:" + projectId,
        scope: "project",
        project_id: projectId,
        name: folder.name || folder.project_name || project.name || project.slug || projectId,
        slug: folder.slug || project.slug || projectId,
        board_slug: folder.board_slug || project.board_slug || "",
        pm_profile: folder.pm_profile || project.pm_profile || "",
        founder_participants: Array.isArray(folder.founder_participants) ? folder.founder_participants : [],
        client_participants: Array.isArray(folder.client_participants) ? folder.client_participants : [],
        conversations: rows.map(function (item) {
          return Object.assign({}, item, {
            project_id: item.project_id || projectId,
            project_name: item.project_name || folder.name || project.name || projectId,
            board_slug: item.board_slug || folder.board_slug || project.board_slug || "",
            scope: "project",
            participants: Array.isArray(item.participants) ? item.participants
              : (item.kind === "client" ? (folder.client_participants || []) : (folder.founder_participants || [])),
          });
        }),
      });
    });
    let embeddedGlobal = null;
    sourceFolders.some(function (folder) {
      return (folder.conversations || []).some(function (item) {
        if (item && (item.kind === "global_founders_office" || item.scope === "global" || item.is_global)) {
          embeddedGlobal = item; return true;
        }
        return false;
      });
    });
    const globalValue = source.global && source.global.conversation
      ? source.global.conversation
      : (source.global || source.global_conversation || embeddedGlobal || null);
    const globalRows = source.global && Array.isArray(source.global.conversations)
      ? source.global.conversations
      : (globalValue && globalValue.thread_id ? [globalValue] : []);
    const globalParticipants = source.global && Array.isArray(source.global.participants)
      ? source.global.participants : [];
    const globalConversations = globalRows.map(function (item) {
      return Object.assign({}, item, {
          title: item.title || pt("chat.global"),
          kind: item.kind || "global_founders_office",
          scope: "global",
          is_global: true,
          participants: Array.isArray(item.participants) ? item.participants : globalParticipants,
        });
    });
    return [{
      key: "global", scope: "global", name: pt("chat.global"),
      participants: globalParticipants,
      conversations: globalConversations,
    }].concat(projectFolders);
  }

  function pmConversationKey(conversation) {
    if (!conversation) return "";
    return [conversation.scope || conversation.kind || "project", conversation.board_slug || "", conversation.thread_id || ""].join(":");
  }

  function pmConversationByKey(folders, key) {
    for (const folder of folders || []) {
      for (const conversation of folder.conversations || []) {
        if (pmConversationKey(conversation) === key) return conversation;
      }
    }
    return null;
  }

  const PM_ACTIVITY_KINDS = new Set([
    "thinking", "reasoning", "working", "tool", "tool_call", "tool_result",
    "status", "agent_status", "agent_working",
  ]);

  function pmActivityRecord(value) {
    if (!value || typeof value !== "object") return null;
    const rawType = String(value.activity_type || value.event_type || value.type || value.kind || "").toLowerCase();
    if (!PM_ACTIVITY_KINDS.has(rawType) && !value.tool_name && !value.tool_call_id) return null;
    let type = rawType;
    if (value.tool_name || value.tool_call_id || type === "tool" || type === "tool_result") type = "tool_call";
    else if (type === "reasoning") type = "thinking";
    else if (type === "agent_status") type = "status";
    else if (type === "agent_working") type = "working";
    let detail = value.details != null ? value.details : (value.summary != null ? value.summary : value.body);
    if (detail && typeof detail === "object") {
      try { detail = JSON.stringify(detail, null, 2); } catch (_error) { detail = String(detail); }
    }
    return {
      id: value.id || value.event_id || value.tool_call_id || null,
      type: type || "status",
      status: String(value.status || (type === "thinking" || type === "working" ? "active" : "")),
      toolName: String(value.tool_name || value.name || ""),
      detail: String(detail || ""),
      result: value.result != null ? (typeof value.result === "string" ? value.result : JSON.stringify(value.result)) : "",
      created_at: Number(value.created_at || value.timestamp || 0),
      author: value.author || value.profile || value.agent || "agent",
      sourceMessageId: String(value.source_message_id || value.message_id || ""),
    };
  }

  function pmPreviewKind(source, toolName) {
    const explicitTool = String(toolName || "").toLowerCase();
    const sourceText = String(source || "").toLowerCase();
    const nonMutatingFounderTool = "pmo_(?:context|repo_[a-z0-9_]*|plan|consult|request_[a-z0-9_]*|handles|ask(?:_[a-z0-9_]*)?)";
    if (new RegExp("^" + nonMutatingFounderTool + "$").test(explicitTool)) return null;
    // Runtime activity records sometimes expose the generic name `tool_call`
    // and put the actual PMO tool in the JSON detail. Do not infer ticket
    // mutations from IDs or words such as "ticket" in those tool arguments.
    if (new RegExp('"name"\\s*:\\s*"' + nonMutatingFounderTool + '"').test(sourceText)) return null;
    const value = (String(toolName || "") + "\n" + String(source || "")).toLowerCase();
    if (/(?:^|[^a-z])skill(?:_|\b)/.test(value)) {
      if (/(patch|write|update|create|install|configure)/.test(value)) return { kind: "skill", label: "Skill updated" };
      return null;
    }
    if (/(ticket|kanban|task\.py|\/tasks\/)/.test(value)) {
      if (/ticket\.py\s+--help/.test(value)) return null;
      if (/(create|new ticket|--title)/.test(value)) return { kind: "ticket", label: "Ticket created" };
      if (/(assign|reassign)/.test(value)) return { kind: "ticket", label: "Ticket assigned" };
      if (/(complete|finali[sz]e|close)/.test(value)) return { kind: "ticket", label: "Ticket completed" };
      return { kind: "ticket", label: "Ticket updated" };
    }
    return null;
  }

  function pmActivityPreviews(activity) {
    if (!activity || activity.type !== "tool_call") return [];
    const source = [activity && activity.toolName, activity && activity.detail, activity && activity.result].filter(Boolean).join("\n");
    const preview = pmPreviewKind(source, activity && activity.toolName);
    if (!preview) return [];
    const ids = pmTicketIds(source);
    const resultTaskMatch = /["']task_id["']\s*:\s*["'](t_[a-z0-9]+)["']/i.exec(source);
    const previewIds = resultTaskMatch ? [resultTaskMatch[1]] : ids;
    const nameMatch = /--title\s+(?:"([^"]+)"|'([^']+)'|([^\n]+))/i.exec(source);
    if (preview.kind === "ticket" && !previewIds.length && !nameMatch) return [];
    return [Object.assign({}, preview, {
      id: String(activity && activity.id || source),
      tickets: previewIds,
      detail: preview.kind === "skill" ? String(activity && activity.toolName || "Hermes skill") : (nameMatch ? (nameMatch[1] || nameMatch[2] || nameMatch[3]).trim() : "Kanban task"),
    })];
  }

  function pmMessagePreviews(message) {
    const envelope = pmWorkflowEnvelope(message && message.body || "");
    // Narrative plans, approval proposals, and summaries commonly mention an
    // existing ticket using words such as "created", "new", or "assigned".
    // Treat only the durable ticket marker as a message-level mutation event;
    // tool-call previews cover actual tool mutations separately.
    if (!envelope || envelope.marker !== PM_PLAN_TICKET_MARKER) return [];
    const taskId = String(envelope.data && envelope.data.task_id || "").trim();
    if (!/^t_[a-z0-9]+$/i.test(taskId)) return [];
    return [{
      id: String(message && message.id || taskId), kind: "ticket",
      label: "Ticket created", tickets: [taskId], detail: "Project Kanban",
    }];
  }

  function PmActionPreviews(props) {
    const previews = props.previews || [];
    if (!previews.length) return null;
    return h("div", { className: "pmo-action-previews", "aria-label": "Agent changes" }, previews.map(function (preview, index) {
      return h("div", { key: preview.id + ":" + index, className: "pmo-action-preview pmo-action-preview--" + preview.kind },
        h("span", { className: "pmo-action-preview__label" }, preview.kind === "skill" ? "✦ " : "↗ ", preview.label),
        preview.detail ? h("span", { className: "pmo-action-preview__detail" }, preview.detail) : null,
        (preview.tickets || []).map(function (ticket) {
          return h("a", { key: ticket, href: pmTicketHref(props.board, ticket), className: "pmo-ticket-link",
            onClick: function (event) { pmNavigateToTicket(event, props.board, ticket); } }, ticket);
        }));
    }));
  }

  function pmChatStream(messages, detail) {
    const result = [];
    const seen = new Set();
    const subchats = detail && Array.isArray(detail.subchats) ? detail.subchats : [];
    const nestedBySubchat = {};
    const sourceToSubchat = {};
    const normalizedActivities = [];
    const normalizeActivity = function (value, suffix) {
      const activity = pmActivityRecord(value);
      if (!activity) return false;
      const key = String(activity.id || [activity.type, activity.created_at, activity.toolName, activity.detail, suffix || ""].join(":"));
      if (seen.has(key)) return true;
      seen.add(key);
      normalizedActivities.push({ key: key, record: activity });
      return true;
    };
    (messages || []).forEach(function (message, index) {
      if (!normalizeActivity(message, "message-" + index)) {
        const envelope = pmWorkflowEnvelope(message && message.body || "");
        if (!envelope || (envelope.marker !== PM_CONSULT_MARKER && envelope.marker !== PM_CONSULT_RESPONSE_MARKER)) {
          result.push({ streamKind: "message", key: "message:" + (message.id || index), record: message });
        }
      }
      (Array.isArray(message.activities) ? message.activities : (Array.isArray(message.events) ? message.events : [])).forEach(function (activity, activityIndex) {
        normalizeActivity(activity, "nested-" + index + "-" + activityIndex);
      });
    });
    const activityBlock = detail && detail.agent_activity;
    const activityRows = detail && (detail.activities || detail.events)
      || (activityBlock && Array.isArray(activityBlock.events) ? activityBlock.events : activityBlock);
    (Array.isArray(activityRows) ? activityRows : []).forEach(function (activity, index) {
      normalizeActivity(activity, "detail-" + index);
    });
    const parseJsonObject = function (value) {
      if (!value || typeof value !== "string") return null;
      try { const parsed = JSON.parse(value); return parsed && typeof parsed === "object" ? parsed : null; }
      catch (_error) { return null; }
    };
    const consultationForActivity = function (activity) {
      if (!activity || activity.type !== "tool_call") return null;
      const detailObject = parseJsonObject(activity.detail) || {};
      const resultObject = parseJsonObject(activity.result) || {};
      const toolName = String(
        activity.toolName === "tool_call" && detailObject.name
          ? detailObject.name : activity.toolName || detailObject.name || ""
      ).toLowerCase();
      if (toolName !== "pmo_consult" && toolName !== "pmo_ask") return null;
      const args = detailObject.arguments && typeof detailObject.arguments === "object"
        ? detailObject.arguments : detailObject;
      const threadId = String(resultObject.thread_id || "");
      const planId = String(args.plan_id || args.task_id || resultObject.plan_id || "");
      const handles = [args.handle].concat(Array.isArray(args.handles) ? args.handles : [])
        .filter(Boolean).map(function (value) { return String(value).replace(/^@/, "").toLowerCase(); });
      const matched = subchats.filter(function (subchat) {
        if (threadId && String(subchat.thread_id || subchat.id || "").endsWith(threadId)) return true;
        return planId && String(subchat.plan_id || "") === planId
          && handles.indexOf(String(subchat.handle || "").replace(/^@/, "").toLowerCase()) >= 0;
      });
      if (matched.length) return matched[matched.length - 1];
      // ``pmo_ask`` permits an omitted task id. A focused one-agent follow-up
      // still belongs to that agent's child run when the handle resolves to a
      // single consultation in this parent conversation.
      const byHandle = subchats.filter(function (subchat) {
        return handles.indexOf(String(subchat.handle || "").replace(/^@/, "").toLowerCase()) >= 0;
      });
      return byHandle.length === 1 ? byHandle[0] : null;
    };
    normalizedActivities.forEach(function (item) {
      const subchat = consultationForActivity(item.record);
      if (!subchat) return;
      const key = String(subchat.id || subchat.thread_id);
      if (!nestedBySubchat[key]) nestedBySubchat[key] = [];
      nestedBySubchat[key].push(item.record);
      if (item.record.sourceMessageId) sourceToSubchat[item.record.sourceMessageId] = key;
      item.subchatKey = key;
      item.nested = true;
    });
    // Hermes may describe the delegation tool in one assistant step and invoke
    // it in the next. Keep that preparation (and same-message reasoning) inside
    // the specialist card instead of leaking it into the parent conversation.
    normalizedActivities.forEach(function (item, index) {
      if (item.nested || item.record.type !== "tool_call") return;
      const detailObject = parseJsonObject(item.record.detail) || {};
      const describedTool = String(detailObject.name || "").toLowerCase();
      if (String(item.record.toolName || "").toLowerCase() !== "tool_describe"
          || (describedTool !== "pmo_consult" && describedTool !== "pmo_ask")) return;
      const nextConsultation = normalizedActivities.slice(index + 1).find(function (candidate) {
        return Boolean(candidate.nested && candidate.subchatKey);
      });
      if (!nextConsultation) return;
      const key = nextConsultation.subchatKey;
      if (!nestedBySubchat[key]) nestedBySubchat[key] = [];
      nestedBySubchat[key].push(item.record);
      if (item.record.sourceMessageId) sourceToSubchat[item.record.sourceMessageId] = key;
      item.subchatKey = key;
      item.nested = true;
    });
    // Reasoning produced in the same assistant message as a consultation tool
    // belongs to the nested run. Keeping the source message id in the API is
    // what makes this deterministic rather than a timestamp heuristic.
    normalizedActivities.forEach(function (item) {
      const key = item.record.sourceMessageId && sourceToSubchat[item.record.sourceMessageId];
      if (!item.nested && key) {
        if (!nestedBySubchat[key]) nestedBySubchat[key] = [];
        nestedBySubchat[key].push(item.record);
        item.nested = true;
      }
      if (!item.nested) result.push({ streamKind: "activity", key: "activity:" + item.key, record: item.record });
    });
    subchats.forEach(function (subchat, index) {
      const key = String(subchat.id || subchat.thread_id || index);
      result.push({
        streamKind: "subchat", key: "subchat:" + key,
        record: Object.assign({}, subchat, { delegation_activity: nestedBySubchat[key] || [] }),
      });
    });
    // The native PMO API returns a session summary object with its live event
    // stream nested under `events`. Keep the events in chronological order and
    // add one compact status row so a user can see that the PM is working even
    // before its first reasoning/tool message has been committed.
    if (activityBlock && !Array.isArray(activityBlock) && activityBlock.state && activityBlock.state !== "idle") {
      const summaryActivity = pmActivityRecord({
        id: "session:" + (activityBlock.session_id || "current"),
        type: activityBlock.state === "working" ? "working" : "status",
        status: activityBlock.state,
        summary: [activityBlock.profile, activityBlock.api_calls ? activityBlock.api_calls + " model calls" : "", activityBlock.tool_calls ? activityBlock.tool_calls + " tool calls" : ""].filter(Boolean).join(" · "),
        timestamp: activityBlock.last_activity || 0,
        profile: activityBlock.profile || "agent",
      });
      if (summaryActivity) result.push({
        streamKind: "activity", key: "activity:session:" + (activityBlock.session_id || "current"), record: summaryActivity,
      });
    }
    return result.sort(function (left, right) {
      return (Number(left.record.created_at) || 0) - (Number(right.record.created_at) || 0);
    });
  }

  // Deterministic hue per principal so a speaker keeps the same colour across
  // reloads and across threads. Not stored anywhere — derived from the string.
  function pmHue(value) {
    let hash = 0;
    const text = String(value || "");
    for (let i = 0; i < text.length; i += 1) {
      hash = (hash * 31 + text.charCodeAt(i)) % 360;
    }
    return hash;
  }

  function pmAvatarStyle(identity) {
    const hue = pmHue(identity.principal);
    return {
      "--pmo-avatar-bg": `hsl(${hue} 55% 32% / .55)`,
      "--pmo-avatar-fg": `hsl(${hue} 85% 82%)`,
      "--pmo-avatar-bd": `hsl(${hue} 55% 55% / .55)`,
    };
  }

  // Split a body into text and @mention tokens. Deliberately builds React
  // children rather than markup: text children are escaped, so this cannot
  // become an injection vector (see tests/plugins/test_pmo_security_hardening).
  const PM_MENTION_RE = /(?:^|(?<=\s))@([a-z0-9][a-z0-9._-]*)/gi;

  function pmRenderBody(body) {
    const text = String(body == null ? "" : body);
    const nodes = [];
    let last = 0;
    let match;
    PM_MENTION_RE.lastIndex = 0;
    while ((match = PM_MENTION_RE.exec(text)) !== null) {
      if (match.index > last) nodes.push(text.slice(last, match.index));
      nodes.push(h("span", { key: `m${match.index}`, className: "pmo-mention" }, match[0]));
      last = match.index + match[0].length;
    }
    if (last < text.length) nodes.push(text.slice(last));
    return nodes.length ? nodes : [text];
  }

  function pmDayLabel(seconds) {
    const date = new Date((Number(seconds) || 0) * 1000);
    if (Number.isNaN(date.getTime())) return "";
    const today = new Date();
    const sameDay = function (a, b) {
      return a.getFullYear() === b.getFullYear()
        && a.getMonth() === b.getMonth()
        && a.getDate() === b.getDate();
    };
    if (sameDay(date, today)) return "Today";
    const yesterday = new Date(today.getTime() - 86400000);
    if (sameDay(date, yesterday)) return "Yesterday";
    try {
      return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    } catch (error) {
      return date.toDateString();
    }
  }

  // Consecutive turns by one author inside this window share a header.
  const PM_GROUP_WINDOW_SECONDS = 300;

  function pmAllowedChatParticipant(item, clientScope) {
    if (!item || !item.handle) return false;
    const handle = String(item.handle).replace(/^@/, "").toLowerCase();
    const base = handle.split(/[._-]/, 1)[0];
    if (item.kind === "agent") return true;
    if (base === "ceo" || base === "cfo") return true;
    return Boolean(clientScope && (base === "client" || base === "customer"));
  }

  function pmMentionRoster(responses, folders, globalScope) {
    const byKey = {};
    const add = function (item) {
      if (!item || !item.handle) return;
      const clean = Object.assign({}, item, { handle: String(item.handle).replace(/^@/, "") });
      // A participant can arrive from both the project's built-in roster and
      // the live autocomplete endpoint.  The handle is the addressable chat
      // identity, so use it as the dedupe boundary instead of leaking two
      // visually identical @pm pills when their backing metadata differs.
      const key = [clean.kind || "human", clean.handle.toLowerCase()].join(":");
      if (!byKey[key]) byKey[key] = clean;
    };
    (responses || []).forEach(function (response) {
      (response && Array.isArray(response.items) ? response.items : []).forEach(add);
    });
    if (globalScope) {
      add({ handle: "pm", kind: "agent", role: pt("chat.allManagers"), profile: "pm" });
      (folders || []).filter(function (folder) { return folder.scope === "project"; }).forEach(function (folder) {
        if (folder.pm_profile) add({
          handle: folder.pm_profile, kind: "agent", profile: folder.pm_profile,
          role: "Project manager · " + folder.name, project_id: folder.project_id,
          mentionable: true,
        });
      });
    }
    return Object.keys(byKey).map(function (key) { return byKey[key]; }).sort(function (left, right) {
      if ((left.kind === "agent") !== (right.kind === "agent")) return left.kind === "agent" ? -1 : 1;
      return left.handle.localeCompare(right.handle);
    });
  }

  function AgentActivity(props) {
    const activity = props.activity;
    const isThinking = activity.type === "thinking" && Boolean(activity.detail);
    // Reasoning is useful audit context, but it should not make a Founder’s
    // Office transcript feel like a terminal. Start collapsed, like a compact
    // Claude-style thinking row, and let the reader reveal it on demand.
    const [thinkingOpen, setThinkingOpen] = useState(false);
    const labels = {
      thinking: pt("chat.thinking"), working: pt("chat.working"),
      tool_call: pt("chat.toolCall"), status: pt("chat.status"),
    };
    const label = labels[activity.type] || pt("chat.status");
    // "working" is the normal live-session state emitted by the PM gateway.
    // Treat it as active rather than leaving a completed-looking transcript row.
    const active = ["active", "working", "running", "started", "in_progress", "pending", "processing"].indexOf(activity.status) >= 0;
    return h("article", {
      className: "pmo-agent-activity pmo-agent-activity--" + activity.type + (active ? " is-live" : ""),
      role: "status", "aria-live": active ? "polite" : "off", "aria-label": label + (activity.status ? ": " + activity.status : ""),
    },
      h("span", { className: "pmo-agent-activity__icon", "aria-hidden": "true" },
        activity.type === "tool_call" ? "⌘" : (active ? "●" : "○")),
      h("div", { className: "pmo-agent-activity__main" },
        h("div", { className: "pmo-agent-activity__head" },
          h("strong", null, label),
          active ? h("span", { className: "pmo-agent-activity__live", "aria-label": "Session is active" },
            h("i", null), h("i", null), h("i", null), h("span", null, "Live")) : null,
          activity.toolName ? h("code", null, activity.toolName) : null,
          activity.status ? h("span", { className: "pmo-agent-status pmo-agent-status--" + activity.status }, activity.status.replace(/_/g, " ")) : null,
          isThinking ? h("button", { type: "button", className: "pmo-thinking-toggle", "aria-expanded": thinkingOpen,
            onClick: function () { setThinkingOpen(!thinkingOpen); } }, thinkingOpen ? "Hide thinking" : "Show thinking") : null),
        h(PmActionPreviews, { previews: pmActivityPreviews(activity), board: props.board }),
        activity.detail && (!isThinking || thinkingOpen) ? h("pre", { className: "pmo-agent-activity__detail" }, activity.detail) : null));
  }

  function SpecialistSubChat(props) {
    const subchat = props.subchat || {};
    const open = Boolean(props.open);
    const handle = String(subchat.handle || subchat.profile || "specialist").replace(/^@/, "");
    const state = String(subchat.status || "queued").toLowerCase();
    const activityBlock = subchat.agent_activity || {};
    const nested = (subchat.delegation_activity || []).concat(
      Array.isArray(activityBlock.events) ? activityBlock.events.map(pmActivityRecord).filter(Boolean) : []
    ).sort(function (left, right) {
      return (Number(left.created_at) || 0) - (Number(right.created_at) || 0);
    });
    const panelId = "pmo-subchat-" + String(subchat.id || subchat.thread_id || handle).replace(/[^a-z0-9_-]/gi, "-");
    const tools = Number(activityBlock.tool_calls || 0);
    const calls = Number(activityBlock.api_calls || 0);
    const completed = state === "completed";
    return h("article", { className: "pmo-subchat pmo-subchat--" + state + (open ? " is-open" : "") },
      h("button", {
        type: "button", className: "pmo-subchat__toggle",
        "aria-expanded": open, "aria-controls": panelId,
        "aria-label": (open ? "Collapse" : "Expand") + " specialist sub-chat with @" + handle,
        onClick: function (event) {
          // Keep the disclosure responsive even while the 2.5s transcript
          // refresh replaces its data object. The parent state/localStorage
          // remains authoritative for the next render; this immediate DOM
          // update prevents a refresh boundary from swallowing the click.
          const nextOpen = event.currentTarget.getAttribute("aria-expanded") !== "true";
          event.currentTarget.setAttribute("aria-expanded", nextOpen ? "true" : "false");
          event.currentTarget.setAttribute("aria-label", (nextOpen ? "Collapse" : "Expand") + " specialist sub-chat with @" + handle);
          const card = event.currentTarget.closest(".pmo-subchat");
          if (card) card.classList.toggle("is-open", nextOpen);
          const panel = card && card.querySelector(".pmo-subchat__detail");
          if (panel) panel.hidden = !nextOpen;
          if (props.onToggle) props.onToggle(subchat.id || subchat.thread_id);
        },
      },
        h("span", { className: "pmo-subchat__avatar", "aria-hidden": "true" }, "AG"),
        h("span", { className: "pmo-subchat__summary" },
          h("span", { className: "pmo-subchat__eyebrow" },
            h("strong", null, "@" + handle), h("span", null, "Specialist sub-chat")),
          h("span", { className: "pmo-subchat__question" }, subchat.question || subchat.title || "Specialist consultation"),
          h("span", { className: "pmo-subchat__meta" },
            completed ? "Recommendation ready" : (state === "working" ? "Working" : "Queued"),
            calls ? " · " + calls + " model call" + (calls === 1 ? "" : "s") : "",
            tools ? " · " + tools + " tool call" + (tools === 1 ? "" : "s") : "")),
        h("span", { className: "pmo-subchat__state pmo-subchat__state--" + state },
          completed ? "Completed" : (state === "working" ? "Working" : "Queued")),
        h("span", { className: "pmo-subchat__chevron", "aria-hidden": "true" }, "›")),
      h("section", { id: panelId, className: "pmo-subchat__detail", hidden: !open, "aria-label": "Operations inside @" + handle + " sub-chat" },
        h("div", { className: "pmo-subchat__delegation" },
          h("span", { "aria-hidden": "true" }, "↳"),
          h("div", null, h("strong", null, "Delegated by project manager"),
            h("p", null, subchat.question || "Specialist review"))),
        nested.length ? h("div", { className: "pmo-subchat__timeline" }, nested.map(function (activity, index) {
          return h(AgentActivity, { key: activity.id || "nested-activity-" + index, activity: activity, board: props.board });
        })) : !completed ? h("div", { className: "pmo-subchat__waiting", role: "status" },
          h("span", { "aria-hidden": "true" }), "Waiting for @", handle, " to begin…") : null,
        subchat.result ? h("div", { className: "pmo-subchat__result" },
          h("div", { className: "pmo-subchat__result-head" },
            h("span", { "aria-hidden": "true" }, "✓"),
            h("strong", null, "Recommendation from @" + handle)),
          h(MarkdownBlock, { source: pmLinkedMarkdown(subchat.result, props.board) })) : null));
  }

  function MentionComposer(props) {
    const [value, setValue] = useState("");
    const [validation, setValidation] = useState("");
    const [items, setItems] = useState([]);
    const [active, setActive] = useState(0);
    const [open, setOpen] = useState(false);
    const token = (value.match(/(?:^|\s)@([a-z0-9._-]*)$/i) || [])[1];
    useEffect(function () {
      if (token == null) { setOpen(false); setItems([]); return; }
      if (Array.isArray(props.mentionItems)) {
        const search = token.toLowerCase();
        const next = props.mentionItems.filter(function (item) {
          return String(item.handle || "").toLowerCase().startsWith(search);
        });
        setItems(next); setOpen(next.length > 0); setActive(0); return;
      }
      if (!props.board) { setOpen(false); setItems([]); return; }
      SDK.fetchJSON(withBoard(`${API}/mentions/autocomplete?prefix=${encodeURIComponent(token)}`, props.board)).then(function (data) {
        const next = data.items || []; setItems(next); setOpen(next.length > 0); setActive(0);
      }).catch(function () { setItems([]); setOpen(false); });
    }, [token, props.board, props.mentionItems]);
    const choose = function (item) {
      setValue(value.replace(/@([a-z0-9._-]*)$/i, "@" + item.handle + " ")); setOpen(false);
    };
    const submit = function () { if (!value.trim() || props.busy) return; if (props.requireMention && !/@[a-z0-9][a-z0-9._-]*/i.test(value)) { setValidation("Mention a project handle (for example @pm) before sending."); return; } setValidation(""); props.send(value).then(function (sent) { if (sent !== false) setValue(""); }); };
    return h("div", { className: "pmo-composer" },
      h("label", { htmlFor: props.id }, pt("chat.message")),
      h("textarea", { id: props.id, value: value, rows: 3, role: "combobox", "aria-autocomplete": "list", "aria-expanded": open, "aria-controls": props.id + "-list", "aria-activedescendant": open && items[active] ? props.id + "-option-" + active : undefined, placeholder: props.placeholder || pt("chat.placeholder"), onChange: function (event) { setValue(event.target.value); setValidation(""); }, onKeyDown: function (event) {
        if (!open) {
          if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); submit(); }
          return;
        }
        if (event.key === "ArrowDown") { event.preventDefault(); setActive((active + 1) % items.length); }
        if (event.key === "ArrowUp") { event.preventDefault(); setActive((active + items.length - 1) % items.length); }
        if (event.key === "Enter" && items[active]) { event.preventDefault(); choose(items[active]); }
        if (event.key === "Escape") { event.preventDefault(); setOpen(false); }
      } }),
      open ? h("ul", {
        id: props.id + "-list", role: "listbox", className: "pmo-combobox",
        "aria-label": pt("chat.suggestions"),
      }, (function () {
        // Group agents before people. Both are real and project-local — agents
        // come from project.yaml, people from access.yaml — and labelling the
        // split is the only way a writer can tell whether an @ will wake an
        // agent or notify a colleague.
        const nodes = [];
        let lastKind = null;
        items.forEach(function (item, index) {
          const isAgent = item.kind === "agent";
          const kindKey = isAgent ? "agent" : "human";
          if (kindKey !== lastKind) {
            nodes.push(h("li", {
              key: "grp-" + kindKey, className: "pmo-combobox__group",
              role: "presentation", "aria-hidden": "true",
            }, isAgent ? pt("chat.groupAgents") : pt("chat.groupPeople")));
            lastKind = kindKey;
          }
          nodes.push(h("li", {
            id: props.id + "-option-" + index, role: "option",
            "aria-selected": index === active, key: item.handle,
          }, h("button", {
            type: "button",
            onMouseDown: function (event) { event.preventDefault(); choose(item); },
          },
            h("span", { className: "pmo-combobox__handle" }, "@" + item.handle),
            h("span", { className: "pmo-combobox__role" }, item.role),
            h("span", {
              className: "pmo-combobox__kind" + (isAgent ? " pmo-combobox__kind--agent" : ""),
            }, isAgent ? pt("chat.agent") : pt("chat.person")))));
        });
        return nodes;
      })()) : null,
      validation ? h("p", { className: "pmo-state pmo-state--error", role: "alert" }, validation) : null,
      h(Button, { size: "sm", disabled: props.busy || !value.trim(), onClick: submit }, props.label || pt("chat.send")));
  }

  function ConversationsView(props) {
    const project = props.project;
    const listUrl = project ? withBoard(`${API}/projects/${encodeURIComponent(project.project_id)}/conversations`, props.board) : null;
    const listPair = usePmResource(listUrl, props.refreshKey);
    const [thread, setThread] = useState(null);
    const [busy, setBusy] = useState(false);
    const [olderMessages, setOlderMessages] = useState([]);
    const [olderPagination, setOlderPagination] = useState(null);
    const [loadingOlder, setLoadingOlder] = useState(false);
    const [sendError, setSendError] = useState("");
    const threadRef = useRef(thread);
    useEffect(function () { const rows = listPair[0].data && listPair[0].data.conversations; if (rows && rows.length && (!thread || !rows.some(function (item) { return item.thread_id === thread; }))) setThread(rows[0].thread_id); }, [listPair[0].data, thread]);
    useEffect(function () { threadRef.current = thread; setOlderMessages([]); setOlderPagination(null); setLoadingOlder(false); }, [thread]);
    const detailPair = usePmResource(thread ? withBoard(`${API}/conversations/${encodeURIComponent(thread)}`, props.board) : null, props.refreshKey);
    // Scroll hooks must sit above the early return below: React requires the
    // same hook order on every render, and a conditional return after a hook
    // call is error #310.
    const logRef = useRef(null);
    const detailForScroll = detailPair[0].data;
    const newestId = detailForScroll && detailForScroll.messages && detailForScroll.messages.length
      ? detailForScroll.messages[detailForScroll.messages.length - 1].id
      : null;
    // The project roster, so transcript names match composer handles.
    const rosterPair = usePmResource(
      props.board ? withBoard(`${API}/mentions/autocomplete`, props.board) : null,
      props.refreshKey,
    );
    const rosterIndex = pmRosterIndex(rosterPair[0].data && rosterPair[0].data.items);
    const scrolledThread = useRef(null);
    useEffect(function () {
      // Deferred a frame: on the render that first paints a thread the log
      // node has no laid-out height yet, so scrollTop would be a no-op and the
      // reader would land on the oldest message.
      const id = window.requestAnimationFrame(function () {
        const node = logRef.current;
        if (!node) return;
        const firstPaintOfThread = scrolledThread.current !== thread;
        // Jump to the newest message when opening a thread; afterwards follow
        // only if the reader is already near the bottom, so reading back
        // through history is never yanked away.
        const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
        if (firstPaintOfThread || distance < 200) {
          node.scrollTop = node.scrollHeight;
          scrolledThread.current = thread;
        }
      });
      return function () { window.cancelAnimationFrame(id); };
    }, [newestId, thread]);
    if (!listPair[0].data) return h(PmState, { loading: listPair[0].loading, error: listPair[0].error, retry: listPair[1] });
    const detail = detailPair[0].data;
    const send = function (body) {
      setBusy(true);
      setSendError("");
      const isClient = detail && detail.conversation.kind === "client";
      const path = isClient ? "client-replies" : "messages";
      return SDK.fetchJSON(withBoard(`${API}/conversations/${encodeURIComponent(thread)}/${path}`, props.board), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body: body }) }).then(function () { props.announce(pt("announce.sent")); return detailPair[1](); }).catch(function (error) { const message = parseApiErrorMessage(error); setSendError(message); props.announce(pt("states.error") + message); return false; }).finally(function () { setBusy(false); });
    };
    const pagination = olderPagination || (detail && detail.pagination);
    const loadOlder = function () {
      if (!thread || !pagination || !pagination.has_more || !pagination.before || loadingOlder) return;
      setLoadingOlder(true);
      const requestedThread = thread;
      const path = `${API}/conversations/${encodeURIComponent(thread)}?before=${encodeURIComponent(pagination.before)}&limit=${encodeURIComponent(pagination.limit || 100)}`;
      SDK.fetchJSON(withBoard(path, props.board)).then(function (page) {
        if (threadRef.current !== requestedThread) return;
        setOlderMessages(function (current) {
          const ids = new Set(current.map(function (message) { return message.id; }));
          return (page.messages || []).filter(function (message) { return !ids.has(message.id); }).concat(current);
        });
        setOlderPagination(page.pagination || null);
      }).catch(function (error) {
        props.announce(pt("states.error") + parseApiErrorMessage(error));
      }).finally(function () { if (threadRef.current === requestedThread) setLoadingOlder(false); });
    };
    const visibleMessages = detail ? olderMessages.concat(detail.messages) : [];
    // Who is reading. Used only to mark the viewer's own turns; every server
    // shape below is optional, so an older API simply yields no "you" badge.
    const viewerPrincipal = (detail && (detail.viewer_principal || detail.actor_id))
      || (listPair[0].data && (listPair[0].data.viewer_principal || listPair[0].data.actor_id))
      || "";
    return h("section", {
      className: "pmo-view", role: "region", "aria-labelledby": "pmo-chat-heading",
    },
      h("h2", { id: "pmo-chat-heading", tabIndex: -1 }, pt("sections.founders")),
      h("p", { className: "pmo-muted" }, pt("chat.gateway")),
      h("div", { className: "pmo-thread-layout" },
        h("nav", { className: "pmo-thread-list", "aria-label": pt("chat.threads") },
          listPair[0].data.conversations.map(function (item) {
            return h("button", {
              type: "button", key: item.thread_id,
              "aria-current": thread === item.thread_id ? "page" : undefined,
              onClick: function () { setThread(item.thread_id); },
            }, item.title);
          })),
        h("div", { className: "pmo-thread" },
          !detail
            ? h(PmState, {
                loading: detailPair[0].loading,
                error: detailPair[0].error,
                retry: detailPair[1],
              })
            : h(React.Fragment, null,
                h("div", { className: "pmo-thread-title" },
                  h("h3", null, detail.conversation.title),
                  // A client thread carries untrusted external content and can
                  // never instruct the PM (plan 010). It must not look like the
                  // trusted founder's thread — the trust model depends on the
                  // reader knowing which channel they are in.
                  detail.conversation.kind === "client"
                    ? h("span", { className: "pmo-thread-tag pmo-thread-tag--external" },
                        pt("chat.external"))
                    : null,
                  visibleMessages.length ? h("span", { className: "pmo-thread-count" },
                    pt("chat.messageCount", visibleMessages.length)) : null),
                detail.conversation.kind === "client"
                  ? h("p", { className: "pmo-external-note" }, pt("chat.externalNote"))
                  : null,
                pagination && pagination.has_more ? h("button", {
                  type: "button", className: "pmo-load-more",
                  disabled: loadingOlder, onClick: loadOlder,
                }, loadingOlder ? pt("states.loading") : pt("chat.loadOlder")) : null,
                h("div", {
                  className: "pmo-messages", role: "log", ref: logRef,
                  "aria-live": "polite", "aria-relevant": "additions",
                }, visibleMessages.length
                  ? visibleMessages.reduce(function (nodes, message, index) {
                      const identity = pmIdentity(message.author, rosterIndex);
                      const previous = index > 0 ? visibleMessages[index - 1] : null;
                      const dayLabel = pmDayLabel(message.created_at);
                      const newDay = !previous || pmDayLabel(previous.created_at) !== dayLabel;
                      if (newDay && dayLabel) {
                        nodes.push(h("div", { key: "day-" + message.id, className: "pmo-day-rule" }, dayLabel));
                      }
                      const grouped = !newDay
                        && previous
                        && previous.author === message.author
                        && Math.abs((Number(message.created_at) || 0) - (Number(previous.created_at) || 0))
                          <= PM_GROUP_WINDOW_SECONDS;
                      const isSelf = Boolean(viewerPrincipal)
                        && identity.principal === viewerPrincipal;
                      const classes = ["pmo-message"];
                      if (grouped) classes.push("pmo-message--grouped");
                      if (!identity.isHuman) classes.push("pmo-message--agent");
                      if (isSelf) classes.push("pmo-message--self");
                      if (message.kind && message.kind === "system") classes.push("pmo-message--system");
                      nodes.push(h("article", {
                        key: message.id,
                        className: classes.join(" "),
                        style: pmAvatarStyle(identity),
                      },
                        h("div", {
                          className: "pmo-message__avatar",
                          "aria-hidden": "true",
                        }, identity.initials),
                        grouped ? null : h("div", { className: "pmo-message__head" },
                          h("span", {
                            className: "pmo-message__author",
                            title: identity.principal,
                          }, identity.name),
                          identity.isHuman
                            ? (isSelf ? h("span", { className: "pmo-message__badge pmo-message__badge--you" }, pt("chat.you")) : null)
                            : h("span", { className: "pmo-message__badge pmo-message__badge--agent" }, pt("chat.agent")),
                          h("time", {
                            className: "pmo-message__time",
                            title: absolutePmTime(message.created_at),
                          }, formatPmTime(message.created_at))),
                        h("div", {
                          className: "pmo-message__body",
                          // Screen readers lose the author when the header is
                          // grouped away, so restate it on the bubble.
                          "aria-label": grouped ? identity.name + ": " + String(message.body || "") : undefined,
                        }, pmRenderBody(message.body))));
                      return nodes;
                    }, [])
                  : h("p", { className: "pmo-muted" }, pt("empty.founders"))),
                sendError ? h("p", { className: "pmo-state pmo-state--error", role: "alert" }, sendError) : null,
                h(MentionComposer, {
                  id: "pmo-message-composer", board: props.board,
                  busy: busy, send: send,
                  requireMention: detail.conversation.kind !== "client",
                  label: detail.conversation.kind === "client"
                    ? pt("chat.approvedReply") : pt("chat.send"),
                })))));
  }

  // Portfolio-wide Founder's Office. The legacy ConversationsView above stays
  // intact as a compatibility reference; this view consumes the additive
  // folders/global APIs and falls back to the legacy project lists.
  const PM_CHAT_BOTTOM_THRESHOLD = 96;

  function pmChatScrollTarget(saved, maxScroll) {
    const maximum = Math.max(0, Number(maxScroll) || 0);
    if (!saved || saved.atBottom !== false) return maximum;
    return Math.min(Math.max(0, Number(saved.top) || 0), maximum);
  }

  function FounderOfficeView(props) {
    const projects = (props.projects && props.projects.length) ? props.projects : (props.project ? [props.project] : []);
    const projectKey = projects.map(function (item) { return item.project_id + ":" + item.board_slug; }).join("|");
    const [catalog, setCatalog] = useState({ loading: true, data: null, error: null });
    const [selectedKey, setSelectedKey] = useState(function () {
      try { return window.localStorage.getItem("hermes.pmo.founders.selectedConversation") || ""; }
      catch (_error) { return ""; }
    });
    const [closedFolders, setClosedFolders] = useState({});
    const [creatingFor, setCreatingFor] = useState("");
    const [newTitle, setNewTitle] = useState("");
    const [createError, setCreateError] = useState("");
    const [createBusy, setCreateBusy] = useState(false);
    const [sendBusy, setSendBusy] = useState(false);
    const [sendError, setSendError] = useState("");
    const [mentionItems, setMentionItems] = useState([]);
    const [waiting, setWaiting] = useState(false);
    const [openSubchats, setOpenSubchats] = useState(function () {
      try { return JSON.parse(window.localStorage.getItem("hermes.pmo.founders.openSubchats") || "{}"); }
      catch (_error) { return {}; }
    });
    const loadFolders = useCallback(function () {
      setCatalog(function (old) { return { loading: true, data: old.data, error: null }; });
      return SDK.fetchJSON(`${API}/founders-office/folders`).then(function (data) {
        setCatalog({ loading: false, data: data, error: null }); return data;
      }).catch(function () {
        return Promise.all(projects.map(function (project) {
          return SDK.fetchJSON(withBoard(`${API}/projects/${encodeURIComponent(project.project_id)}/conversations`, project.board_slug))
            .then(function (data) { return Object.assign({}, project, { conversations: data.conversations || [] }); })
            .catch(function () { return Object.assign({}, project, { conversations: [] }); });
        }).concat([SDK.fetchJSON(`${API}/founders-office/global`).then(function (data) { return data.conversation; }).catch(function () { return null; })]))
          .then(function (rows) {
            const data = { folders: rows.slice(0, -1), global: rows[rows.length - 1] };
            setCatalog({ loading: false, data: data, error: null }); return data;
          });
      }).catch(function (error) {
        setCatalog({ loading: false, data: null, error: parseApiErrorMessage(error) }); return null;
      });
    }, [projectKey, props.refreshKey]);
    useEffect(function () { loadFolders(); }, [loadFolders]);

    const folders = pmConversationFolders(catalog.data, projects);
    const conversations = folders.reduce(function (rows, folder) { return rows.concat(folder.conversations || []); }, []);
    const selected = pmConversationByKey(folders, selectedKey) || conversations[0] || null;
    const actualKey = pmConversationKey(selected);
    const isGlobal = Boolean(selected && (selected.scope === "global" || selected.is_global || selected.kind === "global_founders_office"));
    const activeBoard = selected && selected.board_slug || props.board;
    const detailUrl = !selected ? null : (isGlobal
      ? (selected.thread_id === "global" ? `${API}/founders-office/global`
        : `${API}/founders-office/global/conversations/${encodeURIComponent(selected.thread_id)}`)
      : withBoard(`${API}/conversations/${encodeURIComponent(selected.thread_id)}`, activeBoard));
    const detailPair = usePmResource(detailUrl, props.refreshKey);
    const loadedDetail = detailPair[0].data;
    const detail = loadedDetail && loadedDetail.conversation && selected
      && String(loadedDetail.conversation.thread_id || "") === String(selected.thread_id || "")
      ? loadedDetail : null;
    const conversation = detail && detail.conversation || selected;
    const messages = detail && Array.isArray(detail.messages) ? detail.messages : [];
    const stream = pmChatStream(messages, detail);
    const viewer = detail && (detail.viewer_principal || detail.actor_id)
      || catalog.data && (catalog.data.viewer_principal || catalog.data.actor_id) || "";
    const rosterIndex = pmRosterIndex(mentionItems);
    const logRef = useRef(null);
    const activeThreadRef = useRef(selected && selected.thread_id);
    const scrollByConversationRef = useRef({});

    useEffect(function () {
      if (!actualKey || actualKey === selectedKey) return;
      setSelectedKey(actualKey);
    }, [actualKey, selectedKey]);
    useEffect(function () {
      activeThreadRef.current = selected && selected.thread_id;
      if (!actualKey) return;
      try { window.localStorage.setItem("hermes.pmo.founders.selectedConversation", actualKey); }
      catch (_error) { /* private mode */ }
    }, [actualKey]);
    useEffect(function () {
      if (!selected) { setMentionItems([]); return; }
      if (Array.isArray(selected.participants) && selected.participants.length) {
        const allowed = selected.participants.filter(function (item) {
          return item.mentionable !== false && pmAllowedChatParticipant(item, selected.kind === "client");
        });
        setMentionItems(pmMentionRoster([{ items: allowed }], folders, isGlobal).filter(function (item) {
          return item.mentionable !== false;
        }));
        return;
      }
      const boards = isGlobal
        ? folders.filter(function (folder) { return folder.scope === "project" && folder.board_slug; }).map(function (folder) { return folder.board_slug; })
        : (activeBoard ? [activeBoard] : []);
      const requestedThread = selected.thread_id;
      Promise.all(boards.map(function (boardSlug) {
        return SDK.fetchJSON(withBoard(`${API}/mentions/autocomplete`, boardSlug)).catch(function () { return { items: [] }; });
      })).then(function (responses) {
        if (activeThreadRef.current === requestedThread) {
          const allowed = responses.map(function (response) {
            return { items: (response.items || []).filter(function (item) {
              return pmAllowedChatParticipant(item, selected.kind === "client");
            }) };
          });
          setMentionItems(pmMentionRoster(allowed, folders, isGlobal).filter(function (item) {
            return item.mentionable !== false;
          }));
        }
      });
    }, [actualKey, projectKey, props.refreshKey]);
    useEffect(function () {
      if (!selected || typeof window.setInterval !== "function") return undefined;
      const timer = window.setInterval(function () { detailPair[1](); }, 2500);
      return function () { window.clearInterval(timer); };
    }, [actualKey, detailPair[1]]);
    useEffect(function () {
      const frame = window.requestAnimationFrame(function () {
        const node = logRef.current;
        if (!node || !actualKey) return;
        const maximum = Math.max(0, node.scrollHeight - node.clientHeight);
        const saved = scrollByConversationRef.current[actualKey];
        node.scrollTop = pmChatScrollTarget(saved, maximum);
        scrollByConversationRef.current[actualKey] = {
          top: node.scrollTop,
          atBottom: maximum - node.scrollTop <= PM_CHAT_BOTTOM_THRESHOLD,
        };
      });
      return function () { window.cancelAnimationFrame(frame); };
    }, [actualKey, stream.length]);
    useEffect(function () {
      const latest = stream.length ? stream[stream.length - 1] : null;
      if (waiting && latest && (latest.streamKind === "activity"
        || (latest.record && latest.record.author && latest.record.author !== viewer))) setWaiting(false);
    }, [actualKey, stream.length]);

    const recordChatScroll = function (event) {
      const node = event.currentTarget;
      if (!node || !actualKey) return;
      const maximum = Math.max(0, node.scrollHeight - node.clientHeight);
      const saved = scrollByConversationRef.current[actualKey];
      // A polling refresh can briefly replace the log with an empty container.
      // Do not let that transient zero overwrite the reader's real position.
      if (maximum <= 0 && saved && saved.top > 0) return;
      scrollByConversationRef.current[actualKey] = {
        top: node.scrollTop,
        atBottom: maximum - node.scrollTop <= PM_CHAT_BOTTOM_THRESHOLD,
      };
    };

    const choose = function (item) { setWaiting(false); setSendError(""); setSelectedKey(pmConversationKey(item)); };
    const send = function (body) {
      if (!selected) return Promise.resolve(false);
      setSendBusy(true); setSendError("");
      const isClient = conversation.kind === "client";
      const url = isGlobal ? (selected.thread_id === "global" ? `${API}/founders-office/global/messages`
        : `${API}/founders-office/global/conversations/${encodeURIComponent(selected.thread_id)}/messages`)
        : withBoard(`${API}/conversations/${encodeURIComponent(selected.thread_id)}/${isClient ? "client-replies" : "messages"}`, activeBoard);
      return SDK.fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body: body }) })
        .then(function (result) {
          props.announce(pt("announce.sent"));
          if (!isClient && (isGlobal || !result || result.dispatched !== false)) setWaiting(true);
          return detailPair[1]();
        }).catch(function (error) {
          const message = parseApiErrorMessage(error); setSendError(message); return false;
        }).finally(function () { setSendBusy(false); });
    };
    const createConversation = function (event, folder) {
      event.preventDefault();
      if (!newTitle.trim() || createBusy) return;
      setCreateBusy(true); setCreateError("");
      const createUrl = folder.scope === "global"
        ? `${API}/founders-office/global/conversations`
        : withBoard(`${API}/projects/${encodeURIComponent(folder.project_id)}/conversations`, folder.board_slug);
      SDK.fetchJSON(createUrl, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(folder.scope === "global"
          ? { title: newTitle.trim() }
          : { title: newTitle.trim(), kind: "founders_office" }),
      }).then(function (result) {
        const created = result && (result.conversation || result);
        setCreatingFor(""); setNewTitle("");
        // Refresh the catalog before selecting the new thread. Selecting it
        // first creates a render where the key is absent from the old catalog;
        // the fallback thread then wins and can be loaded against the wrong
        // project board, yielding a misleading 403 until the user clicks again.
        return loadFolders().then(function () {
          if (created && created.thread_id) choose(Object.assign({}, created, {
            scope: folder.scope, is_global: folder.scope === "global",
            project_id: created.project_id || folder.project_id,
            board_slug: created.board_slug || folder.board_slug,
          }));
        });
      }).catch(function (error) { setCreateError(pt("chat.createError") + parseApiErrorMessage(error)); })
        .finally(function () { setCreateBusy(false); });
    };

    const workflowResponses = pmWorkflowResponses(messages);
    const renderMessage = function (message, index) {
      const identity = pmIdentity(message.author, rosterIndex);
      const workflow = pmWorkflowEnvelope(message.body || "");
      const workflowResolved = workflow && workflow.marker === PM_DETAIL_REQUEST_MARKER
        ? workflowResponses.details.has(String(message.id))
        : Boolean(workflow && workflow.marker === PM_TICKET_CONFIRMATION_MARKER && workflowResponses.confirmations.has(String(message.id)));
      const workflowResponse = workflow && workflow.marker === PM_DETAIL_REQUEST_MARKER
        ? workflowResponses.details.get(String(message.id)) : null;
      const self = Boolean(viewer) && identity.principal === viewer;
      const classes = ["pmo-message", !identity.isHuman ? "pmo-message--agent" : "", self ? "pmo-message--self" : "", message.kind === "system" ? "pmo-message--system" : ""].filter(Boolean).join(" ");
      return h("article", { key: message.id || "message-" + index, className: classes, style: pmAvatarStyle(identity) },
        h("div", { className: "pmo-message__avatar", "aria-hidden": "true" }, identity.initials),
        h("div", { className: "pmo-message__head" },
          h("span", { className: "pmo-message__author", title: identity.principal }, identity.name),
          h("span", { className: "pmo-message__badge" + (!identity.isHuman ? " pmo-message__badge--agent" : "") }, self ? pt("chat.you") : (!identity.isHuman ? pt("chat.agent") : pt("chat.person"))),
          message.created_at ? h("time", { className: "pmo-message__time", title: absolutePmTime(message.created_at) }, formatPmTime(message.created_at)) : null),
        h("div", { className: "pmo-message__body" },
          h(MarkdownBlock, { source: pmLinkedMarkdown(pmWorkflowVisibleBody(message.body || ""), activeBoard) }),
          !identity.isHuman ? h(FounderWorkflowCard, { message: message, send: send, busy: sendBusy, resolved: workflowResolved, response: workflowResponse }) : null,
          !identity.isHuman ? h(PmActionPreviews, { previews: pmMessagePreviews(message), board: activeBoard }) : null));
    };

    if (!catalog.data) return h(PmState, { loading: catalog.loading, error: catalog.error, retry: loadFolders });
    const participants = pmMentionRoster([{
      items: Array.isArray(conversation.participants) ? conversation.participants
        : (Array.isArray(selected.participants) ? selected.participants : mentionItems),
    }], folders, isGlobal);
    return h("section", { className: "pmo-view pmo-chat-view", role: "region", "aria-labelledby": "pmo-chat-heading" },
      h("div", { className: "pmo-chat-heading" }, h("div", null,
        h("h2", { id: "pmo-chat-heading", tabIndex: -1 }, pt("sections.founders")),
        h("p", { className: "pmo-muted" }, pt("chat.gateway"))),
        h("span", { className: "pmo-status" }, projects.length, " projects")),
      h("div", { className: "pmo-thread-layout" },
        h("aside", { className: "pmo-chat-sidebar" },
          h("div", { className: "pmo-chat-sidebar__title" }, pt("chat.threads"), h("span", null, conversations.length)),
          h("nav", { className: "pmo-thread-list", "aria-label": pt("chat.threads") }, folders.map(function (folder) {
            const closed = Boolean(closedFolders[folder.key]);
            return h("section", { className: "pmo-chat-folder", key: folder.key },
              h("div", { className: "pmo-chat-folder__head" },
                h("button", { type: "button", className: "pmo-chat-folder__toggle", "aria-expanded": !closed,
                  onClick: function () { setClosedFolders(Object.assign({}, closedFolders, { [folder.key]: !closed })); } },
                  h("span", { "aria-hidden": "true" }, closed ? "›" : "⌄"), h("span", null, folder.name),
                  h("small", null, folder.scope === "global" ? pt("chat.allProjects") : (folder.pm_profile ? "@" + folder.pm_profile : "PM"))),
                h("button", { type: "button", className: "pmo-chat-folder__add", title: pt("chat.newConversation"),
                  "aria-label": pt("chat.newConversation") + " · " + folder.name,
                  onClick: function () { setCreatingFor(folder.key); setNewTitle(""); setCreateError(""); } }, "+")),
              !closed ? h("div", { className: "pmo-chat-folder__body" },
                (folder.conversations || []).map(function (item) { return h("button", {
                  type: "button", key: pmConversationKey(item), className: "pmo-chat-thread-button",
                  "aria-current": pmConversationKey(item) === actualKey ? "page" : undefined,
                  onClick: function () { choose(item); },
                }, h("span", { "aria-hidden": "true" }, item.kind === "client" ? "↗" : (folder.scope === "global" ? "◎" : "#")),
                  h("span", null, item.title || pt("chat.global")), item.unread_count ? h("b", null, item.unread_count) : null); }),
                creatingFor === folder.key ? h("form", { className: "pmo-new-conversation", onSubmit: function (event) { createConversation(event, folder); } },
                  h("label", { className: "pmo-sr-only", htmlFor: "pmo-new-conversation-title" }, pt("chat.conversationTitle")),
                  h("input", { id: "pmo-new-conversation-title", autoFocus: true, value: newTitle, placeholder: pt("chat.conversationTitle"), onChange: function (event) { setNewTitle(event.target.value); }, onKeyDown: function (event) { if (event.key === "Escape") setCreatingFor(""); } }),
                  h("div", null, h(Button, { type: "submit", size: "sm", disabled: createBusy || !newTitle.trim() }, pt("chat.create")),
                    h("button", { type: "button", className: "pmo-link-button", onClick: function () { setCreatingFor(""); } }, pt("chat.cancel"))),
                  createError ? h("p", { className: "pmo-state pmo-state--error", role: "alert" }, createError) : null) : null) : null);
          }))),
        h("main", { className: "pmo-thread" }, !detail ? h(PmState, { loading: detailPair[0].loading, error: detailPair[0].error, retry: detailPair[1] }) : h(React.Fragment, null,
          h("header", { className: "pmo-thread-header" },
            h("div", { className: "pmo-thread-title" }, h("h3", null, conversation.title),
              isGlobal ? h("span", { className: "pmo-thread-tag pmo-thread-tag--global" }, pt("chat.allProjects")) : null,
              conversation.kind === "client" ? h("span", { className: "pmo-thread-tag pmo-thread-tag--external" }, pt("chat.external")) : null,
              h("span", { className: "pmo-thread-count" }, pt("chat.messageCount", messages.length))),
            h("div", { className: "pmo-participants", "aria-label": pt("chat.participants") }, participants.map(function (item) {
              return h("span", { key: item.kind + ":" + item.handle, className: "pmo-participant" + (item.kind === "agent" ? " pmo-participant--agent" : ""), title: item.role || "" }, "@", item.handle, h("small", null, item.kind === "agent" ? pt("chat.agent") : pt("chat.person")));
            }))),
          conversation.kind === "client" ? h("p", { className: "pmo-external-note" }, pt("chat.externalNote")) : null,
          h("div", { className: "pmo-messages", role: "log", ref: logRef, onScroll: recordChatScroll, "aria-live": "polite", "aria-relevant": "additions" },
            stream.length ? stream.map(function (item, index) {
              if (item.streamKind === "activity") return h(AgentActivity, { key: item.key, activity: item.record, board: item.record.board_slug || activeBoard });
              if (item.streamKind === "subchat") {
                const subchatId = String(item.record.id || item.record.thread_id || item.key);
                return h(SpecialistSubChat, {
                  key: item.key, subchat: item.record, board: item.record.board_slug || activeBoard,
                  open: Boolean(openSubchats[subchatId]),
                  onToggle: function () {
                    const nextOpenSubchats = Object.assign({}, openSubchats, {
                      [subchatId]: !openSubchats[subchatId],
                    });
                    try { window.localStorage.setItem("hermes.pmo.founders.openSubchats", JSON.stringify(nextOpenSubchats)); }
                    catch (_error) { /* private mode */ }
                    setOpenSubchats(nextOpenSubchats);
                  },
                });
              }
              return renderMessage(item.record, index);
            }) : h("p", { className: "pmo-muted" }, pt("empty.founders"))),
          waiting ? h("div", { className: "pmo-agent-waiting", role: "status" }, h("span", { "aria-hidden": "true" }), pt("chat.waiting")) : null,
          sendError ? h("p", { className: "pmo-state pmo-state--error", role: "alert" }, sendError) : null,
          h(MentionComposer, { id: "pmo-message-composer", board: activeBoard, mentionItems: mentionItems,
            busy: sendBusy, send: send, requireMention: conversation.kind !== "client",
            placeholder: isGlobal ? "Message project managers or specialists with @…" : undefined,
            label: conversation.kind === "client" ? pt("chat.approvedReply") : pt("chat.send") })))));
  }

  // Administration is intentionally separate from Founder's Office. It is an
  // operator record and control room, not another path for giving delivery
  // instructions to a project's PM.
  function AdministrationView(props) {
    const catalogPair = usePmResource(`${API}/administration`, props.refreshKey);
    const catalog = catalogPair[0].data;
    const [selectedKey, setSelectedKey] = useState("");
    const [draft, setDraft] = useState("");
    const [agentRole, setAgentRole] = useState("dev");
    const [agentCount, setAgentCount] = useState("1");
    const [agentModel, setAgentModel] = useState("");
    const [modelHandle, setModelHandle] = useState("pm");
    const [modelValue, setModelValue] = useState("");
    const [runtimeSkills, setRuntimeSkills] = useState("");
    const [runtimePlugins, setRuntimePlugins] = useState("");
    const [runtimeChannels, setRuntimeChannels] = useState("");
    const [globalModels, setGlobalModels] = useState("");
    const [globalSkills, setGlobalSkills] = useState("");
    const [globalPlugins, setGlobalPlugins] = useState("");
    const [globalChannels, setGlobalChannels] = useState("");
    const [globalAgents, setGlobalAgents] = useState("");
    const [oauth, setOauth] = useState(null);
    const [busy, setBusy] = useState("");
    const [error, setError] = useState("");
    const projects = catalog && Array.isArray(catalog.projects) ? catalog.projects : [];
    const globalKey = "__global__";
    const selected = selectedKey === globalKey ? null : (projects.find(function (item) { return item.project_id === selectedKey; }) || projects[0] || null);
    const activeKey = selectedKey || (selected ? selected.project_id : (catalog && catalog.can_global ? globalKey : ""));
    const isGlobal = activeKey === globalKey;
    const detailUrl = isGlobal ? `${API}/administration/global/messages` : (selected ? withProject(`${API}/administration/projects/${encodeURIComponent(selected.project_id)}/messages`, selected.project_id) : null);
    const detailPair = usePmResource(detailUrl, props.refreshKey);
    const detail = detailPair[0].data;
    const activeProject = detail && detail.project || selected;
    const permissions = activeProject && activeProject.permissions || {};
    const messages = detail && Array.isArray(detail.messages) ? detail.messages : [];
    const health = activeProject && activeProject.health || {};
    const profiles = Array.isArray(health.profiles) ? health.profiles : [];
    const credentialRows = Array.isArray(health.providers) ? health.providers : [];
    const projectConfiguration = activeProject && activeProject.configuration || {};
    const runtimeConfig = projectConfiguration.project && projectConfiguration.project.runtime || {};
    const effectiveRuntime = projectConfiguration.effective && projectConfiguration.effective.runtime || {};
    const globalConfiguration = catalog && catalog.global_configuration || {};
    const globalPermissions = catalog && catalog.global_permissions || {};

    useEffect(function () {
      if (!selectedKey && projects.length) setSelectedKey(projects[0].project_id);
      else if (!selectedKey && catalog && catalog.can_global) setSelectedKey(globalKey);
    }, [selectedKey, projects.length, catalog && catalog.can_global]);
    useEffect(function () {
      if (!profiles.length) return;
      if (!profiles.some(function (item) { return item.handle === modelHandle; })) setModelHandle(profiles[0].handle);
    }, [detail && detail.project && detail.project.project_id, profiles.length]);
    useEffect(function () {
      const asText = function (items) { return Array.isArray(items) ? items.join(", ") : ""; };
      setRuntimeSkills(asText(runtimeConfig.skills));
      setRuntimePlugins(asText(runtimeConfig.plugins));
      setRuntimeChannels(asText(runtimeConfig.channels));
    }, [activeProject && activeProject.project_id, JSON.stringify(runtimeConfig)]);
    useEffect(function () {
      const globalRuntime = globalConfiguration.runtime || {};
      const modelPairs = Object.keys(globalConfiguration.models || {}).filter(function (key) { return globalConfiguration.models[key]; }).map(function (key) { return key + "=" + globalConfiguration.models[key]; });
      const asText = function (items) { return Array.isArray(items) ? items.join(", ") : ""; };
      setGlobalModels(modelPairs.join(", "));
      setGlobalSkills(asText(globalRuntime.skills));
      setGlobalPlugins(asText(globalRuntime.plugins));
      setGlobalChannels(asText(globalRuntime.channels));
      setGlobalAgents(asText(globalConfiguration.access && globalConfiguration.access.global_agents));
    }, [JSON.stringify(globalConfiguration)]);

    const send = function (event) {
      event.preventDefault();
      if (!draft.trim() || busy || !detailUrl) return;
      setBusy("message"); setError("");
      const url = isGlobal ? `${API}/administration/global/messages` : withProject(`${API}/administration/projects/${encodeURIComponent(activeProject.project_id)}/messages`, activeProject.project_id);
      SDK.fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body: draft.trim() }) })
        .then(function () { setDraft(""); return detailPair[1](); })
        .catch(function (reason) { setError(parseApiErrorMessage(reason)); })
        .finally(function () { setBusy(""); });
    };
    const provision = function () {
      if (!activeProject || busy || !permissions.manage_agents) return;
      const count = Math.max(1, Math.min(8, Number(agentCount) || 1));
      setBusy("agent"); setError("");
      SDK.fetchJSON(withProject(`${API}/administration/projects/${encodeURIComponent(activeProject.project_id)}/agents`, activeProject.project_id), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: agentRole, count: count, model: agentModel.trim() || null }),
      }).then(function () { props.announce("Project agent created."); return detailPair[1](); })
        .catch(function (reason) { setError(parseApiErrorMessage(reason)); })
        .finally(function () { setBusy(""); });
    };
    const setModel = function () {
      if (!activeProject || busy || !permissions.manage_config) return;
      setBusy("model"); setError("");
      SDK.fetchJSON(withProject(`${API}/administration/projects/${encodeURIComponent(activeProject.project_id)}/models`, activeProject.project_id), {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ handle: modelHandle, model: modelValue.trim() || null }),
      }).then(function () { props.announce("Project model route updated."); return detailPair[1](); })
        .catch(function (reason) { setError(parseApiErrorMessage(reason)); })
        .finally(function () { setBusy(""); });
    };
    const runtimeList = function (value) {
      const seen = {};
      return String(value || "").split(",").map(function (item) { return item.trim(); }).filter(function (item) {
        const key = item.toLowerCase();
        if (!item || seen[key]) return false;
        seen[key] = true;
        return true;
      });
    };
    const saveRuntime = function () {
      if (!activeProject || busy || !permissions.manage_config) return;
      setBusy("runtime"); setError("");
      SDK.fetchJSON(withProject(`${API}/administration/projects/${encodeURIComponent(activeProject.project_id)}/runtime`, activeProject.project_id), {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skills: runtimeList(runtimeSkills), plugins: runtimeList(runtimePlugins), channels: runtimeList(runtimeChannels) }),
      }).then(function () {
        props.announce("Project runtime configuration saved.");
        return Promise.all([detailPair[1](), catalogPair[1]()]);
      }).catch(function (reason) { setError(parseApiErrorMessage(reason)); })
        .finally(function () { setBusy(""); });
    };
    const globalModelMap = function (value) {
      const allowed = { pm: true, worker: true, reviewer: true, summariser: true, escalation_override: true };
      const result = {};
      String(value || "").split(",").forEach(function (entry) {
        const pair = entry.split("="); const key = String(pair.shift() || "").trim(); const model = pair.join("=").trim();
        if (key && model && allowed[key]) result[key] = model;
      });
      return result;
    };
    const saveGlobalConfiguration = function () {
      if (busy || !catalog || !catalog.can_global) return;
      setBusy("global-config"); setError("");
      SDK.fetchJSON(`${API}/administration/global/configuration`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ models: globalModelMap(globalModels), runtime: { skills: runtimeList(globalSkills), plugins: runtimeList(globalPlugins), channels: runtimeList(globalChannels) } }),
      }).then(function () { props.announce("Global PMO configuration saved."); return Promise.all([catalogPair[1](), detailPair[1]()]); })
        .catch(function (reason) { setError(parseApiErrorMessage(reason)); })
        .finally(function () { setBusy(""); });
    };
    const saveGlobalAgents = function () {
      if (busy || !globalPermissions.manage_agents) return;
      setBusy("global-agents"); setError("");
      SDK.fetchJSON(`${API}/administration/global/configuration/agents`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ principals: runtimeList(globalAgents) }),
      }).then(function () { props.announce("Global configuration agent access saved."); return Promise.all([catalogPair[1](), detailPair[1]()]); })
        .catch(function (reason) { setError(parseApiErrorMessage(reason)); })
        .finally(function () { setBusy(""); });
    };
    const connectCodex = function () {
      if (!activeProject || busy || !permissions.connect_provider) return;
      let loginWindow = null;
      try { loginWindow = window.open("about:blank", "datansh-codex-login"); if (loginWindow) loginWindow.opener = null; } catch (_error) { loginWindow = null; }
      setBusy("oauth"); setError("");
      SDK.fetchJSON(withProject(`${API}/administration/projects/${encodeURIComponent(activeProject.project_id)}/oauth/openai-codex/start`, activeProject.project_id), {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ handle: modelHandle || "pm" }),
      }).then(function (result) {
        setOauth(result);
        if (loginWindow && result && result.verification_url) loginWindow.location.replace(result.verification_url);
        props.announce("OpenAI Codex sign-in started.");
        return detailPair[1]();
      }).catch(function (reason) { if (loginWindow) loginWindow.close(); setError(parseApiErrorMessage(reason)); })
        .finally(function () { setBusy(""); });
    };
    const disconnectCodex = function (handle) {
      if (!activeProject || busy || !permissions.connect_provider) return;
      setBusy("credential:" + handle); setError("");
      SDK.fetchJSON(withProject(`${API}/administration/projects/${encodeURIComponent(activeProject.project_id)}/credentials/${encodeURIComponent(handle)}/openai-codex`, activeProject.project_id), {
        method: "DELETE",
      }).then(function () {
        props.announce("Project credential removed; global OpenAI Codex is now the fallback.");
        return detailPair[1]();
      }).catch(function (reason) { setError(parseApiErrorMessage(reason)); })
        .finally(function () { setBusy(""); });
    };
    const action = function (item) {
      if (!item) return;
      if (item.kind === "connect_codex") { connectCodex(); return; }
      if (item.kind === "refresh_health") { detailPair[1](); return; }
      if (item.kind === "open_onboarding") { props.navigate("onboarding"); return; }
      if (item.kind === "open_access") { props.navigate("access"); return; }
      if (item.kind === "create_agent") { const node = document.getElementById("pmo-admin-agent-role"); if (node) node.focus(); }
    };
    const oauthPanel = oauth ? h("article", { className: "pmo-panel pmo-github-setup", role: "status" }, h("h3", null, "OpenAI Codex sign-in"), h("p", null, "Approve the device code for @", oauth.handle, "."), h("code", null, oauth.user_code), oauth.verification_url ? h("p", null, h("a", { href: oauth.verification_url, target: "_blank", rel: "noreferrer" }, "Open OpenAI verification")) : null) : null;

    if (!catalog) return h(PmState, { loading: catalogPair[0].loading, error: catalogPair[0].error, retry: catalogPair[1] });
    if (!catalog.can_access) return h("section", { className: "pmo-view" }, h("h2", { tabIndex: -1 }, "Administration"), h("p", { className: "pmo-muted" }, "Administration is available to project operators and global administrators."));
    return h("section", { className: "pmo-view pmo-chat-view", role: "region", "aria-labelledby": "pmo-administration-heading" },
      h("div", { className: "pmo-chat-heading" }, h("div", null,
        h("h2", { id: "pmo-administration-heading", tabIndex: -1 }, "Hermes Administration"),
        h("p", { className: "pmo-muted" }, "Project-scoped agents, models, skills, plugins, channels, provider sign-in, and health. Changes are explicit and audited.")),
        h("div", { className: "pmo-actions" },
          h("label", { className: "pmo-project-picker", htmlFor: "pmo-administration-project-switch" }, h("span", null, "Configuration scope"), h("select", { id: "pmo-administration-project-switch", value: activeKey, onChange: function (event) { setSelectedKey(event.target.value); setOauth(null); setError(""); } },
            catalog.can_global ? h("option", { value: globalKey }, "Global administration") : null,
            projects.map(function (project) { return h("option", { key: project.project_id, value: project.project_id }, project.name); }))),
          h(Button, { size: "sm", onClick: function () { detailPair[1](); catalogPair[1](); } }, "Refresh"))),
      h("div", { className: "pmo-thread-layout pmo-admin-layout" },
        h("aside", { className: "pmo-chat-sidebar" },
          h("div", { className: "pmo-chat-sidebar__title" }, "Administration folders"),
          catalog.can_global ? h("button", { type: "button", className: "pmo-admin-folder-button", "aria-current": isGlobal ? "page" : undefined, onClick: function () { setSelectedKey(globalKey); setOauth(null); setError(""); } }, h("span", { className: "pmo-admin-folder-button__icon", "aria-hidden": "true" }, "◉"), h("span", { className: "pmo-admin-folder-button__name" }, "Global administration"), h("span", { className: "pmo-admin-folder-button__role" }, "Global")) : null,
          projects.map(function (project) { return h("button", { type: "button", key: project.project_id, className: "pmo-admin-folder-button", "aria-current": !isGlobal && activeProject && activeProject.project_id === project.project_id ? "page" : undefined, onClick: function () { setSelectedKey(project.project_id); setOauth(null); setError(""); } }, h("span", { className: "pmo-admin-folder-button__icon", "aria-hidden": "true" }, "#"), h("span", { className: "pmo-admin-folder-button__name" }, project.name), h("span", { className: "pmo-admin-folder-button__role" }, project.permissions.role)); })),
        h("main", { className: "pmo-thread pmo-admin-thread" }, !detail ? h(PmState, { loading: detailPair[0].loading, error: detailPair[0].error, retry: detailPair[1] }) : h(React.Fragment, null,
          h("header", { className: "pmo-thread-header" }, h("div", { className: "pmo-thread-title" }, h("h3", null, isGlobal ? "Global administration" : activeProject.name), h("span", { className: "pmo-thread-tag pmo-thread-tag--global" }, isGlobal ? "Global admin" : permissions.role || "project"))),
          !isGlobal ? h("div", { className: "pmo-admin-tools" },
            h("article", { className: "pmo-panel" }, h("h3", null, "Project agents"), h("p", { className: "pmo-muted" }, "Creates isolated project profiles; no other project can use them."),
              h("div", { className: "pmo-onboarding-fields" },
                h("label", { htmlFor: "pmo-admin-agent-role" }, h("span", null, "Role"), h("select", { id: "pmo-admin-agent-role", value: agentRole, disabled: !permissions.manage_agents || !!busy, onChange: function (event) { setAgentRole(event.target.value); } }, ["dev", "qa", "ops", "research", "design", "data"].map(function (role) { return h("option", { key: role, value: role }, role); }))),
                h("label", { htmlFor: "pmo-admin-agent-count" }, h("span", null, "Count"), h(Input, { id: "pmo-admin-agent-count", type: "number", min: "1", max: "8", value: agentCount, disabled: !permissions.manage_agents || !!busy, onChange: function (event) { setAgentCount(event.target.value); } })),
                h("label", { htmlFor: "pmo-admin-agent-model" }, h("span", null, "Optional model pin"), h(Input, { id: "pmo-admin-agent-model", value: agentModel, placeholder: "gpt-5.6-luna", disabled: !permissions.manage_agents || !!busy, onChange: function (event) { setAgentModel(event.target.value); } }))),
              h(Button, { size: "sm", disabled: !permissions.manage_agents || !!busy, onClick: provision }, busy === "agent" ? "Creating…" : "Create agent")),
            h("article", { className: "pmo-panel" }, h("h3", null, "Model routing"), h("p", { className: "pmo-muted" }, "Model pins are stored in this project's project.yaml. Credential selection is managed separately below."),
              h("div", { className: "pmo-onboarding-fields" },
                h("label", { htmlFor: "pmo-admin-model-handle" }, h("span", null, "Agent"), h("select", { id: "pmo-admin-model-handle", value: modelHandle, disabled: !permissions.manage_config || !!busy, onChange: function (event) { setModelHandle(event.target.value); } }, profiles.map(function (profile) { return h("option", { key: profile.handle, value: profile.handle }, "@" + profile.handle + " · " + profile.profile); }))),
                h("label", { htmlFor: "pmo-admin-model-value" }, h("span", null, "Model route"), h(Input, { id: "pmo-admin-model-value", value: modelValue, placeholder: "gpt-5.6-luna", disabled: !permissions.manage_config || !!busy, onChange: function (event) { setModelValue(event.target.value); } }))),
              h("div", { className: "pmo-actions" }, h(Button, { size: "sm", disabled: !permissions.manage_config || !!busy, onClick: setModel }, "Save model route"))),
            h("article", { className: "pmo-panel", "aria-labelledby": "pmo-project-runtime-heading" }, h("h3", { id: "pmo-project-runtime-heading" }, "Project runtime"),
              h("p", { className: "pmo-muted" }, "These settings belong only to this project. Skills are used as defaults for newly created tasks. Plugin and channel entries are project declarations; saving them does not install plugins or enable channels globally."),
              h("div", { className: "pmo-onboarding-fields" },
                h("label", { htmlFor: "pmo-admin-runtime-skills" }, h("span", null, "Default skills"), h(Input, { id: "pmo-admin-runtime-skills", value: runtimeSkills, placeholder: "pmo, browser", disabled: !permissions.manage_config || !!busy, onChange: function (event) { setRuntimeSkills(event.target.value); } })),
                h("label", { htmlFor: "pmo-admin-runtime-plugins" }, h("span", null, "Allowed plugins"), h(Input, { id: "pmo-admin-runtime-plugins", value: runtimePlugins, placeholder: "github, slack", disabled: !permissions.manage_config || !!busy, onChange: function (event) { setRuntimePlugins(event.target.value); } })),
                h("label", { htmlFor: "pmo-admin-runtime-channels" }, h("span", null, "Delivery channels"), h(Input, { id: "pmo-admin-runtime-channels", value: runtimeChannels, placeholder: "pmo, slack", disabled: !permissions.manage_config || !!busy, onChange: function (event) { setRuntimeChannels(event.target.value); } }))),
              h("p", { className: "pmo-muted" }, "Use comma-separated identifiers. Entries are validated and unique within this project."),
              h("p", { className: "pmo-muted" }, "Effective skills for new tasks: " + (effectiveRuntime.skills || []).join(", ") + ". Global defaults are listed first; duplicates are removed."),
              h("div", { className: "pmo-actions" }, h(Button, { size: "sm", disabled: !permissions.manage_config || !!busy, onClick: saveRuntime }, busy === "runtime" ? "Saving…" : "Save project runtime"))),
            h("article", { className: "pmo-panel", "aria-labelledby": "pmo-project-credentials-heading" }, h("h3", { id: "pmo-project-credentials-heading" }, "Project AI credentials"),
              h("p", { className: "pmo-muted" }, "A project credential overrides the global OpenAI Codex credential for that agent. Without one, the agent automatically uses the global credential."),
              credentialRows.length ? credentialRows.map(function (row) {
                const source = row.openai_codex_source || "missing";
                const label = source === "project" ? "Project-specific" : (source === "global" ? "Global fallback" : "Not configured");
                return h("div", { className: "pmo-row", key: row.handle },
                  h("div", null, h("strong", null, "@" + row.handle), h("p", { className: "pmo-muted" }, row.profile)),
                  h("span", { className: "pmo-thread-tag" + (source === "project" ? " pmo-thread-tag--global" : "") }, label),
                  source === "project" ? h(Button, { size: "sm", disabled: !permissions.connect_provider || !!busy, onClick: function () { disconnectCodex(row.handle); } }, busy === "credential:" + row.handle ? "Removing…" : "Use global instead") : null);
              }) : h("p", { className: "pmo-muted" }, "No project agent profiles found."),
              h("div", { className: "pmo-actions" }, h(Button, { size: "sm", disabled: !permissions.connect_provider || !!busy || !modelHandle, onClick: connectCodex }, busy === "oauth" ? "Starting sign-in…" : "Set project credential for @" + (modelHandle || "pm")))),
            h("article", { className: "pmo-panel" }, h("h3", null, "Health"), h("p", null, health.project_config ? "Project config available" : "Project config missing"), h("p", { className: "pmo-muted" }, profiles.map(function (item) { return "@" + item.handle + ": " + (item.available ? "ready" : "profile missing"); }).join(" · ") || "No profiles found.")),
            oauthPanel
          ) : h("div", { className: "pmo-admin-tools" },
            h("article", { className: "pmo-panel" }, h("h3", null, "Global control plane"), h("p", { className: "pmo-muted" }, "Use this channel for IAM, project onboarding, and cross-project operational decisions. Project-level agent and provider changes remain in their project folder."), h("div", { className: "pmo-actions" }, h(Button, { size: "sm", onClick: function () { props.navigate("onboarding"); } }, "Project onboarding"), h(Button, { size: "sm", onClick: function () { props.navigate("access"); } }, "Access management"))),
            h("article", { className: "pmo-panel", "aria-labelledby": "pmo-global-runtime-heading" }, h("h3", { id: "pmo-global-runtime-heading" }, "Global PMO defaults"), h("p", { className: "pmo-muted" }, "Global defaults are combined with each project’s additions. Project operators can view the result but cannot edit these values."),
              h("div", { className: "pmo-onboarding-fields" },
                h("label", { htmlFor: "pmo-global-models" }, h("span", null, "Model routes"), h(Input, { id: "pmo-global-models", value: globalModels, placeholder: "pm=gpt-5.6, worker=gpt-5.6-mini", disabled: !!busy, onChange: function (event) { setGlobalModels(event.target.value); } })),
                h("label", { htmlFor: "pmo-global-skills" }, h("span", null, "Default skills"), h(Input, { id: "pmo-global-skills", value: globalSkills, placeholder: "pmo, browser", disabled: !!busy, onChange: function (event) { setGlobalSkills(event.target.value); } })),
                h("label", { htmlFor: "pmo-global-plugins" }, h("span", null, "Allowed plugins"), h(Input, { id: "pmo-global-plugins", value: globalPlugins, placeholder: "github, slack", disabled: !!busy, onChange: function (event) { setGlobalPlugins(event.target.value); } })),
                h("label", { htmlFor: "pmo-global-channels" }, h("span", null, "Delivery channels"), h(Input, { id: "pmo-global-channels", value: globalChannels, placeholder: "pmo, slack", disabled: !!busy, onChange: function (event) { setGlobalChannels(event.target.value); } }))),
              h("p", { className: "pmo-muted" }, "Model routes use comma-separated role=model pairs. Supported roles: pm, worker, reviewer, summariser, escalation_override."),
              h("div", { className: "pmo-actions" }, h(Button, { size: "sm", disabled: !!busy, onClick: saveGlobalConfiguration }, busy === "global-config" ? "Saving…" : "Save global defaults"))),
            h("article", { className: "pmo-panel", "aria-labelledby": "pmo-global-agent-access-heading" }, h("h3", { id: "pmo-global-agent-access-heading" }, "Global configuration agents"),
              h("p", { className: "pmo-muted" }, "Exact Hermes machine identities that may edit global models, skills, plugins, and channels. They cannot add themselves here and gain no project access."),
              h("label", { htmlFor: "pmo-global-configuration-agents" }, h("span", null, "Agent principals"), h(Input, { id: "pmo-global-configuration-agents", value: globalAgents, placeholder: "agent:global/platform-admin", disabled: !globalPermissions.manage_agents || !!busy, onChange: function (event) { setGlobalAgents(event.target.value); } })),
              h("p", { className: "pmo-muted" }, "Only a human global administrator may change this allowlist."),
              h("div", { className: "pmo-actions" }, h(Button, { size: "sm", disabled: !globalPermissions.manage_agents || !!busy, onClick: saveGlobalAgents }, busy === "global-agents" ? "Saving…" : "Save configuration agents"))),
            oauthPanel
          ),
          h("div", { className: "pmo-messages" + (!messages.length ? " pmo-messages--empty" : ""), role: "log", "aria-live": "polite" }, messages.length ? messages.map(function (message) { return h("article", { key: message.id, className: "pmo-message" + (message.kind === "assistant" ? " pmo-message--agent" : "") }, h("div", { className: "pmo-message__head" }, h("span", { className: "pmo-message__author" }, message.author), h("time", { className: "pmo-message__time" }, formatPmTime(message.created_at))), h("div", { className: "pmo-message__body" }, h(MarkdownBlock, { source: message.body || "" }), Array.isArray(message.actions) && message.actions.length ? h("div", { className: "pmo-actions" }, message.actions.map(function (item) { return h(Button, { key: item.kind, size: "sm", onClick: function () { action(item); } }, item.label); })) : null)); }) : h("section", { className: "pmo-admin-empty" }, h("span", { className: "pmo-admin-empty__icon", "aria-hidden": "true" }, "AI"), h("div", null, h("strong", null, isGlobal ? "Operate the PM-OS control plane" : "Operate " + activeProject.name), h("p", null, isGlobal ? "Use this conversation for IAM, onboarding, and cross-project administration." : "Choose a project operation or describe the outcome you need. Changes stay inside this project.")), h("div", { className: "pmo-actions" }, isGlobal ? h(Button, { size: "sm", onClick: function () { props.navigate("onboarding"); } }, "Onboard a project") : null, isGlobal ? h(Button, { size: "sm", onClick: function () { props.navigate("access"); } }, "Manage access") : null, !isGlobal && permissions.manage_agents ? h(Button, { size: "sm", onClick: function () { action({ kind: "create_agent" }); } }, "Create project agent") : null, !isGlobal && permissions.manage_config ? h(Button, { size: "sm", onClick: function () { const node = document.getElementById("pmo-admin-runtime-skills"); if (node) node.focus(); } }, "Configure runtime") : null, !isGlobal && permissions.connect_provider ? h(Button, { size: "sm", onClick: connectCodex }, "Connect OpenAI Codex") : null))),
          error ? h("p", { className: "pmo-state pmo-state--error", role: "alert" }, error) : null,
          h("form", { className: "pmo-composer", onSubmit: send }, h("label", { className: "pmo-sr-only", htmlFor: "pmo-admin-message" }, "Administration message"), h("textarea", { id: "pmo-admin-message", value: draft, disabled: !!busy, placeholder: isGlobal ? "Ask about onboarding, IAM, or global Hermes operations…" : "Ask about agents, OpenAI Codex, model routing, or health…", onChange: function (event) { setDraft(event.target.value); } }), h(Button, { size: "sm", type: "submit", disabled: !!busy || !draft.trim() }, busy === "message" ? "Sending…" : "Send"))))));
  }

  function ApprovalsView(props) {
    const pair = usePmResource(withBoard(`${API}/approvals`, props.board), props.refreshKey);
    const [busy, setBusy] = useState(null);
    const [draft, setDraft] = useState(null);
    const [note, setNote] = useState("");
    const [decisionError, setDecisionError] = useState("");
    if (!pair[0].data) return h(PmState, { loading: pair[0].loading, error: pair[0].error, retry: pair[1] });
    // Mirrors RANK_ACTIONS["approval.escalate"] in plugins/pmo/access_policy.py.
    // The server is authoritative; this only decides whether to offer a control
    // that would certainly 403.
    const ESCALATE_MIN_RANK = 50;
    const canEscalate = (pair[0].data.actor_rank || 0) >= ESCALATE_MIN_RANK;
    const startDecision = function (item, decision) {
      setDraft({ approvalId: item.approval_id, decision: decision });
      setNote("");
      setDecisionError("");
    };
    const cancelDecision = function () { setDraft(null); setNote(""); setDecisionError(""); };
    // Requirement #8: "the CFO can ask for CEO approval here for tickets, if
    // he's requiring it."  Escalation is up-only and the reason is mandatory —
    // an unexplained escalation is noise to whoever receives it.  The server
    // owns both rules; this form only avoids offering a guaranteed failure.
    const startEscalation = function (item) {
      setDraft({
        approvalId: item.approval_id,
        mode: "escalate",
        toRank: String((item.required_rank || 0) + 10),
        toApprover: item.approver_profile || "",
        reason: "",
      });
      setDecisionError("");
    };
    const patchDraft = function (patch) { setDraft(function (prev) { return Object.assign({}, prev, patch); }); };
    const confirmEscalation = function (item) {
      if (!draft || draft.approvalId !== item.approval_id || draft.mode !== "escalate") return;
      setBusy(item.approval_id);
      setDecisionError("");
      SDK.fetchJSON(withBoard(`${API}/approvals/${encodeURIComponent(item.approval_id)}/escalation`, props.board), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          to_rank: Number(draft.toRank),
          to_approver_profile: draft.toApprover,
          reason: draft.reason,
        }),
      }).then(function () {
        props.announce(pt("announce.escalated")); cancelDecision(); return pair[1]();
      }).catch(function (error) {
        const message = parseApiErrorMessage(error);
        setDecisionError(message); props.announce(pt("states.error") + message);
      }).finally(function () { setBusy(null); });
    };
    const confirmDecision = function (item) {
      if (!draft || draft.approvalId !== item.approval_id) return;
      setBusy(item.approval_id);
      setDecisionError("");
      SDK.fetchJSON(withBoard(`${API}/approvals/${encodeURIComponent(item.approval_id)}/decision`, props.board), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: draft.decision, note: note }),
      }).then(function () {
        props.announce(pt("announce.decided")); cancelDecision(); return pair[1]();
      }).catch(function (error) {
        const message = parseApiErrorMessage(error);
        setDecisionError(message); props.announce(pt("states.error") + message);
      }).finally(function () { setBusy(null); });
    };
    return h("section", { className: "pmo-view", role: "region", "aria-labelledby": "pmo-approval-heading" },
      h("h2", { id: "pmo-approval-heading", tabIndex: -1 }, pt("approval.title")),
      pair[0].data.approvals.length ? h("div", { className: "pmo-card-list" }, pair[0].data.approvals.map(function (item) {
        const activeDraft = draft && draft.approvalId === item.approval_id && draft.mode !== "escalate";
        const activeEscalation = draft && draft.approvalId === item.approval_id && draft.mode === "escalate";
        const noteId = "pmo-approval-note-" + item.approval_id;
        const escalateId = "pmo-approval-escalate-" + item.approval_id;
        return h("article", { key: item.approval_id, className: "pmo-panel" },
          h("div", { className: "pmo-heading-row" }, h("div", null,
            h("h3", null, item.title), h("p", { className: "pmo-muted" }, item.detail)),
            h("span", { className: "pmo-status" }, item.status)),
          h("p", null, pt("approval.requires", item.required_rank)),
          h("div", { className: "pmo-actions" },
            h(Button, { size: "sm", disabled: !item.can_decide || busy === item.approval_id, title: item.can_decide ? undefined : pt("approval.requires", item.required_rank), onClick: function () { startDecision(item, "approved"); } }, pt("approval.approve")),
            h(Button, { size: "sm", disabled: !item.can_decide || busy === item.approval_id, title: item.can_decide ? undefined : pt("approval.requires", item.required_rank), onClick: function () { startDecision(item, "rejected"); } }, pt("approval.reject")),
            item.status === "pending" ? h(Button, {
              size: "sm",
              disabled: !canEscalate || busy === item.approval_id,
              title: canEscalate ? pt("approval.escalateHint") : pt("approval.needsRank", ESCALATE_MIN_RANK),
              onClick: function () { startEscalation(item); },
            }, pt("approval.escalate")) : null),
          activeEscalation ? h("div", { className: "pmo-decision-form", role: "group", "aria-labelledby": escalateId + "-label" },
            h("p", { id: escalateId + "-label", className: "pmo-muted" }, pt("approval.escalateHint")),
            h("label", { htmlFor: escalateId + "-rank" }, pt("approval.targetRank")),
            h(Input, { id: escalateId + "-rank", type: "number", min: 0, value: draft.toRank, autoFocus: true, onChange: function (event) { patchDraft({ toRank: event.target.value }); } }),
            h("label", { htmlFor: escalateId + "-approver" }, pt("approval.targetApprover")),
            h(Input, { id: escalateId + "-approver", value: draft.toApprover, placeholder: "human:ceo@example.com", onChange: function (event) { patchDraft({ toApprover: event.target.value }); } }),
            h("label", { htmlFor: escalateId + "-reason" }, pt("approval.escalateReason")),
            h("textarea", { id: escalateId + "-reason", rows: 3, value: draft.reason, onChange: function (event) { patchDraft({ reason: event.target.value }); } }),
            decisionError ? h("p", { className: "pmo-state pmo-state--error", role: "alert" }, pt("states.error"), decisionError) : null,
            h("div", { className: "pmo-actions" },
              h(Button, { size: "sm", disabled: busy === item.approval_id || !draft.reason.trim() || !draft.toApprover.trim(), onClick: function () { confirmEscalation(item); } }, pt("approval.confirmEscalate")),
              h(Button, { size: "sm", disabled: busy === item.approval_id, onClick: cancelDecision }, pt("approval.cancel")))) : null,
          activeDraft ? h("div", { className: "pmo-decision-form", role: "group", "aria-labelledby": noteId + "-label" },
            h("label", { id: noteId + "-label", htmlFor: noteId }, pt("approval.note")),
            h("textarea", { id: noteId, rows: 3, value: note, autoFocus: true, onChange: function (event) { setNote(event.target.value); } }),
            decisionError ? h("p", { className: "pmo-state pmo-state--error", role: "alert" }, pt("states.error"), decisionError) : null,
            h("div", { className: "pmo-actions" },
              h(Button, { size: "sm", disabled: busy === item.approval_id, onClick: function () { confirmDecision(item); } }, draft.decision === "approved" ? pt("approval.confirmApprove") : pt("approval.confirmReject")),
              h(Button, { size: "sm", disabled: busy === item.approval_id, onClick: cancelDecision }, pt("approval.cancel")))) : null);
      })) : h("p", { className: "pmo-muted" }, pt("approval.none")));
  }

  function TimelineView(props) {
    const [task, setTask] = useState(""); const [selected, setSelected] = useState(null);
    const pair = usePmResource(selected ? withBoard(`${API}/timeline/${encodeURIComponent(selected)}`, props.board) : null, props.refreshKey);
    return h("section", { className: "pmo-view", role: "region", "aria-labelledby": "pmo-timeline-heading" }, h("h2", { id: "pmo-timeline-heading", tabIndex: -1 }, pt("sections.timeline")), h("form", { className: "pmo-inline-form", onSubmit: function (event) { event.preventDefault(); setSelected(task.trim()); } }, h("label", { htmlFor: "pmo-timeline-task" }, pt("timeline.ticket")), h(Input, { id: "pmo-timeline-task", value: task, onChange: function (event) { setTask(event.target.value); } }), h(Button, { size: "sm", type: "submit", disabled: !task.trim() }, pt("timeline.load"))), !selected ? null : !pair[0].data ? h(PmState, { loading: pair[0].loading, error: pair[0].error, retry: pair[1] }) : pair[0].data.items.length ? h("ol", { className: "pmo-timeline" }, pair[0].data.items.map(function (item, index) { return h("li", { key: item.timestamp + item.kind + index }, h("time", { title: absolutePmTime(item.timestamp) }, formatPmTime(item.timestamp)), h("strong", null, item.kind), h("span", null, item.summary)); })) : h("p", { className: "pmo-muted" }, pt("timeline.none")));
  }

  function NotificationsView(props) {
    const [unread, setUnread] = useState(false); const pair = usePmResource(withBoard(`${API}/notifications?unread_only=${unread}`, props.board), props.refreshKey + String(unread));
    const mark = function (id) { SDK.fetchJSON(withBoard(`${API}/notifications/${id}/read`, props.board), { method: "POST" }).then(function () { props.announce(pt("announce.read")); pair[1](); }); };
    if (!pair[0].data) return h(PmState, { loading: pair[0].loading, error: pair[0].error, retry: pair[1] });
    return h("section", { className: "pmo-view", role: "region", "aria-labelledby": "pmo-notifications-heading" }, h("div", { className: "pmo-heading-row" }, h("h2", { id: "pmo-notifications-heading", tabIndex: -1 }, pt("sections.notifications")), h("label", { className: "pmo-check" }, h(Checkbox, { checked: unread, onCheckedChange: setUnread }), pt("notification.unread"))), pair[0].data.notifications.length ? h("div", { className: "pmo-card-list" }, pair[0].data.notifications.map(function (item) { return h("article", { key: item.id, className: "pmo-panel" }, h("div", { className: "pmo-heading-row" }, h("strong", null, item.author), h("span", { className: "pmo-status" }, item.urgency)), h("p", null, item.body), h("div", { className: "pmo-actions" }, h("a", { href: safePmoHref(item.link) }, item.task_id), !item.read ? h(Button, { size: "sm", onClick: function () { mark(item.id); } }, pt("notification.markRead")) : null)); })) : h("p", { className: "pmo-muted" }, pt("notification.none")));
  }

  function PortfolioAccessView(props) {
    const pair = usePmResource(`${API}/access/portfolio`, props.refreshKey); const data = pair[0].data;
    const [principal, setPrincipal] = useState(""); const [role, setRole] = useState("viewer"); const [projectId, setProjectId] = useState(""); const [passwordPrincipal, setPasswordPrincipal] = useState(""); const [passwordProject, setPasswordProject] = useState(""); const [password, setPassword] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
    if (!data) return h(PmState, { loading: pair[0].loading, error: pair[0].error, retry: pair[1] });
    const canonical = function (value) { const clean = String(value || "").trim(); return clean.startsWith("human:") || clean.startsWith("dashboard:") ? clean : clean.includes("@") ? "human:" + clean : clean; };
    const save = function (event) { event.preventDefault(); if (!principal.trim() || !projectId || busy) return; setBusy(true); setError(""); const project = (data.projects || []).find(function (item) { return item.project_id === projectId; }); const account = canonical(principal); SDK.fetchJSON(withBoard(`${API}/access/members`, project.board_slug), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ principal: account, role: role }) }).then(function () { if (!password.trim()) return null; return SDK.fetchJSON(withBoard(`${API}/access/password`, project.board_slug), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ principal: account, password: password }) }); }).then(function () { props.announce(pt("announce.saved")); setPrincipal(""); setPassword(""); return pair[1](); }).catch(function (err) { setError(parseApiErrorMessage(err)); }).finally(function () { setBusy(false); }); };
    const updatePassword = function (event) { event.preventDefault(); if (!passwordPrincipal.trim() || !passwordProject || password.length < 8 || busy) return; setBusy(true); setError(""); const project = (data.projects || []).find(function (item) { return item.project_id === passwordProject; }); SDK.fetchJSON(withBoard(`${API}/access/password`, project.board_slug), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ principal: canonical(passwordPrincipal), password: password }) }).then(function () { props.announce(pt("announce.saved")); setPasswordPrincipal(""); setPassword(""); return pair[1](); }).catch(function (err) { setError(parseApiErrorMessage(err)); }).finally(function () { setBusy(false); }); };
    const remove = function (member, project) { if (busy) return; setBusy(true); setError(""); SDK.fetchJSON(withBoard(`${API}/access/members/${encodeURIComponent(member)}`, project.board_slug), { method: "DELETE" }).then(function () { props.announce(pt("announce.saved")); return pair[1](); }).catch(function (err) { setError(parseApiErrorMessage(err)); }).finally(function () { setBusy(false); }); };
    const projects = data.projects || [];
    return h("section", { className: "pmo-view", role: "region", "aria-labelledby": "pmo-access-heading" },
      h("div", { className: "pmo-heading-row" }, h("div", null, h("h2", { id: "pmo-access-heading", tabIndex: -1 }, "Organization project access"), h("p", { className: "pmo-muted" }, "See which users can access which projects, and manage access one project at a time.")), h("span", { className: "pmo-status" }, data.actor.principal)),
      h("div", { className: "pmo-grid" },
        h("article", { className: "pmo-panel" }, h("h3", null, "Users and their projects"), data.users && data.users.length ? data.users.map(function (user) { return h("div", { key: user.principal, className: "pmo-row" }, h("strong", null, user.principal), h("span", null, user.projects.map(function (item) { return item.project_name + " (" + item.role + ")"; }).join(", "))); }) : h("p", { className: "pmo-muted" }, "No authorized projects found.")),
        h("article", { className: "pmo-panel" }, h("h3", null, "Project access"), projects.length ? projects.map(function (project) { return h("div", { key: project.project_id, className: "pmo-access-project" }, h("div", { className: "pmo-heading-row" }, h("strong", null, project.name), h("span", { className: "pmo-status" }, String(project.members.length) + " members")), project.members.map(function (member) { return h("div", { key: member.principal, className: "pmo-row" }, h("strong", null, member.principal), h("span", null, member.role), project.can_manage_members && member.principal !== data.actor.principal ? h(Button, { size: "sm", disabled: busy, onClick: function () { remove(member.principal, project); } }, "Remove") : null); })); }) : h("p", { className: "pmo-muted" }, "No authorized projects found.")),
        h("form", { className: "pmo-panel pmo-access-form", onSubmit: save }, h("h3", null, "Create project account"), h("p", { className: "pmo-muted" }, "Grant access and optionally set the project login password."), h("label", { htmlFor: "pmo-member-project" }, "Project"), h("select", { id: "pmo-member-project", value: projectId, onChange: function (event) { setProjectId(event.target.value); } }, h("option", { value: "" }, "Select a project"), projects.filter(function (item) { return item.can_manage_members; }).map(function (item) { return h("option", { key: item.project_id, value: item.project_id }, item.name); })), h("label", { htmlFor: "pmo-member-principal" }, "User email"), h(Input, { id: "pmo-member-principal", value: principal, placeholder: "name@example.com", autoComplete: "username", onChange: function (event) { setPrincipal(event.target.value); } }), h("label", { htmlFor: "pmo-member-role" }, "Role"), h("select", { id: "pmo-member-role", value: role, onChange: function (event) { setRole(event.target.value); } }, ["viewer", "contributor", "pm", "project_admin"].map(function (item) { return h("option", { key: item, value: item }, item); })), h("label", { htmlFor: "pmo-member-password" }, "Password (optional)"), h(Input, { id: "pmo-member-password", type: "password", value: password, placeholder: "At least 8 characters", autoComplete: "new-password", onChange: function (event) { setPassword(event.target.value); } }), h(Button, { size: "sm", type: "submit", disabled: busy || !principal.trim() || !projectId || (password && password.length < 8) }, "Create account")),
        h("form", { className: "pmo-panel pmo-access-form", onSubmit: updatePassword }, h("h3", null, "Update project password"), h("label", { htmlFor: "pmo-password-project" }, "Project"), h("select", { id: "pmo-password-project", value: passwordProject, onChange: function (event) { setPasswordProject(event.target.value); } }, h("option", { value: "" }, "Select a project"), projects.filter(function (item) { return item.can_manage_members; }).map(function (item) { return h("option", { key: item.project_id, value: item.project_id }, item.name); })), h("label", { htmlFor: "pmo-password-principal" }, "User email"), h(Input, { id: "pmo-password-principal", value: passwordPrincipal, placeholder: "name@example.com", autoComplete: "username", onChange: function (event) { setPasswordPrincipal(event.target.value); } }), h("label", { htmlFor: "pmo-password-value" }, "New password"), h(Input, { id: "pmo-password-value", type: "password", value: password, placeholder: "At least 8 characters", autoComplete: "new-password", onChange: function (event) { setPassword(event.target.value); } }), h(Button, { size: "sm", type: "submit", disabled: busy || !passwordPrincipal.trim() || !passwordProject || password.length < 8 }, "Save password"), error ? h("p", { className: "pmo-state pmo-state--error", role: "alert" }, error) : null)));
  }

  function AccessView(props) {
    return h(PortfolioAccessView, props);
  }

  function AccessViewLegacy(props) {
    const pair = usePmResource(withBoard(`${API}/access`, props.board), props.refreshKey); const data = pair[0].data;
    const [memberPrincipal, setMemberPrincipal] = useState(""); const [memberRole, setMemberRole] = useState("viewer"); const [memberPassword, setMemberPassword] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
    if (!data) return h(PmState, { loading: pair[0].loading, error: pair[0].error, retry: pair[1] });
    const canonicalPrincipal = function (value) { const clean = String(value || "").trim(); return clean.startsWith("human:") || clean.startsWith("dashboard:") ? clean : clean.includes("@") ? "human:" + clean : clean; };
    const saveMember = function (event) {
      event.preventDefault(); if (!memberPrincipal.trim() || busy) return; setBusy(true); setError("");
      const principal = canonicalPrincipal(memberPrincipal);
      SDK.fetchJSON(withBoard(`${API}/access/members`, props.board), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ principal: principal, role: memberRole }) }).then(function () {
        if (!memberPassword.trim()) return null;
        return SDK.fetchJSON(withBoard(`${API}/access/password`, props.board), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ principal: principal, password: memberPassword }) });
      }).then(function () { props.announce(pt("announce.saved")); setMemberPrincipal(""); setMemberPassword(""); return pair[1](); }).catch(function (err) { setError(parseApiErrorMessage(err)); }).finally(function () { setBusy(false); });
    };
    const removeMember = function (principal) {
      if (busy) return; setBusy(true); setError("");
      SDK.fetchJSON(withBoard(`${API}/access/members/${encodeURIComponent(principal)}`, props.board), { method: "DELETE" }).then(function () { props.announce(pt("announce.saved")); return pair[1](); }).catch(function (err) { setError(parseApiErrorMessage(err)); }).finally(function () { setBusy(false); });
    };
    const list = function (title, rows, first, second) { return h("article", { className: "pmo-panel" }, h("h3", null, title), rows.length ? rows.map(function (item, index) { return h("div", { key: String(item[first]) + index, className: "pmo-row" }, h("strong", null, item[first]), h("span", null, item[second])); }) : h(PmState, null)); };
    return h("section", { className: "pmo-view", role: "region", "aria-labelledby": "pmo-access-heading" },
      h("div", { className: "pmo-heading-row" }, h("div", null, h("h2", { id: "pmo-access-heading", tabIndex: -1 }, pt("sections.access")), h("p", { className: "pmo-muted" }, pt("access.manageHint"))), h("span", { className: "pmo-status" }, data.actor.project_role || pt("access.readOnly"))),
      h("div", { className: "pmo-grid" },
        h("article", { className: "pmo-panel" }, h("h3", null, pt("access.members")), h("p", { className: "pmo-muted" }, pt("access.actor"), ": ", data.actor.principal, data.actor.org_rank != null ? " · rank " + data.actor.org_rank : ""), data.members.length ? data.members.map(function (item, index) { const hasPassword = (data.credentialed_members || []).includes(item.principal); return h("div", { key: String(item.principal) + index, className: "pmo-row" }, h("strong", null, item.principal), h("span", null, item.role + " · " + (hasPassword ? pt("access.credentialed") : pt("access.noCredential"))), data.can_manage_members && item.principal !== data.actor.principal ? h(Button, { size: "sm", disabled: busy, onClick: function () { removeMember(item.principal); } }, pt("access.remove")) : null); }) : h(PmState, null), data.can_manage_members ? h("form", { className: "pmo-access-form", onSubmit: saveMember }, h("label", { htmlFor: "pmo-member-principal" }, pt("access.addMember")), h(Input, { id: "pmo-member-principal", value: memberPrincipal, placeholder: pt("access.memberPlaceholder"), autoComplete: "username", onChange: function (event) { setMemberPrincipal(event.target.value); } }), h("label", { htmlFor: "pmo-member-role" }, pt("access.roleHint")), h("select", { id: "pmo-member-role", value: memberRole, onChange: function (event) { setMemberRole(event.target.value); } }, ["viewer", "contributor", "pm", "project_admin"].map(function (role) { return h("option", { key: role, value: role }, role); })), h("label", { htmlFor: "pmo-member-password" }, pt("access.password")), h(Input, { id: "pmo-member-password", type: "password", value: memberPassword, placeholder: pt("access.passwordPlaceholder"), autoComplete: "new-password", onChange: function (event) { setMemberPassword(event.target.value); } }), h("p", { className: "pmo-muted" }, pt("access.passwordHint")), h(Button, { size: "sm", type: "submit", disabled: busy || !memberPrincipal.trim() }, pt("access.save"))) : h("p", { className: "pmo-muted" }, pt("access.readOnly")), error ? h("p", { className: "pmo-state pmo-state--error", role: "alert" }, error) : null),
        list(pt("access.ranks"), data.org_ranks, "principal", "rank"),
        list(pt("access.agents"), data.agents, "handle", "profile")));
  }

  function HumanTaskList(props) {
    // Requirement #5 is "query ... to delegate human tasks". Delegating with
    // no ledger leaves the manager holding a toast, so the list sits above
    // the form: what exists first, then the way to add to it.
    const pair = usePmResource(withBoard(`${API}/human-tasks`, props.board), props.refreshKey);
    const data = pair[0].data;
    if (!data) return h(PmState, { loading: pair[0].loading, error: pair[0].error, retry: pair[1] });
    const rows = data.tasks || [];
    if (!rows.length) {
      return h("p", { className: "pmo-muted" }, pt("humanTask.none"));
    }
    return h("table", { className: "pmo-table" },
      h("thead", null, h("tr", null,
        h("th", null, pt("humanTask.colTask")),
        h("th", null, pt("humanTask.colAssignee")),
        h("th", null, pt("humanTask.colStatus")))),
      h("tbody", null, rows.map(function (row) {
        return h("tr", { key: row.id },
          h("td", null, h("strong", null, row.title),
            h("span", { className: "pmo-muted pmo-mono" }, " ", row.id)),
          h("td", null, pmIdentity(row.assignee || "").name),
          h("td", null, h("span", { className: "pmo-status" },
            FALLBACK_COLUMN_LABEL[row.status] || row.status)));
      })));
  }

  function HumanTasksView(props) {
    const [title, setTitle] = useState(""); const [outcome, setOutcome] = useState(""); const [assignee, setAssignee] = useState("human:"); const [acceptance, setAcceptance] = useState(""); const [evidence, setEvidence] = useState(""); const [busy, setBusy] = useState(false); const [message, setMessage] = useState(""); const [error, setError] = useState("");
    const submit = function (event) { event.preventDefault(); if (busy || !title.trim() || !outcome.trim() || !assignee.trim() || !acceptance.trim() || !evidence.trim()) return; setBusy(true); setMessage(""); setError(""); SDK.fetchJSON(withBoard(`${API}/human-tasks`, props.board), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: title.trim(), outcome: outcome.trim(), assignee: assignee.trim(), acceptance_criteria: [acceptance.trim()], evidence: [evidence.trim()] }) }).then(function (result) { setMessage(pt("humanTask.created") + " · " + result.task_id); setTitle(""); setOutcome(""); setAcceptance(""); setEvidence(""); props.announce(pt("humanTask.created")); }).catch(function (err) { setError(parseApiErrorMessage(err)); }).finally(function () { setBusy(false); }); };
    return h("section", { className: "pmo-view", role: "region", "aria-labelledby": "pmo-human-task-heading" }, h("h2", { id: "pmo-human-task-heading", tabIndex: -1 }, pt("humanTask.title")), h("p", { className: "pmo-muted" }, pt("humanTask.note")), h("h3", null, pt("humanTask.delegated")), h(HumanTaskList, props), h("h3", null, pt("humanTask.title")), h("form", { className: "pmo-panel pmo-human-task-form", onSubmit: submit }, h("label", { htmlFor: "pmo-human-task-title" }, pt("humanTask.taskTitle")), h(Input, { id: "pmo-human-task-title", value: title, onChange: function (event) { setTitle(event.target.value); } }), h("label", { htmlFor: "pmo-human-task-outcome" }, pt("humanTask.outcome")), h("textarea", { id: "pmo-human-task-outcome", rows: 3, value: outcome, onChange: function (event) { setOutcome(event.target.value); } }), h("label", { htmlFor: "pmo-human-task-assignee" }, pt("humanTask.assignee")), h(Input, { id: "pmo-human-task-assignee", value: assignee, onChange: function (event) { setAssignee(event.target.value); } }), h("label", { htmlFor: "pmo-human-task-acceptance" }, pt("humanTask.acceptance")), h(Input, { id: "pmo-human-task-acceptance", value: acceptance, onChange: function (event) { setAcceptance(event.target.value); } }), h("label", { htmlFor: "pmo-human-task-evidence" }, pt("humanTask.evidence")), h(Input, { id: "pmo-human-task-evidence", value: evidence, onChange: function (event) { setEvidence(event.target.value); } }), error ? h("p", { className: "pmo-state pmo-state--error", role: "alert" }, error) : null, message ? h("p", { className: "pmo-state", role: "status" }, message) : null, h(Button, { size: "sm", type: "submit", disabled: busy || !title.trim() || !outcome.trim() || !assignee.trim() || !acceptance.trim() || !evidence.trim() }, pt("humanTask.create"))));
  }

  function DecisionsView(props) {
    const pair = usePmResource(withBoard(`${API}/decisions`, props.board), props.refreshKey); const data = pair[0].data;
    if (!data) return h(PmState, { loading: pair[0].loading, error: pair[0].error, retry: pair[1] });
    return h("section", { className: "pmo-view", role: "region", "aria-labelledby": "pmo-decisions-heading" }, h("h2", { id: "pmo-decisions-heading", tabIndex: -1 }, pt("sections.decisions")), h("div", { className: "pmo-grid" }, h("article", { className: "pmo-panel" }, h("h3", null, pt("decision.decisions")), data.decisions.map(function (item) { return h("div", { key: item.id, className: "pmo-row" }, h("strong", null, item.title), h("span", null, item.decision, " · ", item.status)); })), h("article", { className: "pmo-panel" }, h("h3", null, pt("decision.knowledge")), data.knowledge.map(function (item) { return h("div", { key: item.id, className: "pmo-row" }, h("strong", null, item.kind), h("span", null, item.body)); })), h("article", { className: "pmo-panel pmo-panel--wide" }, h("h3", null, pt("decision.context")), h("pre", { className: "pmo-context" }, data.context))));
  }

  function SpendView(props) {
    const pair = usePmResource(withBoard(`${API}/spend`, props.board), props.refreshKey); const data = pair[0].data;
    if (!data) return h(PmState, { loading: pair[0].loading, error: pair[0].error, retry: pair[1] });
    const models = Array.isArray(data.models) ? data.models : [];
    const modelRows = models.map(function (item) {
      return h("div", { className: "pmo-row", key: (item.provider || "") + ":" + item.model },
        h("div", null, h("strong", null, item.model), h("p", { className: "pmo-muted" }, item.provider || "Hermes")),
        h("div", { className: "pmo-spend-model-stats" },
          h("span", null, pt("spend.runs", item.runs)),
          h("span", null, pt("spend.input") + ": " + formatPmCount(item.input_tokens)),
          h("span", null, pt("spend.cached") + ": " + formatPmCount(item.cached_input_tokens)),
          h("span", null, pt("spend.output") + ": " + formatPmCount(item.output_tokens)),
          h("strong", null, formatPmMoney(item.cost_usd, data.currency))));
    });
    const agentCards = data.agents.map(function (item) {
      const tokens = item.input_tokens + item.cached_input_tokens + item.output_tokens;
      return h("article", { className: "pmo-panel", key: item.profile },
        h("div", { className: "pmo-heading-row" }, h("strong", null, "@", item.profile), h("span", null, formatPmMoney(item.cost_usd, data.currency))),
        h("p", { className: "pmo-muted" }, pt("spend.runs", item.runs), " · ", pt("spend.tokens", tokens)));
    });
    return h("section", { className: "pmo-view", role: "region", "aria-labelledby": "pmo-spend-heading" },
      h("h2", { id: "pmo-spend-heading", tabIndex: -1 }, pt("spend.title")),
      h("p", { className: "pmo-muted" }, pt("spend.note")),
      h("div", { className: "pmo-metrics" },
        h(PmMetric, { label: pt("metrics.spend"), value: formatPmMoney(data.cost_usd, data.currency) }),
        h(PmMetric, { label: pt("spend.perTicket"), value: formatPmMoney(data.cost_per_completed_ticket, data.currency) }),
        h(PmMetric, { label: pt("spend.input"), value: formatPmCount(data.input_tokens) }),
        h(PmMetric, { label: pt("spend.output"), value: formatPmCount(data.output_tokens) })),
      models.length ? h("article", { className: "pmo-panel pmo-panel--wide" }, h("h3", null, pt("spend.modelUsage")), h("div", { className: "pmo-card-list" }, modelRows)) : null,
      h("div", { className: "pmo-card-list" }, agentCards));
  }

  function PmoPage() {
    const keys = ["portfolio", "onboarding", "overview", "board", "founders", "administration", "approvals", "humanTasks", "timeline", "notifications", "access", "decisions", "spend"];
    const bootHash = pmParseHash();
    const [view, setView] = useState(bootHash && keys.indexOf(bootHash.view) >= 0 ? bootHash.view : "portfolio");
    const [board, setBoard] = useState(function () { return readSelectedBoard() || null; });
    const [portfolio, setPortfolio] = useState(null); const [refreshKey, setRefreshKey] = useState(0); const [announcement, setAnnouncement] = useState("");
    const accessPair = usePmResource(board ? withBoard(`${API}/access`, board) : null, refreshKey);
    const administrationPair = usePmResource(`${API}/administration`, refreshKey);
    const canViewAccess = Boolean(accessPair[0].data);
    const canViewAdministration = Boolean(administrationPair[0].data && administrationPair[0].data.can_access);
    const headingRef = useRef(null);
    useEffect(function () { function selected(event) { setBoard(event.detail && event.detail.board); setRefreshKey(function (value) { return value + 1; }); } window.addEventListener("pmo:board-selected", selected); return function () { window.removeEventListener("pmo:board-selected", selected); }; }, []);
    useEffect(function () { function requested(event) { const next = event.detail && event.detail.view; if (keys.indexOf(next) >= 0) { setView(next); window.history.replaceState(null, "", "#pmo/" + next); } } window.addEventListener("pmo:navigate", requested); return function () { window.removeEventListener("pmo:navigate", requested); }; }, []);
    // The hash was previously read once, at mount. Changing it on a page that
    // is already open — a ticket link in a Founder's Office message, a pasted
    // deep link, the back button — moved the URL and re-rendered nothing.
    // Declared after the pmo:navigate listener above so that listener is
    // already attached when applyHash runs on mount. navigate() uses
    // replaceState, which never fires hashchange, so this cannot loop.
    useEffect(function () {
      function applyHash() {
        const parsed = pmParseHash();
        if (!parsed || keys.indexOf(parsed.view) < 0) return;
        if (parsed.board && parsed.task) { pmNavigateToTicket(null, parsed.board, parsed.task); return; }
        if (parsed.board) { writeSelectedBoard(parsed.board); window.dispatchEvent(new CustomEvent("pmo:board-selected", { detail: { board: parsed.board } })); }
        setView(parsed.view);
      }
      applyHash();
      window.addEventListener("hashchange", applyHash);
      return function () { window.removeEventListener("hashchange", applyHash); };
    }, []);
    useEffect(function () { SDK.fetchJSON(`${API}/portfolio`).then(function (data) { rememberProjectBoards(data && data.projects); setPortfolio(data); }).catch(function () { setPortfolio(null); }); }, [refreshKey]);
    // The host dashboard's project_id is the authorization boundary.  A stale
    // PMO localStorage board must never override it after a page refresh or a
    // core-tab navigation.  Resolve that host project to its authoritative
    // board as soon as the visible portfolio arrives.
    useEffect(function () {
      if (!portfolio || !Array.isArray(portfolio.projects)) return;
      const requestedProjectId = new URLSearchParams(window.location.search).get("project_id");
      if (!requestedProjectId) return;
      const requestedProject = portfolio.projects.find(function (item) { return item.project_id === requestedProjectId; });
      if (!requestedProject || requestedProject.board_slug === board) return;
      writeSelectedBoard(requestedProject.board_slug);
      setBoard(requestedProject.board_slug);
      setRefreshKey(function (value) { return value + 1; });
    }, [portfolio, board]);
    useEffect(function () { const heading = document.querySelector(".pmo-content h2[tabindex='-1']"); if (heading) heading.focus(); }, [view]);
    const navigate = function (next) { setView(next); window.history.replaceState(null, "", "#pmo/" + next); };
    const effectiveView = view === "access" && !canViewAccess ? "portfolio" : (view === "administration" && !canViewAdministration ? "portfolio" : view);
    const selectedProject = portfolio && portfolio.projects.find(function (item) { return item.board_slug === board; });
    const switchProject = function (project, nextView) {
      const target = new URL(window.location.href);
      const currentProjectId = target.searchParams.get("project_id") || "";
      const currentProfile = target.searchParams.get("profile") || "";
      if (project.project_id && (currentProjectId !== project.project_id || (project.pm_profile && currentProfile !== project.pm_profile))) {
        writeSelectedBoard(project.board_slug);
        target.searchParams.set("project_id", project.project_id);
        if (project.pm_profile) target.searchParams.set("profile", project.pm_profile);
        target.hash = "#pmo/" + (nextView || view);
        window.location.assign(target.pathname + target.search + target.hash);
        return true;
      }
      writeSelectedBoard(project.board_slug); setBoard(project.board_slug); setRefreshKey(function (value) { return value + 1; });
      return false;
    };
    const openProject = function (project) { if (!switchProject(project, "overview")) navigate("overview"); };
    const openOnboardedProject = function (result) {
      if (!switchProject(result, "overview")) navigate("overview");
    };
    let content;
    const shared = { board: board, project: selectedProject, projects: portfolio && portfolio.projects || [], openProject: openProject, switchProject: switchProject, canViewAccess: canViewAccess, refreshKey: refreshKey, navigate: navigate, announce: setAnnouncement };
    if (effectiveView === "portfolio") content = h(PortfolioView, Object.assign({}, shared, { openProject: openProject }));
    else if (effectiveView === "onboarding") content = h(ProjectOnboardingView, Object.assign({}, shared, { onComplete: openOnboardedProject }));
    else if (effectiveView === "overview") content = h(OverviewView, shared);
    else if (effectiveView === "board") content = h(KanbanPage, shared);
    else if (effectiveView === "founders") content = h(FounderOfficeView, shared);
    else if (effectiveView === "administration") content = h(AdministrationView, shared);
    else if (effectiveView === "approvals") content = h(ApprovalsView, shared);
    else if (effectiveView === "humanTasks") content = h(HumanTasksView, shared);
    else if (effectiveView === "timeline") content = h(TimelineView, shared);
    else if (effectiveView === "notifications") content = h(NotificationsView, shared);
    else if (effectiveView === "access") content = h(AccessView, shared);
    else if (effectiveView === "decisions") content = h(DecisionsView, shared);
    else content = h(SpendView, shared);
    return h(ErrorBoundary, null, h("div", { className: "pmo-shell" },
      h("header", { className: "pmo-header" }, h("div", null, h("h1", null, pt("appName")), h("p", null, pt("subtitle"))), h(Button, { size: "sm", onClick: function () { setRefreshKey(function (value) { return value + 1; }); } }, pt("actions.refresh"))),
      h("nav", { className: "pmo-nav", "aria-label": pt("appName") }, keys.filter(function (key) { return (key !== "access" || canViewAccess) && (key !== "administration" || canViewAdministration); }).map(function (key) { return h("button", { key: key, type: "button", "aria-current": effectiveView === key ? "page" : undefined, onClick: function () { navigate(key); } }, pt("sections." + key)); })),
      h("div", { ref: headingRef, className: "pmo-content" },
        ["portfolio", "onboarding", "access", "administration"].indexOf(effectiveView) < 0 ? h(ProjectContextBar, shared) : null,
        content),
      h("div", { className: "pmo-sr-only", role: "status", "aria-live": "polite", "aria-atomic": "true" }, announcement)));
  }

  // PMO-DASHBOARD-WRAPPER:START
  // -------------------------------------------------------------------------
  // Datansh Hermes wrapper chrome.  This deliberately lives in the plugin
  // bundle and uses the host's public slot API; the upstream Hermes sidebar
  // implementation stays untouched and remains easy to sync.
  // -------------------------------------------------------------------------
  const WRAPPER_API = "/api/plugins/pmo";
  const WRAPPER_STYLE_ID = "datansh-hermes-wrapper-style";

  function wrapperRolePolicy() {
    const selected = readSelectedBoard();
    const portfolioUrl = `${WRAPPER_API}/portfolio`;
    return SDK.fetchJSON(portfolioUrl).then(function (portfolio) {
      const projects = Array.isArray(portfolio && portfolio.projects) ? portfolio.projects : [];
      const board = selected || (projects[0] && projects[0].board_slug);
      if (!board) {
        return { role: "manager", allowed_paths: ["/pmo"], projects: projects, selected_board: "", loading: false };
      }
      return SDK.fetchJSON(`${WRAPPER_API}/tab-policy?board=${encodeURIComponent(board)}`).then(function (policy) {
        return Object.assign({}, policy, { projects: projects, selected_board: board });
      }).catch(function () {
        // A temporary policy request failure must not remove the upstream
        // Hermes work surface.  Preserve the discovered project context and
        // let Hermes' existing server-side authorization remain authoritative
        // until this additive navigation hint can be refreshed.
        return { role: "manager", allowed_paths: [], projects: projects, selected_board: board, unrestricted: true };
      });
    });
  }

  function wrapperCss() {
    if (document.getElementById(WRAPPER_STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = WRAPPER_STYLE_ID;
    style.textContent = `
      #app-sidebar > div:first-child > div > [data-datansh-hermes-wordmark] ~ * {
        display: none !important;
      }
      [data-datansh-hermes-wordmark] {
        color: var(--midground, currentColor);
        font-family: var(--font-sans, ui-sans-serif, system-ui, sans-serif);
        font-size: 1.02rem;
        font-weight: 800;
        letter-spacing: .035em;
        line-height: .95;
        white-space: nowrap;
      }
      [data-datansh-hermes-persona] {
        border: 1px solid color-mix(in srgb, currentColor 25%, transparent);
        border-radius: 999px;
        font-size: .62rem;
        letter-spacing: .08em;
        line-height: 1;
        padding: .28rem .48rem;
        text-transform: uppercase;
        white-space: nowrap;
      }
      [data-datansh-hermes-project-context] {
        align-items: center;
        display: inline-flex;
        gap: .35rem;
        margin-left: .55rem;
        max-width: min(30vw, 280px);
      }
      [data-datansh-hermes-project-context] label {
        color: var(--muted-foreground, currentColor);
        font-size: .62rem;
        font-weight: 750;
        letter-spacing: .06em;
        text-transform: uppercase;
      }
      [data-datansh-hermes-project-context] select {
        background: var(--background, transparent);
        border: 1px solid color-mix(in srgb, currentColor 26%, transparent);
        border-radius: .4rem;
        color: inherit;
        font: inherit;
        font-size: .75rem;
        min-height: 30px;
        max-width: 210px;
        padding: .28rem .42rem;
      }
      @media (max-width: 1023px) {
        [data-datansh-hermes-wordmark] { font-size: .95rem; }
        [data-datansh-hermes-project-context] { max-width: 46vw; }
        [data-datansh-hermes-project-context] label { display: none; }
      }
    `;
    document.head.appendChild(style);
  }

  function applyWrapperNav(policy) {
    if (!policy || !Array.isArray(policy.allowed_paths)) return;
    if (policy.unrestricted) return;
    const allowed = new Set(policy.allowed_paths);
    const links = Array.prototype.slice.call(document.querySelectorAll("#app-sidebar nav a"));
    links.forEach(function (link) {
      let path = "";
      try { path = new URL(link.getAttribute("href") || "", window.location.origin).pathname; } catch (_e) { return; }
      const visible = allowed.has(path);
      link.hidden = !visible;
      link.setAttribute("aria-hidden", visible ? "false" : "true");
    });
    Array.prototype.slice.call(document.querySelectorAll("#app-sidebar nav [role='group']")).forEach(function (group) {
      const visibleLink = group.querySelector("a:not([hidden])");
      group.hidden = !visibleLink;
    });
    document.documentElement.dataset.datanshHermesRole = String(policy.role || "manager");
    document.documentElement.dataset.datanshHermesPolicyReady = "true";
    const currentPath = window.location.pathname.replace(/\/$/, "") || "/";
    if (currentPath !== "/pmo" && !allowed.has(currentPath)) {
      window.location.replace("/pmo");
    }
  }

  function replaceMobileBrand() {
    const headers = Array.prototype.slice.call(document.querySelectorAll("header"));
    const mobile = headers.find(function (header) {
      return !!header.querySelector("button[aria-label*='navigation']");
    });
    if (!mobile) return;
    const brand = Array.prototype.slice.call(mobile.children).find(function (child) {
      return child.tagName !== "BUTTON";
    });
    if (brand && brand.textContent !== "DATANSH\\nHERMES") brand.textContent = "DATANSH\\nHERMES";
  }

  function DatanshHermesShell() {
    const [policy, setPolicy] = useState({ role: "manager", allowed_paths: ["/pmo"], loading: true });
    useEffect(function () {
      wrapperCss();
      let alive = true;
      let observer = null;
      let timer = null;
      const load = function () {
        wrapperRolePolicy().then(function (next) {
          if (alive) setPolicy(Object.assign({}, next, { loading: false }));
        }).catch(function () {
          // Never hide inherited Hermes capabilities because an optional PMO
          // navigation hint is temporarily unavailable. Server-side Hermes
          // authentication and PMO authorization remain the authority.
          if (alive) setPolicy({ role: "manager", allowed_paths: [], projects: [], selected_board: "", unrestricted: true, loading: false });
        });
      };
      const selected = function () { load(); };
      window.addEventListener("pmo:board-selected", selected);
      load();
      return function () {
        alive = false;
        window.removeEventListener("pmo:board-selected", selected);
        if (observer) observer.disconnect();
        if (timer) window.clearInterval(timer);
      };
    }, []);
    useEffect(function () {
      if (policy.loading) return undefined;
      const apply = function () { applyWrapperNav(policy); replaceMobileBrand(); };
      apply();
      const observer = new MutationObserver(apply);
      observer.observe(document.body, { childList: true, subtree: true });
      const timer = window.setInterval(apply, 500);
      return function () {
        observer.disconnect();
        window.clearInterval(timer);
      };
    }, [policy]);
    useEffect(function () {
      const wordmark = document.querySelector("[data-datansh-hermes-wordmark]");
      if (wordmark) {
        wordmark.setAttribute("aria-label", "DATANSH HERMES");
        wordmark.setAttribute("title", "DATANSH HERMES");
      }
    }, [policy]);
    const projects = Array.isArray(policy.projects) ? policy.projects : [];
    const selectedBoard = policy.selected_board || readSelectedBoard() || (projects[0] && projects[0].board_slug) || "";
    return h(React.Fragment, null,
      h("span", {
      "data-datansh-hermes-wordmark": "true",
      "aria-label": "Datansh Hermes · " + policy.role,
      title: "Datansh Hermes · " + policy.role,
      }, "DATANSH", h("br"), "HERMES"),
      projects.length ? h("span", { "data-datansh-hermes-project-context": "true" },
        h("label", { htmlFor: "datansh-hermes-global-project-switch" }, "Project"),
        h("select", { id: "datansh-hermes-global-project-switch", value: selectedBoard, "aria-label": "Switch project context", onChange: function (event) {
          const next = projects.find(function (project) { return project.board_slug === event.target.value; });
          if (next) writeSelectedBoard(next.board_slug);
        } }, projects.map(function (project) { return h("option", { key: project.project_id, value: project.board_slug }, project.name); }))) : null);
  }
  // PMO-DASHBOARD-WRAPPER:END
  if (window.__HERMES_TEST__) {
    window.__PMO_UI_TEST__ = Object.freeze({
      MentionComposer: MentionComposer,
      FounderOfficeView: FounderOfficeView,
      AgentActivity: AgentActivity,
      SpecialistSubChat: SpecialistSubChat,
      conversationFolders: pmConversationFolders,
      chatStream: pmChatStream,
      chatScrollTarget: pmChatScrollTarget,
      identity: pmIdentity,
      mentionRoster: pmMentionRoster,
      allowedChatParticipant: pmAllowedChatParticipant,
      PmoPage: PmoPage,
      TaskCard: TaskCard,
      TaskDrawer: TaskDrawer,
      Column: Column,
      formatMoney: formatPmMoney,
      pageColumnTasks: pageColumnTasks,
      safeHref: safePmoHref,
      ticketHref: pmTicketHref,
      linkedMarkdown: pmLinkedMarkdown,
      workflowEnvelope: pmWorkflowEnvelope,
      workflowVisibleBody: pmWorkflowVisibleBody,
      workflowResponses: pmWorkflowResponses,
      workflowSubmittedAnswer: pmWorkflowSubmittedAnswer,
      FounderWorkflowCard: FounderWorkflowCard,
      activityPreviews: pmActivityPreviews,
      messagePreviews: pmMessagePreviews,
      repositoryList: pmRepositoryList,
      rememberProjectBoards: rememberProjectBoards,
      withBoard: withBoard,
      repositoryRequest: pmRepositoryRequest,
      githubInstallations: pmGithubInstallations,
      githubRepositories: pmGithubRepositories,
      githubRequest: pmGithubRequest,
      onboardingRequest: pmOnboardingRequest,
      onboardingRepositories: pmOnboardingRepositories,
      projectSlug: pmProjectSlug,
      onboardingErrorMessage: pmOnboardingErrorMessage,
      RepositoryCard: RepositoryCard,
      GithubRepositoryComposer: GithubRepositoryComposer,
      RepositoryPanel: RepositoryPanel,
      ProjectOnboardingView: ProjectOnboardingView,
      strings: PM_STRINGS.en,
    });
  }
  // PMO-DASHBOARD-EXTENSION:END

  // -------------------------------------------------------------------------
  // Register
  // -------------------------------------------------------------------------

  if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === "function") {
    window.__HERMES_PLUGINS__.register("pmo", PmoPage);
    if (typeof window.__HERMES_PLUGINS__.registerSlot === "function") {
      window.__HERMES_PLUGINS__.registerSlot("pmo", "header-left", DatanshHermesShell);
    }
  }
})();
