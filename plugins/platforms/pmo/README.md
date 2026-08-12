# Datansh PM-OS gateway platform

This plugin bridges PM-OS project conversations into Hermes' existing gateway.
It registers through `ctx.register_platform()` and makes no core changes.

The plugin also registers `hermes pmo` through Hermes's public plugin CLI seam.
Its subcommands dispatch to PMO edge modules; no core CLI file is edited.

## Storage and execution boundary

- A project conversation is a native Kanban task parked in `blocked` so the
  normal dispatcher never claims it.
- Messages are append-only native `task_comments`. There is no PMO message or
  session database.
- `BasePlatformAdapter.handle_message()` owns session binding, turn leasing,
  profile selection, memory, skills, self-learning, delegation, and delivery.
- Agent replies use the same native comment API. Automatic replies into a
  client thread fail closed until a human invokes the approved-send action.

## Project provisioning and profile routes

Resolve an explicit project with `plugins.pmo.project_scope.resolve`, then call
`ensure_project_conversations(scope)`. The result contains two task ids and
normal `gateway.profile_routes` entries:

```yaml
gateway:
  multiplex_profiles: true
  profile_routes:
    - name: pmo-acme-founders-office
      platform: pmo
      chat_id: acme
      thread_id: <founders-task-id>
      profile: pm-acme
    - name: pmo-acme-client
      platform: pmo
      chat_id: acme
      thread_id: <client-task-id>
      profile: pm-acme-client
```

The Founder's Office route selects the configured orchestrator profile. The
client route selects the distinct `<pm-profile>-client` profile. That profile is
the enforcement boundary for plan 010: configure it with the narrow client
collaboration toolset. The envelope is defense in depth, not authorization.

The helper returns YAML-serializable entries via `config_routes()` but never
edits global `config.yaml`, reads an active board/profile, or creates profiles.

## Inbound contract

An authenticated PMO dashboard route calls
`adapter.post_authenticated_message(...)` only after its normal project and
thread membership checks. A Founder's Office post wakes the PM only for a real
`@pm` mention outside code. A client post always enters the client profile and
is:

1. scanned with Hermes' shared `tools.threat_patterns` library;
2. stripped of invisible/bidirectional control characters;
3. XML-escaped inside `<untrusted-client-message>`; and
4. marked `trusted_instruction=false` in event metadata.

Flagged content is delivered with a warning rather than silently dropped. The
native comment remains the durable record. A future audit UI may project the
event's threat ids without changing this adapter or adding a conversation store.
