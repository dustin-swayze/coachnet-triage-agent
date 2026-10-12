# Glossary

Terms as used in this project. Short, opinionated, and specific to how we use them.

## Agent concepts
- **Agent.** A program where an LLM decides, at each step, which tool to call next or whether to answer. Ours is *bounded*: the LLM decides only inside the `diagnose` node, under a step cap.
- **Agent loop.** The repeat of "model proposes a tool call -> code runs it -> result goes back to the model" until the model stops calling tools or a limit is hit.
- **Tool.** A plain function with a typed input and output schema that the model can call. In this repo, every tool is read-only and has a mock and a live implementation.
- **Tool contract.** The Pydantic input/output models plus the typed errors a tool may raise. The contract is the reusable asset; it survives a move to Agentforce.
- **Orchestration.** The code that decides the order of nodes and tools. Here: a LangGraph `StateGraph`. Separate from tools on purpose.
- **StateGraph.** LangGraph's explicit graph: typed state, nodes (functions that read state and return updates), edges (fixed or conditional).
- **Node / edge.** A node is a step (`health_check`, `diagnose`). An edge is "what runs next"; a *conditional* edge picks the next node from state.
- **Reducer.** How LangGraph merges a node's update into state for a given key. Lists of evidence and messages use an "append" reducer so nodes add without overwriting.
- **Step cap.** Hard maximum number of tool invocations per run (`STEP_CAP`, default 8). Hitting it ends the run with `incomplete=True`.
- **Time budget.** Total wall-clock allowed per run (`RUN_BUDGET_SECONDS`). Checked between nodes. Per-tool timeouts are separate.
- **Fail-closed.** If a safety check cannot be completed, refuse rather than proceed. Our scoping check works this way: no provable partner filter, no results.
- **Trace.** One JSONL line per tool call: name, redacted inputs, output summary, latency, error. One file per run in `traces/`.
- **Redaction.** Replacing secret-looking values (tokens, keys, passwords, PII fields on a denylist) with `***` before anything is written to a trace or log.
- **Fake chat model.** A scripted stand-in for the LLM used in mock mode. Ours picks its reply by *node name*, not call order, so skipped branches do not desync it.
- **Provider.** Which company's model API we call (`anthropic`, `openai`). Swapped via config through LangChain's `init_chat_model`.

## Retrieval (RAG)
- **RAG.** Retrieval-Augmented Generation: fetch relevant documents first, then let the model answer using them. Here the documents are runbook entries.
- **Runbook.** `docs/runbook/*.md`, one known failure mode per file. The source of truth. Humans edit it; the index is rebuilt from it.
- **Embedding.** A vector of numbers representing a text's meaning, so similar texts are near each other. Produced by an embedding model (fake one in mock mode).
- **Vector store / vector index.** Storage that finds nearest embeddings to a query. Chroma locally now; Postgres + pgvector later. A *derived artifact*, never edited by hand.
- **Chunk.** A slice of a document that gets its own embedding. Each runbook entry is small enough to be one chunk.
- **Retriever.** The interface `search(query, k) -> hits`. The graph only sees this interface, which is what makes the backend swappable.
- **k / top-k.** How many hits to return. Default 3.
- **Hybrid search.** Combining keyword (BM25) and vector scores. On the roadmap; useful when error codes matter more than semantics.

## Evaluation
- **Eval.** A scripted scenario (inputs + fixtures -> expected diagnosis) run automatically. A *suite* of them gives a pass rate.
- **Scenario fixture.** The synthetic case record, log lines, and app status that make one failure mode reproducible in mock mode.

## Integration terms (this project's domain)
- **Partner.** An external organisation whose system syncs with TrxNow's Salesforce. First partner: Coach Net. Identified by `PARTNER_CODE` (`CNET`).
- **Partner code.** The Case field value (`Partner_Code__c = CNET`, placeholder name) that marks a case as belonging to a partner. The scoping key for every tool.
- **Sync.** The HubSpot <-> Salesforce data flow through the Mule app. Cases originate in HubSpot and land in Salesforce; updates can flow back. Direction matters for diagnosis.
- **MuleSoft / Mule app.** The integration application on Anypoint CloudHub 2.0 (Private Space). Listed as "CoachNET SaaS API" in API Manager.
- **Runtime Manager.** Anypoint's console for deployment status, replicas, and logs. Our live source for health and logs.
- **Correlation id.** The id Mule attaches to one end-to-end transaction. Ties a Case to its log lines. Exact location: `TODO(dustin)`.
- **MDC.** Mapped Diagnostic Context: structured key/values attached to Mule log lines (often includes the correlation id).
- **Transform.** The DataWeave step that maps a HubSpot payload to Salesforce fields. A frequent failure point.
- **API contract.** The agreed request/response schema between HubSpot and the Mule API (lives in the work repo). Needed to judge "payload vs contract" failures.
- **Redispatch flag.** A Case marker that says "this is an intentional re-send." A duplicate without it is a bug.
- **Priority Queue.** TrxNow's internal queue that synced cases should appear in. "Sent but not in the queue" is failure mode 02.

## Interop standards (for later phases)
- **MCP.** Model Context Protocol: a standard way to expose tools/resources to any model host. If TrxNow adopts an MCP server for Salesforce or Anypoint, our tools could be served that way.
- **A2A.** Agent-to-Agent protocol: agents calling other agents as peers. Relevant if the dispatch agent (backburner) and this triage agent need to talk.
- **Agentforce.** Salesforce's agent platform. Our tools and runbook are designed to port there; the LangGraph orchestration would be replaced.
