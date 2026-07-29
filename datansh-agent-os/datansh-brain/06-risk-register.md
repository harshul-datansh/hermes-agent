# Risk Register

| Risk | Likelihood | Impact | Mitigation | Status |
| --- | --- | --- | --- | --- |
| Free OpenRouter models may be rate-limited or unavailable | Medium | Medium | Keep multiple discovered free candidates and support offline/demo fallback | Open |
| Native Windows TUI/PTY support may be weaker than WSL2 | Medium | Medium | Recommend WSL2 for Hermes chat/TUI; keep monitor and wrapper cross-platform | Open |

| Remote GitHub fork not attached in local checkout | Medium | Medium | Add Datansh fork URL as origin after GitHub fork is created | Open |
| Remote GitHub fork not attached in local checkout | Medium | Medium | Add Datansh fork URL as origin after GitHub fork is created | Open |
| Remote GitHub fork not attached in local checkout | Medium | Medium | Add Datansh fork URL as origin after GitHub fork is created | Open |
| Remote GitHub fork not attached in local checkout | Medium | Medium | Add Datansh fork URL as origin after GitHub fork is created | Open |
| Client-uploaded documents are a prompt-injection vector into client-facing answers | Medium | High | Untrusted-content delimiters, output schema validation, ingestion-time scan for instruction-like text | Open |
| Answer quality unproven until an eval baseline is recorded against a production-grade model | High | Medium | Run the labeled eval set on a paid model before scheduling any client pilot | Open |
