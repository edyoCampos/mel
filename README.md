# Mel — customer service agent for Empório da Música

Mel is a text-based customer service agent for **Empório da Música**, a (fictitious) musical
instrument store in Campo Grande, Brazil. It answers customers about products, prices, orders
and store policies, in the store's tone, without inventing prices, stock or rules.

Built in Python for the Artefact AI Engineer technical challenge (2026).

**Core idea: the LLM decides _what_ to look up; the code decides _how much_.** The model
understands the request, chooses a tool and writes the answer. Every amount (promotional
price, PIX discount, installments, deadlines, store hours) is computed by tested Python code
and reaches the model already formatted, e.g. `R$ 2.199,00`.

---

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you) and a free
API key from **Groq** (default) or **Google Gemini** (alternative).

```bash
git clone https://github.com/edyoCampos/mel.git
cd mel
make setup                 # uv sync: creates .venv from uv.lock
cp .env.example .env       # then put your API key in .env
make chat                  # talk to Mel in the terminal (type "sair" to quit)
```

Useful variants:

```bash
make chat-trace                                            # shows every tool call and result
uv run python -m emporio chat --now 2026-11-01T10:00       # freezes the store clock (demos)
make test                                                  # unit and integration tests, offline
make scenarios                                             # conversation scenarios, real LLM
```

Without Python, with [Docker](https://docs.docker.com/get-docker/) only:

```bash
cp .env.example .env       # put your API key in .env (values without quotes)
make docker-chat           # docker build -t mel . && docker run -it --rm --env-file .env mel
docker run --rm mel pytest -q                              # offline test suite inside the image
```

The `.env` file is never copied into the image; the key is passed only at run time.

### Choosing the model provider

It works with any provider that exposes an OpenAI-compatible API; it was tested with Groq and
Gemini. Both are free, so switching is only a matter of environment variables in `.env`.
**Groq is the recommended provider:** Gemini works, but on its free tier each call took about
50 s (and sometimes timed out) during our tests.

| | Default: Groq | Alternative: Google Gemini |
| --- | --- | --- |
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `LLM_MODEL` | `openai/gpt-oss-120b` | `gemini-3.8-flash` |
| `LLM_API_KEY` | key from the Groq console | key from Google AI Studio |

`.env.example` has both blocks; comment one and uncomment the other. Optionally,
`LLM_REASONING_EFFORT` (for example `low`) is sent to the model; leave it empty to omit it.

**Token budget on the free tier.** `make chat` fits easily: one turn costs about 3k tokens.
`make scenarios` costs about **108k tokens per full run** (measured on 02/11/2026: 41 calls,
almost all input tokens, because the prompt, the tool schemas and the history are sent again on
every call). Groq's free tier for `openai/gpt-oss-120b` allows 200k tokens per day (8k per
minute), so it fits **one full run a day**; use `--only <id>` to rerun a single scenario.

---

## How it works

```text
CLI ──► Agent ──► LLM (Groq / Gemini, OpenAI-compatible)
          │
          └──► ToolRegistry ──► 5 tools ──► StoreData (CSV) · PolicyBook (manual) · services (rules)
```

Each turn: the agent builds the system prompt with the current date, time and store status
(open/closed, next opening), sends it with the conversation and the tool schemas, executes the
tools the model asks for (at most 6 calls per turn), and returns the final answer.

| Tool | When the model uses it |
| --- | --- |
| `search_products` | Which instruments exist in a category, price range or with a feature |
| `get_product_details` | Price, PIX, installments, promotion, availability, delivery and alternatives of one product |
| `get_order_status` | Status, tracking, delay and return/warranty deadlines of an order (needs order number + e-mail or phone) |
| `get_store_policy` | Hours, payment, returns, shipping, promotions, warranty, privacy, contact, scope |
| `escalate_to_human` | Complaints, delayed orders, special freight quotes, privacy requests, anything it cannot answer safely |

`--trace` makes the agent's decision visible — data or policy, and with which arguments:

```text
→ get_product_details {"query": "takamine gd 20"}
← ok {"status": "found", "product": {"id": 95, "name": "Takamine GD20 Dreadnought Natural", ...} · 3 ms
```

Code layout (`src/emporio/`, one flat package):

| Module | Responsibility |
| --- | --- |
| `money.py` | Exact BRL amounts with `Decimal`, "R$ 2.199,00" formatting |
| `models.py` | Pydantic entities and enums (product availability, order status) |
| `store_data.py` | Loads the 6 CSVs, normalizes specs, fuzzy product search |
| `services.py` | Every business rule of the manual, pure and tested: pricing, installments, shipping, return windows, delays, identity check, store hours |
| `policies.py` | The manual (converted to Markdown) retrieved by topic |
| `tools.py` | The 5 tools, their JSON schemas and dispatch |
| `llm.py` | Provider-independent `LLMClient` protocol + OpenAI-compatible adapter with 429 retry |
| `agent.py` | Tool-calling loop and session history |
| `cli.py` | Terminal chat (`typer` + `rich`) |
| `prompts/system.md` | Mel's system prompt |

---

## Technical decisions

The full decision log is in [`DECISIONS.md`](DECISIONS.md). Summary of the five decisions the
challenge asks for:

**1. Framework / agent approach — function calling with a small own loop (no LangChain).**
The flow is linear with five tools; a short loop is easy to test (a scripted `FakeLLM`
drives it offline) and to explain line by line. Tools are typed (Pydantic) instead of
text-to-SQL, so the model can never query the customers table freely. If the flow grew into
states and approvals, I would move to LangGraph.

**2. Model and provider — Groq by default, Gemini as alternative.** Both are free, so the
evaluator can run the project without cost, and both speak the OpenAI chat-completions format,
so one adapter serves any OpenAI-compatible provider (tested with Groq and Gemini). The
adapter retries HTTP 429, 502 and 503 up to 3 times with growing waits, because free tiers
rate-limit and overload. Temperature is 0. Gemini was the first choice and works, but on its
free tier each call took about 50 s (some over 2 minutes or timed out) on 01/11/2026, so the
fallback rule made Groq the default.

**3. Retrieval — structured retrieval by topic, not a vector store.** The manual has 8 pages
and 10 sections, so each policy topic maps to fixed sections; embeddings would only add
retrieval errors. Store data is queried through tools, not retrieved as text. Policies are a
tool (not pasted into the prompt) so that the "data or policy?" decision is visible and testable.

**4. Prompt strategy — blocks plus time context every turn.** The system prompt has six blocks
(identity and tone, inviolable rules, when to use each tool, conversation flow and special
situations, answer format, current context). The current date, time and store status are
injected on every turn; without them the model gets "are you open?" wrong. The inviolable rule
that matters most: never state an amount that did not come from a tool.

**5. Interface and conversation history — CLI, history in memory.** The challenge focuses on the
agent, and `--trace` shows its reasoning better than a web page would. History lives in memory
for the session; persisting it was not needed for a terminal prototype.

**Data treatment.** The CSVs are loaded once into validated models; money is `Decimal`; the mixed
`strings` spec is split into material and count; fuzzy matching handles typos ("takamine gd 20").
The manual was converted once to Markdown and reviewed (`scripts/extract_policies.py`).

### About the data in this repository

`data/raw/` holds the six CSV files exactly as provided by Artefact for this challenge, and
`data/policies.md` is the store manual converted to Markdown. All data is **fictitious** and was
supplied for the challenge; it is versioned because the agent cannot run without it. The
original PDFs (challenge statement and manual) are **not** versioned.

---

## Data findings and assumptions

The challenge asks to assume a reasonable interpretation when something is ambiguous and to
document it. These are the inconsistencies found in the data and the manual, and the decision
taken for each.

| # | Finding | Decision |
| --- | --- | --- |
| A-01 | Phone (67) 3341-4444 on the cover and as "Telefone / WhatsApp"; WhatsApp (67) 3321-4500 in section 7 | Mel gives (67) 3341-4444, which appears twice |
| A-02 | E-mail with an accent ("empóriodamusica") in the company table | Use the version without accent, from the cover |
| A-03 | The payment table says minimum installment R$ 100; section 3.1 has three bands and an ambiguous "no minimum (except below R$ 50)" | Section 3.1 wins: 2–3x ≥ R$ 50, 4–6x ≥ R$ 80, 7–12x ≥ R$ 100 |
| A-04 | "More than 300 instruments" in the manual; 65 in the CSV | Mel never states the catalog size |
| A-05 | Woodwind, brass and orchestral strings exist as categories with no products | Mel says there are no items in the system and offers a human |
| A-06 | Kala KA-ABP-CTG is `coming_soon` with stock 2 and appears in orders 3 and 14 | Shown as "coming soon", not sellable |
| A-07 | Order totals differ from the item sum in orders 3 (−R$ 48) and 20 (−R$ 88) | The registered total is reported |
| A-08 | Orders 9, 16 and 18 break the minimum installment rule | Documented only |
| A-09 | PIX orders 1, 5, 10, 13 and 17 have no 5% discount in the total | Registered total = amount charged |
| A-10 | Orders do not say whether the purchase was online (regret right is for online purchases) | Mel confirms with the customer |
| A-11 | No actual receipt date | `estimated_delivery` is used as the receipt date for delivered orders |
| A-12 | Data goes until March 2026 | Real clock by default; `--now` fixes the date for demos and tests |
| A-13 | Products 93, 96 and 99 have two promotions each | Only active ones count (none of these is active); with two active, the larger wins |
| A-14 | The manual says to suggest partner stores but lists none | Mel does not invent stores |
| A-15 | Order 18 was cancelled "out of stock"; the product has stock 10 today | Current stock prevails |
| A-16 | Customers 11 and 43 share the same e-mail | Orders are verified by the pair order number + contact |
| A-17 | "Holidays" without a list | Brazilian national holidays (`holidays` library); municipal ones are a known limitation |
| A-18 | Preference exchange "within 7 days" without a start date | Counted from receipt |
| A-19 | "Free shipping above R$ 500" | Exactly R$ 500,00 pays R$ 35,00 |
| A-20 | The manual defines no dispatch deadline | Shipped past the estimate, or not shipped after 7 days = delayed; Mel offers a human |
| A-21 | The `strings` spec mixes material ("nylon") and count ("6") | Split into `string_material` and `string_count` |
| A-22 | The manual describes yearly campaigns (Black Friday, Semana do Músico) but none is active | Only active promotions exist for Mel; no campaign discount is promised |
| A-23 | Ibanez Bass 2X is `discontinued` but has stock 13 | Status wins over stock: not offered |
| A-24 | "Metropolitan region of Campo Grande" is not defined | Only the city of Campo Grande gets local delivery rules |
| A-25 | Special freight for "contrabaixos"; the catalog has electric basses only | Read as the large acoustic double bass; electric basses ship normally |
| A-26 | Product 139 is named "Bateria Acústica Yamaha Kit 1" but its description says "Pearl Export" | Kept as is; search by text may return it for "Pearl" |
| — | The store is in Campo Grande/MS, UTC−04:00 | Store hours use `America/Campo_Grande` |

---

## Tests

| Layer | What it covers | Runs offline |
| --- | --- | --- |
| Unit | Money, every rule of the manual (installments, PIX × promotion, shipping, return windows, delays, identity, store hours with holidays) | Yes |
| Integration | CSV loading, the 5 tools end to end, the manual's 10 sections | Yes |
| Agent loop | Scripted `FakeLLM`: tool → answer, invalid arguments, 6-call limit, LLM errors | Yes |
| Scenarios | 15 real conversations checking tools called and required/forbidden text (`tests/scenarios.yaml`) | No (needs an API key) |

Development followed TDD: the history shows each `test:` commit before its `feat:` commit.

**Scenario results:** _to be filled after the run with the real model._

## Example conversations

_To be exported from the scenario run into [`examples/`](examples/)._

---

## Known limitations

- Municipal holidays of Campo Grande are not covered (national holidays only).
- Freight for other cities is not calculated (the manual requires ZIP code, weight and dimensions).
- The data does not say how a purchase was made nor when it was actually received (A-10, A-11).
- History is lost when the CLI closes; escalation protocols go only to a local log file.
- The metropolitan region is reduced to the city of Campo Grande (A-24).
- Free-tier rate limits can slow down the scenario run (the runner pauses between scenarios).

## With more time

- Persist conversations (SQLite) and send escalations to a real queue.
- An output validator that checks every "R$" in an answer against the tool results.
- An LLM judge for tone; property-based tests for the pricing rules; `mypy --strict`.
- A WhatsApp webhook and a small web UI.
- Hybrid search for the manual if it grows.
- Fewer input tokens per call: a leaner prompt, compact tool schemas, and prompt caching where
  the provider offers it.

---

## Use of AI coding assistants

<!-- TODO(Edyo): review this section so it matches your own workflow before delivery. -->

I used **Claude Code** (Anthropic) throughout, with the [Reversa](https://www.npmjs.com/package/reversa)
spec-driven workflow:

1. **Requirements:** I wrote a first PRD, then asked Claude to audit it against the challenge PDF,
   the manual and the CSVs. It recalculated every value from the data, found inconsistencies
   (e.g. a discontinued product with stock, the store's time zone) and produced a revised PRD.
   I made the decisions (provider, data in the repo, technology list, scope cuts).
2. **Specifications:** from the PRD, Claude wrote one spec per module and per tool, the system
   prompt spec, the scenarios, a traceability matrix and a task plan with cut-off points.
3. **Implementation:** Claude implemented the plan task by task with TDD (a failing `test:`
   commit, then the `feat:` commit), under rules I set: Conventional Commits, no new
   dependencies outside an approved list, push only with the suite green.
4. **Review:** I reviewed the specs, the tests and the code, and I can explain every file.

---

_Empório da Música, its data and its manual are fictitious and were provided by Artefact for this challenge._
