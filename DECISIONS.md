# Decision log

Every technical decision of the project, with its reason. The README summarizes the five the
challenge asks for. Status: **kept** (from the first plan), **revised** (changed after review),
**new** (added during planning or implementation).

| ID | Decision | Status | Why |
| --- | --- | --- | --- |
| D-01 | Own tool-calling loop with the model's native function calling; no LangChain | Kept | Linear flow with 5 tools; a short loop is testable offline and explainable. With states and approvals, LangGraph would be the next step |
| D-02 | Policies retrieved by topic; no vector store | Kept | The manual has 8 pages and 10 sections; embeddings would only add retrieval errors. If it grows: BM25 or embeddings behind `PolicyBook` |
| D-03 | Typed tools instead of text-to-SQL | Kept | Free SQL would expose the customers table; typed tools limit what the model can ask |
| D-04 | Every business rule in deterministic code | Kept | The manual forbids giving prices, stock or deadlines without checking the system; the model only copies formatted amounts |
| D-05 | CSV → Pydantic models → in-memory `StoreData` | Kept | 65 products and 20 orders; a database would change only `store_data.py` |
| D-06 | Orders only with order number + e-mail or phone | Kept (an inference) | Two customers share an e-mail; looking up by name would leak orders of other people (LGPD) |
| D-07 | "Now" injected; `--now` freezes it | Kept | Store hours and deadlines are testable and demos are reproducible |
| D-08 | Manual converted once to Markdown and versioned | Kept | Git diff shows policy changes; a test guarantees the 10 sections |
| D-09 | `LLMClient` protocol + one OpenAI-compatible adapter; **Groq** (`openai/gpt-oss-120b`) by default, Gemini (`gemini-3.8-flash`) as alternative; retry HTTP 429, 502 and 503 up to 3 attempts; optional `LLM_REASONING_EFFORT` | Revised | Both providers are free (the evaluator runs at no cost) and share the OpenAI format; free tiers rate-limit and overload. Gemini was the first choice and works, but took about 50 s per call on its free tier (01/11/2026), so the fallback rule made Groq the default. Works with any OpenAI-compatible provider; tested with Groq and Gemini |
| D-10 | `Money` with `Decimal` | Kept | Floats get cents wrong; a wrong cent in a price is misleading advertising |
| D-11 | Flat package of 10 modules instead of hexagonal architecture | Revised | Keeps what gives guarantees (isolated rules, injectable LLM and clock) with fewer files |
| D-12 | No reviewer agent at run time, with two narrow exceptions: deterministic **protocol** and **language** guards in `Agent` | Revised | A reviewer doubles cost and latency and can be wrong the same way; correctness comes from computed values. Both exceptions exist because the model ignored the prompt rule in the test scenarios. **Protocol guard:** a reply that mentions a protocol with no `escalate_to_human` result (the model once invented "2026-11-00123") is retried once with an instruction to call the tool; a second miss returns the store contacts. **Language guard:** a Portuguese reply to an English message (the model answered an English customer in Portuguese twice, despite the rule) is retried once with an instruction to answer in English; a second Portuguese draft is kept. Both are word or regex checks, not a second model, and cost a call only when they fire |
| D-13 | Fuzzy product names (`rapidfuzz`) | Kept | "takamine gd 20" finds the GD20; ambiguous names return candidates for Mel to ask |
| D-14 | CLI with `--trace`; no web UI | Revised | The challenge focuses on the agent; the trace shows the data-or-policy decision |
| D-15 | History in memory for the session | Revised | Persistence is optional in the challenge; SQLite is listed under "with more time" |
| D-16 | YAML scenarios with required/forbidden text; no LLM judge | Revised | Objective, cheap checks that also export the example conversations |
| D-17 | `uv`, `ruff` and `pytest` (CI on GitHub Actions); no `mypy --strict` | Revised | Reproducible install with a lock file; strict typing left for later |
| D-18 | System prompt in blocks, with date, time and store status every turn | Revised | Separates rules from style; without time context the model gets "are you open?" wrong |
| D-19 | Repository in English; Mel answers in the customer's language | Revised | Delivery in English; the store's customers are Brazilian |
| D-20 | AI assistant usage declared in the README | Kept | Required by the challenge |
| D-21 | Policies as a tool, not pasted into the prompt | New | Makes "when to consult policies" visible in the trace and testable |
| D-22 | Delivery by e-mail (and form, when required) | New | The challenge asks for the e-mail with the repository link |
| D-23 | Version the 6 CSVs and the converted manual; not the PDFs | New | Without the data the project does not run; the data is fictitious and provided for the challenge |
| D-24 | Closed technology list: Python 3.12, `uv`, `Makefile`; `pydantic`, `openai` (as HTTP client), `python-dotenv`, `rapidfuzz`, `holidays`, `typer`, `rich`, `pyyaml`; dev: `pytest`, `ruff`, `pypdf` | New | Small, explainable stack. Not used on purpose: LangChain, LlamaIndex, vector stores, FastAPI, Streamlit, databases, Redis, docker-compose |
| D-25 | Minimal `Dockerfile` and `make docker-chat` (optional extra) | New | Runs without installing Python; built only if time allows |

## Smaller implementation choices

- `search_products` returns up to 15 items, so "violões até R$ 1.000" lists all 12 matches.
- Installments are allowed when `price ≥ count × minimum`, compared exactly (R$ 149,99 stays at 2x).
- An unknown order and a contact that does not match return the same error, so order numbers cannot be probed.
- Tool schemas are cleaned (no `$ref`, `anyOf` with null, or titles) for OpenAI-compatible providers.
- Escalations get a protocol `EM-YYYYMMDD-NNNN` and a JSON line in `logs/escalations.jsonl`, with contacts masked.
- Prices reach the model as ready-to-copy strings from `PricingService` (`price_display`, `pix_display`, `installments_display`); `pix_display` is absent when a promotion is active, so PIX can never be stacked on it.
- `get_store_policy("promotions")` adds `active_campaign_promotions`, computed from `promotions.csv`: citing the manual's usual campaign ranges is transparency; promising a discount or start date is not allowed.
- The store context gives the next opening in Portuguese ("segunda-feira, 09/11/2026, às 09:00"), the same text the escalation tool returns.
- Empty or degenerate model replies (mostly symbols or zero-width spaces) are retried and then become an explicit error; they are never shown to the customer.
- Replies are normalized to plain hyphens and spaces ("R$ 429,90", "12%"), because some models write U+2011 or U+202F.
