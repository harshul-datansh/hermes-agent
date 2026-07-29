# OpenRouter Free Models Summary

Generated: 2026-07-29T12:35:02

Live API candidates saved to `openrouter-free-models-2026-07-29.json`.

| Rank | Model ID | Context | Why Candidate | Risk |
| --- | --- | ---: | --- | --- |
| 1 | `openrouter/free` |  | free router; easiest setup | Random free model choice |
| 2 | `cohere/north-mini-code:free` | 256000 | explicit :free endpoint; 256,000 context; matches agent, code, coding | Availability and tool-call quality unproven |
| 3 | `poolside/laguna-s-2.1:free` | 262144 | explicit :free endpoint; 262,144 context; matches agent, coding | Availability and tool-call quality unproven |
| 4 | `poolside/laguna-xs-2.1:free` | 262144 | explicit :free endpoint; 262,144 context; matches agent, coding | Availability and tool-call quality unproven |
| 5 | `inclusionai/ling-3.0-flash:free` | 262144 | explicit :free endpoint; 262,144 context; matches agent | Availability and tool-call quality unproven |
| 6 | `nvidia/nemotron-3-ultra-550b-a55b:free` | 1000000 | explicit :free endpoint; 1,000,000 context; matches reason | Availability and tool-call quality unproven |

## Smoke Test Matrix

| Model | Passed | Mode | Latency ms | Notes |
| --- | --- | --- | ---: | --- |
| `openrouter/free` | True | live | 2825 | Datansh free model smoke test passed. |
| `cohere/north-mini-code:free` | True | live | 2629 | Datansh free model smoke test passed. |
| `poolside/laguna-s-2.1:free` | False | live_failed | 410 | {"error":{"message":"Provider returned error","code":429,"metadata":{"raw":"poolside/laguna-s-2.1:free is temporarily rate-limited upstream. Please retry shortly, or add your own k |
