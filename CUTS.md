# Cuts

Appended the moment something is cut, with a one-line reason. Not reconstructed at the end.

| # | Cut | Reason |
|---|-----|--------|
| 1 | RAGAS evaluation harness | No time in the 8h box, and it measures a global corpus that is now per-user. |
| 2 | Tavily web search route | Grounding and isolation claims are unfalsifiable if the agent can answer from outside the indexed corpus. |
| 3 | `execute_code` and `read_file` agent tools | `read_file` reads any path on disk and `execute_code` is raw `exec`; on a multi-tenant platform that is a worse hole than Tavily. |
| 4 | Generic agent session surface (`app/agent`, `/sessions` routes) | Existed only to host the deleted tools; the product is document chat, and the sessions were never scoped by user. |
| 5 | RAG meta-classifier (`/route_question`) | Chose between the corpus and the general agent; with the agent gone, every question goes to the corpus, which is the property we want. |
| 6 | Semantic response cache | Keyed answers on question text with no tenant scoping, so a hit would serve user A's answer to user B. |
| 7 | Global ingestion scripts (`app/rag/ingestion`) | Built one global index from a scraped local corpus; the sync worker replaces the path entirely. |
| 8 | Retriever kinds `semantic`, `pdr`, `turbovec` | Each needs a second prebuilt index, so per-user routing would mean a second embedding pass per tenant at sync time. |
| 9 | Deployment surface (helm charts, k8s, `deploy.sh`, `DEPLOYMENT.md`) | Named default cut, and all of it targets the single-tenant service that no longer exists. |
| 10 | Grafana / Prometheus dashboards | Chart agent-session metrics that were deleted with the session surface. |
| 11 | Pre-fork demo scripts (`demo.sh/ps1/cmd`, `scripts/demo.py`) | Bootstrapped a venv and built indices from a local corpus; superseded by `docker compose up`. |
