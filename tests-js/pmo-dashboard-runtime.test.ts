import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'

import { test } from 'vitest'

type ElementNode = {
  type: unknown
  props: Record<string, unknown> & {children?: unknown[]}
}

function runtime() {
  const registered: {name?: string; component?: () => ElementNode} = {}
  class Component {
    props: Record<string, unknown>
    state: Record<string, unknown> = {}

    constructor(props: Record<string, unknown>) { this.props = props }
    setState(value: Record<string, unknown>) { this.state = {...this.state, ...value} }
  }
  const React = {
    Component,
    Fragment: Symbol('Fragment'),
    createElement(type: unknown, props: Record<string, unknown> | null, ...children: unknown[]): ElementNode {
      return {type, props: {...(props || {}), children}}
    },
  }
  const noopComponent = 'control'
  const context = {
    console,
    CustomEvent: class {detail: unknown; constructor(_name: string, init: {detail: unknown}) { this.detail = init.detail }},
    document: {
      activeElement: null,
      contains: () => false,
      documentElement: {lang: 'en'},
      querySelector: () => null,
    },
    navigator: {language: 'en-US'},
    setTimeout,
    clearTimeout,
    URLSearchParams,
    window: {
      __HERMES_TEST__: true,
      __HERMES_PLUGIN_SDK__: {
        React,
        components: {
          Card: noopComponent, CardContent: noopComponent, Badge: noopComponent,
          Button: noopComponent, Input: noopComponent, Label: noopComponent,
          Select: noopComponent, SelectOption: noopComponent, Checkbox: noopComponent,
        },
        hooks: {
          useState(initial: unknown) {
            return [typeof initial === 'function' ? (initial as () => unknown)() : initial, () => undefined]
          },
          useEffect: () => undefined,
          useCallback: (fn: unknown) => fn,
          useMemo: (fn: () => unknown) => fn(),
          useRef: (value: unknown) => ({current: value}),
        },
        utils: {cn: (...parts: string[]) => parts.filter(Boolean).join(' '), timeAgo: () => ''},
        fetchJSON: () => Promise.resolve({}),
      },
      __HERMES_PLUGINS__: {
        register(name: string, component: () => ElementNode) {
          registered.name = name
          registered.component = component
        },
      },
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => true,
      history: {replaceState: () => undefined},
      localStorage: {getItem: () => null, setItem: () => undefined, removeItem: () => undefined},
      location: {hash: ''},
      requestAnimationFrame: (fn: () => void) => fn(),
    },
  }
  const source = fs.readFileSync(
    path.resolve(__dirname, '../plugins/pmo/dashboard/dist/index.js'), 'utf8',
  )
  vm.runInNewContext(source, context, {filename: 'pmo-dashboard.js'})
  return {
    registered,
    testApi: (context.window as typeof context.window & {__PMO_UI_TEST__: Record<string, unknown>}).__PMO_UI_TEST__,
  }
}

function walk(value: unknown, found: ElementNode[] = []): ElementNode[] {
  if (!value || typeof value !== 'object') {return found}
  const node = value as ElementNode
  if ('type' in node && node.props) {found.push(node)}
  for (const child of node.props?.children || []) {
    if (Array.isArray(child)) {child.forEach(item => walk(item, found))} else {walk(child, found)}
  }
  return found
}

test('registers an independent PMO shell with keyboard-native section navigation and a polite live region', () => {
  const loaded = runtime()
  assert.equal(loaded.registered.name, 'pmo')
  assert.ok(loaded.registered.component)
  const nodes = walk(loaded.registered.component!())
  const nav = nodes.find(node => node.type === 'nav' && node.props.className === 'pmo-nav')
  assert.ok(nav)
  const navButtons = walk(nav).filter(node => node.type === 'button')
  assert.equal(navButtons.length, 11)
  assert.ok(navButtons.some(node => node.props.children?.[0] === 'Project onboarding'))
  const live = nodes.find(node => node.props['aria-live'] === 'polite')
  assert.equal(live?.props.role, 'status')
})

test('unconfigured project onboarding offers a visible GitHub connection action', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '../plugins/pmo/dashboard/dist/index.js'), 'utf8',
  )
  assert.match(source, /pmo-github-unconfigured[\s\S]*?onClick: connectGithub/)
  assert.match(source, /Repository consent will start here once the Datansh GitHub App is configured/)
  assert.doesNotMatch(source, /github\.com\/settings\/apps\/new/)
})

test('ticket deletion stays bound to the dashboard-selected project board', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '../plugins/pmo/dashboard/dist/index.js'), 'utf8',
  )
  assert.match(source, /withBoard\(`\$\{API\}\/tasks\/\$\{encodeURIComponent\(taskId\)\}`, board\)/)
  assert.match(source, /withBoard\(`\$\{API\}\/tasks\/\$\{encodeURIComponent\(id\)\}`, board\)/)
})

test('board requests carry the project bound by the portfolio', () => {
  const loaded = runtime()
  const remember = loaded.testApi.rememberProjectBoards as (
    projects: Array<{project_id: string; board_slug: string}>,
  ) => void
  const withBoard = loaded.testApi.withBoard as (url: string, board: string) => string

  remember([{project_id: 'project-target', board_slug: 'target-board'}])

  assert.equal(
    withBoard('/api/plugins/pmo/tasks?unread=1', 'target-board'),
    '/api/plugins/pmo/tasks?unread=1&board=target-board&project_id=project-target',
  )
})

test('administration exposes project credential precedence and removal controls', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '../plugins/pmo/dashboard/dist/index.js'), 'utf8',
  )
  assert.match(source, /Project AI credentials/)
  assert.match(source, /Project-specific/)
  assert.match(source, /Global fallback/)
  assert.match(source, /Use global instead/)
  assert.match(source, /credentials\/\$\{encodeURIComponent\(handle\)\}\/openai-codex/)
  assert.doesNotMatch(source, /placeholder: "openai-codex-" \+ activeProject\.slug/)
})

test('administration exposes project-scoped runtime configuration controls', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '../plugins/pmo/dashboard/dist/index.js'), 'utf8',
  )
  assert.match(source, /Project runtime/)
  assert.match(source, /Default skills/)
  assert.match(source, /Allowed plugins/)
  assert.match(source, /Delivery channels/)
  assert.match(source, /administration\/projects\/\$\{encodeURIComponent\(activeProject\.project_id\)\}\/runtime/)
  assert.match(source, /does not install plugins or enable channels globally/)
})

test('project-scoped screens and configuration expose a consistent project selector', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '../plugins/pmo/dashboard/dist/index.js'), 'utf8',
  )
  const style = fs.readFileSync(
    path.resolve(__dirname, '../plugins/pmo/dashboard/dist/style.css'), 'utf8',
  )
  assert.match(source, /function ProjectContextBar\(props\)/)
  assert.match(source, /id: "pmo-global-project-switch"/)
  assert.match(source, /id: "pmo-administration-project-switch"/)
  assert.match(source, /All actions stay within the selected project/)
  assert.match(style, /\.pmo-project-context \{/)
})

test('PMO board selection follows the host project id and carries its PM profile', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '../plugins/pmo/dashboard/dist/index.js'), 'utf8',
  )
  assert.match(source, /new URLSearchParams\(window\.location\.search\)\.get\("project_id"\)/)
  assert.match(source, /item\.project_id === requestedProjectId/)
  assert.match(source, /target\.searchParams\.set\("project_id", project\.project_id\)/)
  assert.match(source, /target\.searchParams\.set\("profile", project\.pm_profile\)/)
})

test('the shared Hermes shell exposes project context beyond PM-OS routes', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '../plugins/pmo/dashboard/dist/index.js'), 'utf8',
  )
  assert.match(source, /data-datansh-hermes-project-context/)
  assert.match(source, /id: "datansh-hermes-global-project-switch"/)
  assert.match(source, /projects: projects, selected_board: board/)
  assert.match(source, /if \(next\) writeSelectedBoard\(next\.board_slug\)/)
})

test('administration exposes global defaults and explains their project composition', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '../plugins/pmo/dashboard/dist/index.js'), 'utf8',
  )
  assert.match(source, /Global PMO defaults/)
  assert.match(source, /Save global defaults/)
  assert.match(source, /administration\/global\/configuration/)
  assert.match(source, /Global defaults are combined with each project/)
  assert.match(source, /duplicates are removed/)
})

test('administration keeps its control rail beside a compact, actionable empty conversation', () => {
  const root = path.resolve(__dirname, '../plugins/pmo/dashboard/dist')
  const source = fs.readFileSync(path.join(root, 'index.js'), 'utf8')
  const style = fs.readFileSync(path.join(root, 'style.css'), 'utf8')
  assert.match(source, /pmo-messages--empty/)
  assert.match(source, /pmo-admin-empty/)
  assert.match(source, /Create project agent/)
  assert.match(source, /Configure runtime/)
  assert.match(style, /\.pmo-thread\.pmo-admin-thread \{ display: grid; \}/)
  assert.match(style, /grid-template-rows: auto minmax\(220px, 35vh\) auto auto/)
  assert.match(style, /\.pmo-thread\.pmo-admin-thread \{\s*display: flex/)
  assert.match(style, /\.pmo-chat-view \.pmo-chat-sidebar \{ max-height: 34vh; \}/)
})

test('mention composer exposes the WAI-ARIA combobox keyboard surface', () => {
  const loaded = runtime()
  const MentionComposer = loaded.testApi.MentionComposer as (props: Record<string, unknown>) => ElementNode
  const nodes = walk(MentionComposer({id: 'composer', board: 'acme', busy: false, send: () => Promise.resolve()}))
  const input = nodes.find(node => node.type === 'textarea')
  assert.equal(input?.props.role, 'combobox')
  assert.equal(input?.props['aria-autocomplete'], 'list')
  assert.equal(input?.props['aria-expanded'], false)
  assert.equal(typeof input?.props.onKeyDown, 'function')
})

test('founder office normalizes global and multiple project conversations into separate folders', () => {
  const loaded = runtime()
  const foldersFor = loaded.testApi.conversationFolders as (
    payload: Record<string, unknown>, projects: Array<Record<string, unknown>>,
  ) => Array<{scope: string; project_id?: string; conversations: Array<Record<string, unknown>>}>
  const folders = foldersFor({global: {
    conversations: [
      {thread_id: 'global', kind: 'global_founders_office', title: 'Global Founders Office'},
      {thread_id: 'a'.repeat(32), kind: 'global_founders_office', title: 'Architecture'},
    ],
    participants: [
      {handle: 'pm', kind: 'agent'},
      {handle: 'dev-1', kind: 'agent'},
      {handle: 'ceo', kind: 'human'},
    ],
  }, folders: [{
    project_id: 'p1', name: 'Alpha', board_slug: 'alpha', pm_profile: 'luna-alpha',
    conversations: [
      {thread_id: 'a1', kind: 'founders_office', title: 'Launch'},
      {thread_id: 'a2', kind: 'founders_office', title: 'Pricing'},
    ],
  }, {
    project_id: 'p2', name: 'Beta', board_slug: 'beta', pm_profile: 'luna-beta',
    conversations: [{thread_id: 'b1', kind: 'founders_office', title: 'Delivery'}],
  }]}, [])
  assert.equal(folders[0].scope, 'global')
  assert.equal(folders[0].conversations.length, 2)
  assert.equal(folders[0].conversations[1].thread_id, 'a'.repeat(32))
  assert.equal((folders[0].conversations[0].participants as Array<Record<string, unknown>>).length, 3)
  assert.equal(folders[1].project_id, 'p1')
  assert.equal(folders[1].conversations.length, 2)
  assert.equal(folders[2].conversations[0].thread_id, 'b1')
})

test('Founder participant policy keeps developer agents available', () => {
  const loaded = runtime()
  const allowed = loaded.testApi.allowedChatParticipant as (
    item: Record<string, unknown>, clientScope: boolean,
  ) => boolean
  assert.equal(allowed({handle: 'pm', kind: 'agent'}, false), true)
  assert.equal(allowed({handle: 'dev-1', kind: 'agent'}, false), true)
  assert.equal(allowed({handle: 'qa', kind: 'agent'}, false), true)
  assert.equal(allowed({handle: 'ceo', kind: 'human'}, false), true)
  assert.equal(allowed({handle: 'client', kind: 'human'}, false), false)
  assert.equal(allowed({handle: 'client', kind: 'human'}, true), true)
})

test('global mention roster deduplicates project agents by addressable handle', () => {
  const loaded = runtime()
  const roster = loaded.testApi.mentionRoster as (
    responses: Array<Record<string, unknown>>,
    folders: Array<Record<string, unknown>>,
    globalScope: boolean,
  ) => Array<{handle: string; kind: string; mentionable?: boolean}>
  const items = roster([{items: [
    {handle: 'pm', kind: 'agent', role: 'All project managers'},
    {handle: 'dev-1', kind: 'agent', role: 'Engineer · Alpha'},
    {handle: 'dev-1', kind: 'agent', role: 'Engineer · Beta'},
    {handle: 'ceo', kind: 'human', role: 'CEO'},
  ]}], [{scope: 'project', project_id: 'alpha', name: 'Alpha', pm_profile: 'pm-alpha'}], true)
  assert.equal(items.map(item => item.handle).join(','), 'dev-1,pm,pm-alpha,ceo')
  assert.notEqual(items.find(item => item.handle === 'pm-alpha')?.mentionable, false)
})

test('chat stream exposes thinking, tool-call and status events alongside messages', () => {
  const loaded = runtime()
  const chatStream = loaded.testApi.chatStream as (
    messages: Array<Record<string, unknown>>, detail: Record<string, unknown>,
  ) => Array<{streamKind: string; record: Record<string, unknown>}>
  const stream = chatStream([
    {id: 1, author: 'user:human:ceo@example.com', body: 'Start', created_at: 1},
    {id: 2, activity_type: 'thinking', status: 'active', details: 'Reviewing context', created_at: 2},
  ], {activities: [{id: 3, type: 'tool_call', tool_name: 'pmo_ticket_create', status: 'completed', created_at: 3}]})
  assert.equal(stream[0].streamKind, 'message')
  assert.equal(stream[1].record.type, 'thinking')
  assert.equal(stream[2].record.type, 'tool_call')
  assert.equal(stream[2].record.toolName, 'pmo_ticket_create')

  const live = chatStream([], {agent_activity: {
    state: 'working', profile: 'pm-alpha', session_id: 's1', api_calls: 2, tool_calls: 1,
    last_activity: 4,
    events: [{id: 'think-1', type: 'thinking', status: 'completed', summary: 'Reading project context', timestamp: 3}],
  }})
  assert.equal(live.length, 2)
  assert.equal(live[0].record.type, 'thinking')
  assert.equal(live[1].record.type, 'working')
  assert.match(String(live[1].record.detail), /pm-alpha/)
})

test('Founder chat preserves an intentional reading position and follows only from the bottom', () => {
  const loaded = runtime()
  const target = loaded.testApi.chatScrollTarget as (
    saved: {top: number; atBottom: boolean} | undefined, maxScroll: number,
  ) => number

  assert.equal(target(undefined, 1200), 1200)
  assert.equal(target({top: 340, atBottom: false}, 1400), 340)
  assert.equal(target({top: 340, atBottom: false}, 200), 200)
  assert.equal(target({top: 1190, atBottom: true}, 1500), 1500)
})

test('specialist consultations render as one nested run without leaking markers or tool calls', () => {
  const loaded = runtime()
  const chatStream = loaded.testApi.chatStream as (
    messages: Array<Record<string, unknown>>, detail: Record<string, unknown>,
  ) => Array<{streamKind: string; record: Record<string, any>}>
  const consult = '[pmo:founder-consult:v1]\n' + JSON.stringify({
    plan_id: '177', consultation_thread_id: 'child-1', handle: 'dev-1',
  }) + '\n\nOpened specialist sub-chat.'
  const response = '[pmo:founder-consult-response:v1]\n' + JSON.stringify({
    plan_id: '177', consultation_thread_id: 'child-1', handle: 'dev-1',
  }) + '\n\nSpecialist replied.'
  const stream = chatStream([
    {id: 1, author: 'human:ceo@example.com', body: '@pm review this', created_at: 1},
    {id: 2, author: 'agent:pm-alpha', body: consult, created_at: 2},
    {id: 3, author: 'agent:dev-alpha-1', body: response, created_at: 4},
  ], {
    subchats: [{
      id: 'child-1', thread_id: 'child-1', plan_id: '177', handle: 'dev-1',
      question: 'Review the contract', status: 'completed', created_at: 2,
      result: 'Use the confirmed mobile contract.',
      agent_activity: {events: [{id: 'child-tool', type: 'tool_call', tool_name: 'read_file', status: 'completed', timestamp: 3}]},
    }],
    agent_activity: {state: 'idle', events: [
      {id: 'describe-think', source_message_id: 'assistant-0', type: 'thinking', status: 'completed', summary: 'Preparing delegation', timestamp: 1.8},
      {id: 'describe-consult', source_message_id: 'assistant-0', type: 'tool_call', tool_name: 'tool_describe', status: 'completed', details: JSON.stringify({name: 'pmo_consult'}), timestamp: 1.9},
      {id: 'parent-think', source_message_id: 'assistant-1', type: 'thinking', status: 'completed', summary: 'Delegating to specialist', timestamp: 2},
      {id: 'parent-consult', source_message_id: 'assistant-1', type: 'tool_call', tool_name: 'pmo_consult', status: 'completed', details: JSON.stringify({plan_id: '177', handle: 'dev-1'}), timestamp: 2},
    ]},
  })
  assert.equal(stream.map(item => item.streamKind).join(','), 'message,subchat')
  assert.equal(stream.some(item => String(item.record.toolName) === 'pmo_consult'), false)
  assert.equal(stream[1].record.delegation_activity.length, 4)
  assert.equal(stream.some(item => String(item.record.detail).includes('pmo_consult')), false)

  const SpecialistSubChat = loaded.testApi.SpecialistSubChat as (props: Record<string, unknown>) => ElementNode
  const nodes = walk(SpecialistSubChat({subchat: stream[1].record, board: 'alpha', open: false, onToggle: () => undefined}))
  const root = nodes.find(node => node.type === 'article' && node.props.className === 'pmo-subchat pmo-subchat--completed')
  const toggle = nodes.find(node => node.type === 'button' && node.props.className === 'pmo-subchat__toggle')
  assert.ok(root)
  assert.equal(toggle?.props['aria-expanded'], false)
  assert.match(String(toggle?.props['aria-label']), /specialist sub-chat with @dev-1/i)
  assert.equal(nodes.find(node => node.props.className === 'pmo-subchat__detail')?.props.hidden, true)
  const attrs: Record<string, string> = {'aria-expanded': 'false'}
  const panel = {hidden: true}
  let toggled = false
  ;(toggle?.props.onClick as (event: unknown) => void)({currentTarget: {
    getAttribute: (name: string) => attrs[name],
    setAttribute: (name: string, value: string) => {attrs[name] = value},
    closest: () => ({classList: {toggle: () => {toggled = true}}, querySelector: () => panel}),
  }})
  assert.equal(attrs['aria-expanded'], 'true')
  assert.equal(panel.hidden, false)
  assert.equal(toggled, true)
  const expanded = walk(SpecialistSubChat({subchat: stream[1].record, board: 'alpha', open: true, onToggle: () => undefined}))
  assert.equal(expanded.find(node => node.props.className === 'pmo-subchat__detail')?.props.hidden, false)
})

test('gateway-prefixed human principals never render as agents', () => {
  const loaded = runtime()
  const identity = loaded.testApi.identity as (author: string, roster: Record<string, unknown>) => {isHuman: boolean; name: string}
  const person = identity('user:human:ceo@datansh.local', {byProfile: {}, byPrincipal: {}})
  assert.equal(person.isHuman, true)
  assert.equal(person.name, 'ceo')
})

test('Founder workflow hides machine envelopes and retains submitted answers', () => {
  const loaded = runtime()
  const parse = loaded.testApi.workflowEnvelope as (body: string) => {marker: string; data: Record<string, unknown>; prose: string} | null
  const visible = loaded.testApi.workflowVisibleBody as (body: string) => string
  const responses = loaded.testApi.workflowResponses as (messages: Array<Record<string, unknown>>) => {details: Map<string, Record<string, unknown>>; confirmations: Set<string>}
  const request = '[pmo:founder-detail-request:v1]\n{"plan_id":"11","questions":[]}\n\nChoose the delivery scope.'
  assert.equal(parse(request)?.data.plan_id, '11')
  assert.equal(visible(request), 'Choose the delivery scope.')
  assert.doesNotMatch(visible(request), /plan_id/)
  const state = responses([
    {id: 90, author: 'human:ceo@example.com', body: '[pmo:founder-detail-response:v1]\n{"request_id":"42","answers":[{"question_id":"scope","choice_id":"full","answer":"Complete"}]}\n\nAnswered'},
    {body: '[pmo:founder-ticket-decision:v1]\n{"confirmation_id":"51"}\n\nApproved'},
  ])
  assert.equal(state.details.has('42'), true)
  assert.equal(state.details.get('42')?.author, 'human:ceo@example.com')
  assert.equal(state.confirmations.has('51'), true)
})

test('Founder workflow renders Codex-style questions with automatic Other and an approval gate', () => {
  const loaded = runtime()
  const Card = loaded.testApi.FounderWorkflowCard as (props: Record<string, unknown>) => ElementNode
  const detailBody = '[pmo:founder-detail-request:v1]\n' + JSON.stringify({
    plan_id: '11',
    questions: [{
      id: 'scope', question: 'Which scope?',
      options: [
        {id: 'small', label: 'Focused', description: 'Only the requested surface.'},
        {id: 'full', label: 'Complete', description: 'All related surfaces.'},
      ],
    }],
  }) + '\n\nChoose one.'
  const detailNodes = walk(Card({message: {id: 42, body: detailBody}, send: () => Promise.resolve(true), busy: false, resolved: false}))
  const detailRadios = detailNodes.filter(node => node.type === 'input' && node.props.type === 'radio')
  assert.deepEqual(detailRadios.map(node => node.props.value), ['small', 'full', '__other__'])
  assert.ok(detailNodes.some(node => node.type === 'button' && node.props.children?.[0] === 'Send details to @pm'))

  const submittedNodes = walk(Card({message: {id: 42, body: detailBody}, send: () => Promise.resolve(true), busy: false, resolved: true,
    response: {answers: [{question_id: 'scope', choice_id: 'full', answer: 'Complete'}]}}))
  const selected = submittedNodes.find(node => node.type === 'input' && node.props.value === 'full')
  assert.equal(selected?.props.checked, true)

  const confirmationBody = '[pmo:founder-ticket-confirmation:v1]\n' + JSON.stringify({
    plan_id: '11', proposal: {
      title: 'Build role-aware administration', assignee: 'dev-1',
      outcome: 'Administrators can manage project agents safely.',
      acceptance_criteria: ['Project scope is enforced'], context_summary: 'Repository and developer context.',
    },
  }) + '\n\nReview this ticket.'
  const approvalNodes = walk(Card({message: {id: 51, body: confirmationBody}, send: () => Promise.resolve(true), busy: false, resolved: false}))
  const choices = approvalNodes.filter(node => node.type === 'input' && node.props.type === 'radio')
  assert.deepEqual(choices.map(node => node.props.value), ['approve', 'changes', 'other'])
  assert.ok(approvalNodes.some(node => node.type === 'strong' && node.props.children?.[0] === 'Build role-aware administration'))

  const humanConfirmationBody = '[pmo:founder-ticket-confirmation:v1]\n' + JSON.stringify({
    plan_id: '11', proposal: {
      title: 'Enable the Arabic locale', assignee: 'human:ceo@datansh.local',
      outcome: 'Arabic content can be administered.',
      acceptance_criteria: ['The locale is recorded'], context_summary: 'Founder-owned setup.',
      human_steps: ['Open locale management', 'Add the approved locale', 'Attach the validation result'],
    },
  }) + '\n\nReview this human hand-off.'
  const humanApprovalNodes = walk(Card({message: {id: 52, body: humanConfirmationBody}, send: () => Promise.resolve(true), busy: false, resolved: false}))
  assert.ok(humanApprovalNodes.some(node => node.type === 'strong' && node.props.children?.[0] === 'Human hand-off steps'))
  assert.ok(humanApprovalNodes.some(node => node.type === 'li' && node.props.children?.[0] === 'Attach the validation result'))
})

test('thinking activity is collapsed by default and exposes an accessible toggle', () => {
  const loaded = runtime()
  const AgentActivity = loaded.testApi.AgentActivity as (props: Record<string, unknown>) => ElementNode
  const nodes = walk(AgentActivity({activity: {type: 'thinking', status: 'completed', detail: 'Checking project context'}}))
  const toggle = nodes.find(node => node.type === 'button' && node.props.className === 'pmo-thinking-toggle')
  assert.equal(toggle?.props['aria-expanded'], false)
  assert.equal(typeof toggle?.props.onClick, 'function')
  assert.equal(nodes.some(node => node.type === 'pre'), false)
})

test('a working agent activity is visibly marked as live', () => {
  const loaded = runtime()
  const AgentActivity = loaded.testApi.AgentActivity as (props: Record<string, unknown>) => ElementNode
  const node = AgentActivity({activity: {type: 'working', status: 'working', detail: 'Checking locale configuration'}})
  assert.match(String(node.props.className), /is-live/)
  assert.equal(node.props['aria-live'], 'polite')
  assert.ok(walk(node).some(item => item.props.className === 'pmo-agent-activity__live'))
})

test('copied cards retain a keyboard and non-drag move path', () => {
  const loaded = runtime()
  const TaskCard = loaded.testApi.TaskCard as (props: Record<string, unknown>) => ElementNode
  const nodes = walk(TaskCard({
    task: {
      id: 't_1', title: 'Ship', status: 'ready', age: {}, priority: 0,
      comment_count: 0, link_counts: {parents: 0, children: 0},
    },
    selected: false, failed: false, draggingSource: false,
    toggleSelected: () => undefined, toggleRange: () => undefined,
    onOpen: () => undefined, onMove: () => undefined,
  }))
  assert.ok(nodes.find(node => node.props.role === 'listitem'))
  const interactiveCard = nodes.find(node => node.props.role === 'button')
  assert.equal(interactiveCard?.props.tabIndex, 0)
  assert.equal(typeof interactiveCard?.props.onKeyDown, 'function')
  const move = nodes.find(node => node.type === 'select' && node.props.className === 'pmo-card-move')
  assert.ok(move)
  assert.match(String(move?.props['aria-label']), /Move Ship/)
})

test('task drawer is a modal dialog that participates in focus management', () => {
  const loaded = runtime()
  const TaskDrawer = loaded.testApi.TaskDrawer as (props: Record<string, unknown>) => ElementNode
  const nodes = walk(TaskDrawer({
    taskId: 't_1', boardSlug: 'acme', onClose: () => undefined,
    onRefresh: () => undefined, allTasks: [], assignees: [], eventTick: 0,
  }))
  const dialog = nodes.find(node => node.props.role === 'dialog')
  assert.equal(dialog?.props['aria-modal'], 'true')
  assert.equal(dialog?.props.tabIndex, -1)
})

test('board columns page at 50 cards and expose a keyboard load-more control', () => {
  const loaded = runtime()
  const page = loaded.testApi.pageColumnTasks as (
    tasks: Array<{id: number}>, limit?: number,
  ) => {tasks: Array<{id: number}>; total: number; hasMore: boolean; nextLimit: number}
  const tasks = Array.from({length: 125}, (_, id) => ({id}))
  const first = page(tasks)
  assert.equal(first.tasks.length, 50)
  assert.equal(first.total, 125)
  assert.equal(first.hasMore, true)
  assert.equal(page(tasks, first.nextLimit).tasks.length, 100)

  const Column = loaded.testApi.Column as (props: Record<string, unknown>) => ElementNode
  const nodes = walk(Column({
    column: {name: 'todo', tasks: [], total_filtered: 125, has_more: true},
    selectedIds: new Set(), failedIds: new Set(), laneByProfile: false,
    onMove: () => undefined, onOpen: () => undefined,
    onCreate: () => Promise.resolve(), onLoadMore: () => undefined,
    allTasks: [],
  }))
  const loadMore = nodes.find(
    node => node.type === 'button' && node.props.className === 'pmo-load-more',
  )
  assert.equal(loadMore?.props.type, 'button')
  assert.equal(typeof loadMore?.props.onClick, 'function')
})

test('rejects unsafe link schemes and formats money through Intl', () => {
  const loaded = runtime()
  const safeHref = loaded.testApi.safeHref as (value: string) => string
  const formatMoney = loaded.testApi.formatMoney as (value: string) => string
  assert.equal(safeHref('javascript:alert(1)'), '#pmo/portfolio')
  assert.equal(safeHref('data:text/html,pwned'), '#pmo/portfolio')
  assert.equal(safeHref('/pmo/boards/acme/tasks/t_1'), '/pmo/boards/acme/tasks/t_1')
  assert.match(formatMoney('12.50'), /12\.50/)
})

test('Founder’s Office turns ticket references and tool outcomes into scoped action previews', () => {
  const loaded = runtime()
  const ticketHref = loaded.testApi.ticketHref as (board: string, task: string) => string
  const linkedMarkdown = loaded.testApi.linkedMarkdown as (body: string, board: string) => string
  const activityPreviews = loaded.testApi.activityPreviews as (activity: Record<string, unknown>) => Array<Record<string, unknown>>
  assert.equal(ticketHref('alpha', 't_ab12'), '#pmo/board?board=alpha&task=t_ab12')
  assert.match(linkedMarkdown('Created t_ab12 for @luna-alpha.', 'alpha'), /\[t_ab12\]\(#pmo\/board\?board=alpha&task=t_ab12\)/)

  const ticket = activityPreviews({
    id: 'tool-1', type: 'tool_call', toolName: 'pmo_ticket_create', status: 'completed',
    detail: 'python plugins/pmo/ticket.py --title "Ship launch"', result: 'Created t_ab12',
  })
  assert.deepEqual(Array.from(ticket[0].tickets as string[]), ['t_ab12'])
  assert.equal(ticket[0].label, 'Ticket created')

  const skill = activityPreviews({id: 'tool-2', type: 'tool_call', toolName: 'skill_manage', detail: 'patch project brief'})
  assert.equal(skill[0].kind, 'skill')
  assert.equal(skill[0].label, 'Skill updated')

  const contextOnly = activityPreviews({
    id: 'tool-3', type: 'tool_call', toolName: 'pmo_context',
    detail: 'load project context', result: 'task t_thread created earlier',
  })
  assert.equal(contextOnly.length, 0)

  const genericContextCall = activityPreviews({
    id: 'tool-4', type: 'tool_call', toolName: 'tool_call',
    detail: '{"arguments":{"request":"review ticket t_ab12 before creation"},"name":"pmo_context"}',
  })
  assert.equal(genericContextCall.length, 0)

  const createWithOverlap = activityPreviews({
    id: 'tool-5', type: 'tool_call', toolName: 'pmo_create_ticket',
    result: '{"ok":true,"task_id":"t_new123","warnings":["touches overlap with t_old123"]}',
  })
  assert.deepEqual(Array.from(createWithOverlap[0].tickets as string[]), ['t_new123'])

  const messagePreviews = loaded.testApi.messagePreviews as (message: Record<string, unknown>) => Array<Record<string, unknown>>
  assert.equal(messagePreviews({
    id: 'plan-1',
    body: '[pmo:founder-plan:v1]\n{}\n\nPlan around existing assigned ticket t_old123 before creating anything.',
  }).length, 0)
  assert.equal(messagePreviews({
    id: 'approval-1',
    body: '[pmo:founder-ticket-confirmation:v1]\n{}\n\nNothing is created until t_old123 is approved.',
  }).length, 0)
  const createdMessage = messagePreviews({
    id: 'created-1',
    body: '[pmo:founder-plan-ticket:v1]\n{"task_id":"t_new123"}\n\nCreated delegated ticket t_new123; parent t_old123.',
  })
  assert.equal(createdMessage[0].label, 'Ticket created')
  assert.deepEqual(Array.from(createdMessage[0].tickets as string[]), ['t_new123'])
})

test('Kanban comments hide Founder workflow machine envelopes', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '../plugins/pmo/dashboard/dist/index.js'), 'utf8',
  )
  assert.match(source, /source: pmWorkflowVisibleBody\(c\.body\)/)
})

test('repository connections normalize backend metadata for project overview cards', () => {
  const loaded = runtime()
  const repositoryList = loaded.testApi.repositoryList as (
    payload: Record<string, unknown> | Array<Record<string, unknown>>,
  ) => Array<Record<string, unknown>>
  const repositories = repositoryList({repositories: [{
    name: 'web-app', path: 'C:\\Projects\\web-app', url: 'https://github.com/acme/web-app.git',
    default_branch: 'main', access: 'write', primary: true,
    valid: true, detail: 'Git repository is ready',
    github_app: {
      provider: 'github_app', installation_id: 42, installation_account: 'acme-inc',
      repository_id: 9001, full_name: 'acme/web-app',
    },
  }, {
    name: 'api', path: '/work/api', url: 'https://github.com/acme/api.git', default_branch: 'develop',
    access: 'read', primary: false, valid: false,
  }]})

  assert.deepEqual(repositories.map(item => ({
    name: item.name, path: item.path, remote: item.remote,
    branch: item.default_branch, access: item.access,
    primary: item.primary, validation: item.validation_status,
    github: item.github_app ? JSON.parse(JSON.stringify(item.github_app)) : null,
  })), [{
    name: 'web-app', path: 'C:\\Projects\\web-app', remote: 'https://github.com/acme/web-app.git',
    branch: 'main', access: 'write', primary: true, validation: 'valid',
    github: {
      provider: 'github_app', installation_id: '42', installation_account: 'acme-inc',
      repository_id: '9001', full_name: 'acme/web-app',
    },
  }, {
    name: 'api', path: '/work/api', remote: 'https://github.com/acme/api.git',
    branch: 'develop', access: 'read', primary: false, validation: 'invalid', github: null,
  }])
})

test('repository mutation requests follow the frozen endpoints without credential fields', () => {
  const loaded = runtime()
  const repositoryRequest = loaded.testApi.repositoryRequest as (
    project: string, action: string, values: Record<string, unknown>,
  ) => {url: string; options: {method: string; body?: string}}

  const connect = repositoryRequest('project/acme', 'connect', {
    path: ' C:\\Projects\\acme ', name: ' acme ', primary: true,
    username: 'must-not-leak', token: 'must-not-leak',
  })
  assert.equal(connect.url, '/api/plugins/pmo/projects/project%2Facme/repositories/connect')
  assert.equal(connect.options.method, 'POST')
  assert.deepEqual(JSON.parse(String(connect.options.body)), {
    path: 'C:\\Projects\\acme', name: 'acme', primary: true,
    default_branch: 'main', access: 'write',
  })

  const connectGithub = repositoryRequest('p1', 'connect', {
    path: ' C:\\Projects\\api ', default_branch: 'develop', access: 'write',
    installation_id: 42, repository_id: 9002, full_name: 'acme/api',
  })
  assert.deepEqual(JSON.parse(String(connectGithub.options.body)), {
    path: 'C:\\Projects\\api', default_branch: 'develop', access: 'write',
    installation_id: 42, repository_id: 9002, full_name: 'acme/api',
  })

  const clone = repositoryRequest('p1', 'clone', {
    path: ' repos/api ', name: '', primary: false,
    default_branch: 'develop', access: 'read',
    installation_id: '42', repository_id: '9002', full_name: ' acme/api ',
    password: 'must-not-leak', ssh_key: 'must-not-leak',
  })
  assert.equal(clone.url, '/api/plugins/pmo/projects/p1/repositories/clone')
  assert.deepEqual(JSON.parse(String(clone.options.body)), {
    path: 'repos/api', installation_id: 42, repository_id: 9002, full_name: 'acme/api',
    default_branch: 'develop', access: 'read',
  })

  const remove = repositoryRequest('p1', 'remove', {name: 'api/web'})
  assert.equal(remove.url, '/api/plugins/pmo/projects/p1/repositories/api%2Fweb')
  assert.equal(remove.options.method, 'DELETE')
  assert.equal(remove.options.body, undefined)
})

test('GitHub App requests and authorized repository metadata follow the consent contract', () => {
  const loaded = runtime()
  const githubRequest = loaded.testApi.githubRequest as (
    project: string, action: string, values: Record<string, unknown>, board?: string,
  ) => {url: string; options: {method: string}}
  const githubInstallations = loaded.testApi.githubInstallations as (
    payload: Record<string, unknown>,
  ) => Array<Record<string, unknown>>
  const githubRepositories = loaded.testApi.githubRepositories as (
    payload: Record<string, unknown>,
  ) => Array<Record<string, unknown>>

  assert.deepEqual(JSON.parse(JSON.stringify(githubRequest('project/acme', 'status', {}, 'roadmap'))), {
    url: '/api/plugins/pmo/github-app/status', options: {method: 'GET'},
  })
  assert.deepEqual(JSON.parse(JSON.stringify(githubRequest('project/acme', 'install', {}, 'roadmap'))), {
    url: '/api/plugins/pmo/projects/project%2Facme/github-app/install?board=roadmap', options: {method: 'POST'},
  })
  assert.deepEqual(JSON.parse(JSON.stringify(githubRequest('project/acme', 'poll', {state: 'consent-state'}, 'roadmap'))), {
    url: '/api/plugins/pmo/projects/project%2Facme/github-app/poll?board=roadmap',
    options: {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{"state":"consent-state"}'},
  })
  assert.deepEqual(JSON.parse(JSON.stringify(githubRequest('project/acme', 'installations', {}, 'roadmap'))), {
    url: '/api/plugins/pmo/projects/project%2Facme/github-app/installations?board=roadmap', options: {method: 'GET'},
  })
  assert.deepEqual(JSON.parse(JSON.stringify(githubRequest('project/acme', 'repositories', {installation_id: 42}, 'roadmap'))), {
    url: '/api/plugins/pmo/projects/project%2Facme/github-app/installations/42/repositories?board=roadmap', options: {method: 'GET'},
  })

  assert.deepEqual(JSON.parse(JSON.stringify(githubInstallations({installations: [{
    installation_id: 42, installation_account: 'acme-inc', repository_selection: 'selected',
    contents_permission: 'write',
  }]}))), [{
    installation_id: '42', installation_account: 'acme-inc', repository_selection: 'selected',
    contents_permission: 'write',
  }])
  assert.deepEqual(JSON.parse(JSON.stringify(githubRepositories({repositories: [{
    repository_id: 9002, full_name: 'acme/api', private: true, default_branch: 'develop',
  }]}))), [{repository_id: '9002', full_name: 'acme/api', private: true, default_branch: 'develop'}])
  assert.match(String((loaded.testApi.strings as Record<string, Record<string, string>>).repositories.githubAccess), /short-lived GitHub App access/)
})

test('project overview progressively discloses repository connection and polls GitHub consent', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '../plugins/pmo/dashboard/dist/index.js'), 'utf8',
  )
  assert.match(source, /addAnother: "\+ Add repository"/)
  assert.match(source, /setAdding\(true\)/)
  assert.match(source, /aria-labelledby": "pmo-add-repository-title"/)
  assert.match(source, /pmGithubRequest\(props\.projectRef, "poll"/)
  assert.match(source, /authorizationWaiting/)
  assert.match(source, /setConsentUrl\(consentUrl\)/)
})

test('project onboarding requests stage one project PM and multiple app repositories without credentials', () => {
  const loaded = runtime()
  const onboardingRequest = loaded.testApi.onboardingRequest as (
    id: string, action: string, values: Record<string, unknown>,
  ) => {url: string; options: {method: string; body?: string}}
  const projectSlug = loaded.testApi.projectSlug as (value: string) => string
  const onboardingErrorMessage = loaded.testApi.onboardingErrorMessage as (error: unknown) => string
  assert.equal(projectSlug('  Launch Plan 2026! '), 'launch-plan-2026')
  assert.equal(
    onboardingErrorMessage(new Error("[WinError 2] The system cannot find the file specified: 'C:\\\\Projects'")),
    'Workspace parent folder does not exist. Choose an existing parent folder.',
  )

  const create = onboardingRequest('', 'create', {
    slug: ' launch ', name: ' Launch ', workspace_path: ' C:\\Projects\\launch ',
    board_slug: ' launch-board ', token: 'must-not-leak', password: 'must-not-leak',
  })
  assert.equal(create.url, '/api/plugins/pmo/project-onboarding')
  assert.deepEqual(JSON.parse(String(create.options.body)), {
    slug: 'launch', name: 'Launch', workspace_path: 'C:\\Projects\\launch', board_slug: 'launch-board',
  })

  assert.deepEqual(JSON.parse(JSON.stringify(onboardingRequest('draft/id', 'install', {}))), {
    url: '/api/plugins/pmo/project-onboarding/draft%2Fid/github-app/install', options: {method: 'POST'},
  })
  assert.deepEqual(JSON.parse(JSON.stringify(onboardingRequest('draft/id', 'poll', {state: 'state-value'}))), {
    url: '/api/plugins/pmo/project-onboarding/draft%2Fid/github-app/poll',
    options: {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{"state":"state-value"}'},
  })
  assert.deepEqual(JSON.parse(JSON.stringify(onboardingRequest('draft/id', 'installations', {}))), {
    url: '/api/plugins/pmo/project-onboarding/draft%2Fid/github-app/installations', options: {method: 'GET'},
  })
  assert.deepEqual(JSON.parse(JSON.stringify(onboardingRequest('draft/id', 'available', {installation_id: 42}))), {
    url: '/api/plugins/pmo/project-onboarding/draft%2Fid/github-app/installations/42/repositories', options: {method: 'GET'},
  })

  const add = onboardingRequest('draft-1', 'add', {
    installation_id: 42, repository_id: 9002, full_name: ' acme/api ',
    local_path: ' ../api ', default_branch: 'develop', access: 'read', primary: false,
    ssh_key: 'must-not-leak', token: 'must-not-leak',
  })
  assert.deepEqual(JSON.parse(String(add.options.body)), {
    installation_id: 42, repository_id: 9002, full_name: 'acme/api',
    local_path: '../api', default_branch: 'develop', access: 'read', primary: false,
  })
  assert.equal(onboardingRequest('draft-1', 'remove', {name: 'api/web'}).url, '/api/plugins/pmo/project-onboarding/draft-1/repositories/api%2Fweb')
  assert.deepEqual(JSON.parse(JSON.stringify(onboardingRequest('draft-1', 'complete', {}))), {
    url: '/api/plugins/pmo/project-onboarding/draft-1/complete', options: {method: 'POST'},
  })
})

test('project onboarding first step previews its dedicated PM and has no credential fields', () => {
  const loaded = runtime()
  const ProjectOnboardingView = loaded.testApi.ProjectOnboardingView as (
    props: Record<string, unknown>,
  ) => ElementNode
  const nodes = walk(ProjectOnboardingView({refreshKey: 0}))
  assert.equal(nodes.find(node => node.type === 'h2')?.props.children?.[0], 'Project onboarding')
  assert.ok(nodes.find(node => node.props.className === 'pmo-onboarding-steps'))
  assert.ok(nodes.find(node => node.props.id === 'pmo-onboarding-workspace'))
  assert.equal(nodes.filter(node => node.props.type === 'password').length, 0)
})

test('repository card exposes validation and safe disconnect confirmation semantics', () => {
  const loaded = runtime()
  const RepositoryCard = loaded.testApi.RepositoryCard as (
    props: Record<string, unknown>,
  ) => ElementNode
  const nodes = walk(RepositoryCard({
    repository: {
      name: 'web-app', path: 'C:\\Projects\\web-app', remote: 'https://github.com/acme/web-app.git',
      default_branch: 'main', access: 'write', primary: false,
      validation_status: 'valid', validation_detail: 'Repository is ready',
      github_app: {
        provider: 'github_app', installation_id: '42', installation_account: 'acme-inc',
        repository_id: '9001', full_name: 'acme/web-app',
      },
    },
    confirming: 'web-app', busy: false,
    setConfirming: () => undefined, remove: () => Promise.resolve(),
  }))

  assert.ok(nodes.find(node => node.type === 'article' && node.props.className === 'pmo-repository-card'))
  assert.equal(nodes.find(node => node.type === 'h4')?.props.children?.[0], 'web-app')
  assert.equal(nodes.find(node => node.type === 'code')?.props.title, 'C:\\Projects\\web-app')
  const validation = nodes.find(node => String(node.props.className || '').includes('pmo-repository-validation--good'))
  assert.equal(validation?.props.title, 'Repository is ready')
  assert.equal(nodes.find(node => node.props.className === 'pmo-repository-github')?.props['aria-label'], 'GitHub App repository acme/web-app')
  assert.equal(nodes.find(node => node.props.className === 'pmo-repository-card__actions')?.props['aria-live'], 'polite')
  assert.equal(nodes.filter(node => node.type === 'dl').length, 1)
})
