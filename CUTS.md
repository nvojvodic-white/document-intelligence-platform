# Cuts

Appended the moment something is cut, with a one-line reason. Not reconstructed at the end.

| # | Cut | Reason |
|---|-----|--------|
| 1 | RAGAS evaluation harness | No time in the 8h box, and it measures a global corpus that is now per-user. |
| 2 | Tavily web search route | Grounding and isolation claims are unfalsifiable if the agent can answer from outside the indexed corpus. |
| 3 | `execute_code` and `read_file` agent tools | `read_file` reads any path on disk and `execute_code` is raw `exec`; on a multi-tenant platform that is a worse hole than Tavily. |
