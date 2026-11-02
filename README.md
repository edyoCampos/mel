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

It works with any provider that exposes an OpenAI-compatible API, and both providers below are
free, so switching is only a matter of environment variables in `.env`. **Groq is the
recommended provider** because it is free for the evaluator. The example conversations and the
final scenario runs of 02/11 used DeepSeek `deepseek-flash` (paid, a few US cents per full run,
same adapter), because Groq's free tier fits one full run per day. Gemini is secondary evidence:
it worked in the initial tool-calling test (01/11) but its free tier was slow and unstable in
later runs (timeouts, 503).

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

The five tools are `search_products`, `get_product_details`, `get_order_status`,
`get_store_policy` and `escalate_to_human`. Three deterministic guards (protocol, language and
alternatives) check the final answer before it reaches the customer. The modules, the tool contracts, the
turn sequence and the guards are described in [`docs/architecture.md`](docs/architecture.md).

`--trace` makes the agent's decision visible — data or policy, and with which arguments:

```text
→ get_product_details {"query": "takamine gd 20"}
← ok {"status": "found", "product": {"id": 95, "name": "Takamine GD20 Dreadnought Natural", ...} · 3 ms
```

## Documentation

[`docs/`](docs/README.md) has the product requirements, the architecture and the conversation
scenarios; [`DECISIONS.md`](DECISIONS.md) has the full decision log.

---

## Technical decisions

The full decision log is in [`DECISIONS.md`](DECISIONS.md). Summary of the five decisions the
challenge asks for, followed by the retrieval architecture and the prompt strategy, which the
README must also justify:

**1. Framework / agent approach — function calling with a small own loop (no LangChain).**
The flow is linear with five tools; a short loop is easy to test (a scripted `FakeLLM`
drives it offline) and to explain line by line. Tools are typed (Pydantic) instead of
text-to-SQL, so the model can never query the customers table freely. If the flow grew into
states and approvals, I would move to LangGraph.

**2. Model and provider — Groq by default, Gemini as alternative.** Both are free, so the
evaluator can run the project without cost, and both speak the OpenAI chat-completions format,
so one adapter serves any OpenAI-compatible provider. The adapter retries HTTP 429, 502 and
503 up to 3 times with growing waits, because free tiers rate-limit and overload. Temperature
is 0. Gemini was the first choice, but after the initial test its free tier took about 50 s per
call and later timed out or returned 503, so the fallback rule made Groq the default. The
final scenario runs and the examples used DeepSeek `deepseek-flash` through the same adapter,
because Groq's free tier fits one full run per day.

**3. Interaction interface — CLI.** The challenge focuses on the agent, and `--trace` shows its
reasoning better than a web page would.

**4. Conversation history persistence — in memory, for the session.** Persisting it was not
needed for a terminal prototype; SQLite is listed under "With more time".

**5. Data handling.** The CSVs are loaded once into validated models; money is `Decimal`; the
mixed `strings` spec is split into material and count; fuzzy matching handles typos ("takamine
gd 20"). The manual was converted once to Markdown and reviewed (`scripts/extract_policies.py`).
Every inconsistency found in the data and the manual is documented with a decision in
[Data findings and assumptions](#data-findings-and-assumptions).

**Retrieval architecture — structured retrieval by topic, not a vector store.** The manual has 8
pages and 10 sections, so each policy topic maps to fixed sections; embeddings would only add
retrieval errors. Store data is queried through tools, not retrieved as text. Policies are a
tool (not pasted into the prompt) so that the "data or policy?" decision is visible and testable.

**Prompt strategy — blocks plus time context every turn.** The system prompt has six blocks
(identity and tone, inviolable rules, when to use each tool, conversation flow and special
situations, answer format, current context). The current date, time and store status are
injected on every turn; without them the model gets "are you open?" wrong. The inviolable rule
that matters most: never state an amount that did not come from a tool.

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
| Agent loop | Scripted `FakeLLM`: tool → answer, invalid arguments, 6-call limit, LLM errors, protocol, language and alternatives guards (including English product names in Portuguese messages) | Yes |
| Scenarios | 15 real conversations checking tools called and required/forbidden text (`tests/scenarios.yaml`) | No (needs an API key) |

Development followed TDD: the history shows each `test:` commit before its `feat:` commit.

**Scenario results: final full run on DeepSeek `deepseek-flash`, 02/11, final code: 15/15.**

How we got there, all on 02/11: Groq `openai/gpt-oss-120b` passed 15/15 in the morning (code
before the review fixes; 41 calls, about 108k tokens). After the review fixes, the full runs on
DeepSeek gave 12/15, 13/15 and 14/15. Each failure was fixed in code or in a check that was
stricter than the requirement: date checks now accept the date without the
year; a guard makes Mel present the alternatives the tool returned for an unavailable product;
and the product name lookup ignores category words ("violão Giannini GF-3D" had come back as
ambiguous). The runner now saves the transcript of every failing scenario, which is how the
lookup bug was found. Every run is in the history table of [`docs/scenarios.md`](docs/scenarios.md).

These are results **measured on that day, not a guarantee**: the model's answers vary between
runs even at temperature 0, and free tiers sometimes fail with 429/502/503, so a later run can
fail a scenario on wording or on the provider. The 15 scenarios, what each one checks and the
history of the runs are in [`docs/scenarios.md`](docs/scenarios.md).

## Example conversations

Exported unedited into [`examples/`](examples/) by the scenario runner; the header of each file
gives the scenario, model, run date and frozen store clock. They were generated on 02/11/2026
with DeepSeek `deepseek-flash` and reviewed, before the last fixes (the alternatives guard and the
name lookup); the final code passed the same five scenarios in the final run, without
re-exporting them:

1. [`01-acoustic-guitars-under-1000.md`](examples/01-acoustic-guitars-under-1000.md): catalog
   search and store address (data + policy).
2. [`02-expired-promotion.md`](examples/02-expired-promotion.md): the Takamine GD20 typed with a
   typo, whose 12% promotion has expired.
3. [`03-regret-return.md`](examples/03-regret-return.md): regret right within the 7-day window,
   with identity check.
4. [`04-scope-and-manipulation.md`](examples/04-scope-and-manipulation.md): accessories, a
   manipulation attempt and Black Friday without an active promotion.
5. [`05-english-delayed-order.md`](examples/05-english-delayed-order.md): an English conversation
   about a delayed order, handed to a human with a protocol.

---

## Known limitations

- Municipal holidays of Campo Grande are not covered (national holidays only).
- Freight for other cities is not calculated (the manual requires ZIP code, weight and dimensions).
- The data does not say how a purchase was made nor when it was actually received (A-10, A-11).
- History is lost when the CLI closes; escalation protocols go only to a local log file.
- The metropolitan region is reduced to the city of Campo Grande (A-24).
- Answers vary between runs: the scenario result (15/15 on 02/11/2026) is a measurement, not a
  guarantee. Scenarios check required and forbidden text, so a correct answer worded differently
  can still fail.
- Groq's free tier fits about one full scenario run per day (about 108k tokens); rate limits
  and 502/503 errors can slow it down (the runner pauses between scenarios).
- Language detection recognizes only English and Portuguese, using common function words; a
  very short message ("ok") keeps the conversation's previous language. Prices ("sem juros"),
  delivery texts and the policy manual stay in Portuguese in English conversations.

## With more time

- Persist conversations (SQLite) and send escalations to a real queue.
- An output validator that checks every "R$" in an answer against the tool results.
- An LLM judge for tone; property-based tests for the pricing rules; `mypy --strict`.
- A WhatsApp webhook and a small web UI.
- Hybrid search for the manual if it grows.
- Fewer input tokens per call: a leaner prompt, compact tool schemas, and prompt caching where
  the provider offers it.

---

## How I used AI on this project

I used AI tools throughout this challenge, and I'd rather be upfront about how than leave it to guesswork.

I started by talking the problem through with Claude (claude.ai). We went over the challenge PDF, the policy manual and the CSVs together, and that's where most of the data issues listed in this README came from: the store phone number that changes between two sections of the manual, the PIX orders that never got their 5% discount, two customers sharing the same email. From those conversations I wrote a PRD and a decision log. I pushed back a lot at this stage. More than once I asked for the whole plan to be checked against the source files again, and a good part of it was rewritten because of that.

Next, I used the Reversa spec-driven workflow inside Claude Code to turn the PRD into specs, a traceability matrix and a task list. Claude Code then wrote the code from those specs, test first, which is why the history alternates between `test:` and `feat:` commits. I didn't write most of the code by hand. My part was setting the rules (no new dependencies, no changes to the agreed architecture, a secrets check before every push), deciding what to do whenever something went wrong, and reviewing what came out.

The review turned out to matter more than I expected. Every test was green, and the conversations still had problems. Mel offered to email a return label, which she has no way of doing, and she listed six guitars when twelve matched the question. I only caught these by reading the exported conversations, with Claude as a second reviewer. Each time, the fix went into the code instead of into a longer prompt.

**What I learned**

- A model will happily ignore a rule written in the prompt. The rules that actually held were the ones enforced in code.
- Green tests don't mean good conversations. You have to read them.
- Agents are fast at producing things. The slow and valuable part is deciding what to build and checking what came back.

---

_Empório da Música, its data and its manual are fictitious and were provided by Artefact for this challenge._
