# Edyo Campos Severo Neto — AI Engineer - Brazil

**Artefact Technical Case 2026**
Repository: <https://github.com/edyoCampos/mel>

## Summary

Mel is a Python text agent that answers the customers of Empório da Música, a (fictitious)
musical instrument store, about products, prices, orders and store policies. The core idea:
**the LLM decides what to look up; the code decides how much.** Every amount, deadline and
store-hours phrase is computed by tested code and reaches the model already formatted.

## What Mel does

- Searches the catalog and gives prices with PIX and interest-free installments, applying only
  active promotions (PIX never stacks on a promotion).
- Suggests alternatives for out-of-stock, discontinued or coming-soon instruments.
- Looks up an order only with the order number plus the e-mail or phone of the purchase, and
  computes delays and return and warranty deadlines.
- Answers store policies (hours, payment, returns, shipping, warranty, privacy, contacts) from the
  manual, by topic.
- Declines off-topic questions and manipulation attempts, and hands complaints, delays and
  anything without a safe answer to a human with a protocol, saying when the team is back.
- Answers in the customer's language (Portuguese or English).

## How it works

A small function-calling loop with five typed tools over the store's CSV data and manual. Two
deterministic guards (protocol and language) check each final answer. Details:
[`docs/architecture.md`](docs/architecture.md).

## How to run

```bash
make setup                 # installs the environment with uv
cp .env.example .env       # then put a free Groq API key in .env
make chat                  # talk to Mel in the terminal
```

## Key decisions

| Topic | Decision |
| --- | --- |
| Framework | Own function-calling loop, no LangChain: linear flow, testable offline with a scripted LLM |
| Model and provider | Groq `openai/gpt-oss-120b` through an OpenAI-compatible adapter, free for the evaluator |
| Interface | CLI with `--trace` to show each tool call |
| Conversation history (persistence) | History in memory for the session |
| Data handling | CSVs validated with Pydantic, product specs normalized, and all 26 data or policy inconsistencies found documented with a decision (README, Data findings) |
| Retrieval | Policies by topic and data through typed tools; no vector store for an 8-page manual |
| Prompt | Six blocks plus date, time and store status on every turn; rules that matter are enforced in code |

All 25 decisions and their reasons: [`DECISIONS.md`](DECISIONS.md).

## Example conversations

1. [Acoustic guitars up to R$ 1.000 and the store address](examples/01-acoustic-guitars-under-1000.md)
2. [Takamine GD20 typed with a typo, with an expired promotion](examples/02-expired-promotion.md)
3. [Regret right within the 7-day window](examples/03-regret-return.md)
4. [Accessories, a manipulation attempt and Black Friday](examples/04-scope-and-manipulation.md)
5. [English conversation about a delayed order, handed to a human](examples/05-english-delayed-order.md)

## Results

_To be filled after the 03/11 run._

## Known limitations

- Answers vary between runs; the scenario results are a measurement, not a guarantee.
- Freight outside Campo Grande is not calculated (the manual requires ZIP code, weight and
  dimensions), and only national holidays are covered.
- History is lost when the CLI closes; escalations go only to a local log file.

## How I used AI

Claude helped me audit the challenge material and plan, and Claude Code wrote the code from specs,
test first, under rules I set; my work was deciding, reviewing and catching what the tests missed.
The full account is in the README: [How I used AI on this project](README.md#how-i-used-ai-on-this-project).
