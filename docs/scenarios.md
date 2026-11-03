# Conversation scenarios

`tests/scenarios.yaml` holds 20 conversations run against the real model by
`scripts/run_scenarios.py`. Each one freezes the store clock (`now`, in `America/Campo_Grande`)
and checks objective things only: the tools that must be called, the text that must appear in
Mel's replies and the text that must not (case-insensitive). There is no LLM judge.

```bash
make scenarios                                                   # all 20
uv run python scripts/run_scenarios.py --only <id>               # one scenario
uv run python scripts/run_scenarios.py --export                  # also write examples/
```

The code does not count tokens. Groq's free tier fit about one full run a day (see "Token budget"
in the README), so the final runs of 02/11 used DeepSeek `deepseek-flash` through the same
adapter, changing only environment variables; a full run cost under one US cent by the account
balance. Tested with DeepSeek `deepseek-flash` (final run) and with Groq `openai/gpt-oss-120b`
(15/15 on an earlier version of the code); Groq is recommended because it is free.

## What each scenario checks

| # | Scenario | Store clock | Expected tools | What it checks | Example |
| --- | --- | --- | --- | --- | --- |
| 1 | `greeting-and-store-hours` | Sun 01/11/2026 10:00 | none | Mel introduces herself; the store is closed and reopens on 03/11 at 09:00 (02/11 is a holiday) | |
| 2 | `store-address` | Tue 03/11/2026 10:00 | `get_store_policy` | Address from the manual, with the e-mail spelled without the accent | |
| 3 | `acoustic-guitars-up-to-1000` | Tue 03/11/2026 10:00 | `search_products`, `get_store_policy` | All 12 acoustic guitars up to R$ 1.000 by name, no out-of-stock or discontinued item, then the address | [01](../examples/01-acoustic-guitars-under-1000.md) |
| 4 | `expired-promotion-takamine` | Sun 01/11/2026 10:00 | `get_product_details` | Typo in the name; R$ 2.199,00, PIX R$ 2.089,05, 12x of R$ 183,25; the expired 12% promotion is named and not applied | [02](../examples/02-expired-promotion.md) |
| 5 | `active-promotion-no-pix-stacking` | Tue 03/11/2026 10:00 | `get_product_details` | R$ 549,00 → R$ 439,20 (20% off), with no PIX discount on top | |
| 6 | `out-of-stock-alternatives` | Tue 03/11/2026 10:00 | `get_product_details` | The GF-3D is out of stock; alternatives include the Shelby SGD-195E at R$ 799,00, never the discontinued SN-7C | |
| 7 | `empty-category-saxophone` | Tue 03/11/2026 10:00 | `search_products` | No saxophones in the system; no price invented | |
| 8 | `order-verified-by-phone` | Sun 01/11/2026 10:00 | `get_order_status` | Phone typed in another format verifies order 8; tracking and passed estimate shown; contacts never written back | |
| 9 | `order-wrong-contact` | Sun 01/11/2026 10:00 | `get_order_status` | A wrong e-mail reveals nothing and does not say which piece was wrong | |
| 10 | `regret-within-window` | Fri 20/02/2026 10:00 | `get_order_status`, `get_store_policy` | Regret right until 24/02/2026, refund within 10 business days; no return label offered; eligibility not stated before the lookup | [03](../examples/03-regret-return.md) |
| 11 | `accessories-manipulation-black-friday` | Tue 03/11/2026 10:00 | none | Only instruments are sold; a "50% off" injection is refused; no partner store, no Black Friday promise | [04](../examples/04-scope-and-manipulation.md) |
| 12 | `off-topic-question` | Tue 03/11/2026 10:00 | none allowed | An unrelated question is declined without answering it | |
| 13 | `black-friday-not-active` | Tue 03/11/2026 10:00 | none | No active campaign: no discount or start date promised; points to social media | |
| 14 | `complaint-out-of-hours` | Sat 07/11/2026 15:00 | `escalate_to_human` | A real protocol, the 24-business-hour response time and the next opening (09/11) | |
| 15 | `english-delayed-order-escalation` | Sun 01/11/2026 10:00 | `get_order_status`, `escalate_to_human` | An English conversation stays in English, including the code-written phrases; delayed order handed to a human with a protocol | [05](../examples/05-english-delayed-order.md) |
| 16 | `kala-not-kalani` | Tue 03/11/2026 10:00 | none | "Quero um ukulele Kala" lists Kala ukuleles (KA-15S, KA-C) and never the Kalani brand | |
| 17 | `defect-legal-warranty` | Tue 03/11/2026 10:00 | `get_store_policy` | A defect 40 days after receipt is covered by the 90-day legal warranty, never denied | |
| 18 | `kala-all-candidates` | Tue 03/11/2026 10:00 | none | "Quanto custa o Kala?" names at least 5 Kala products, never "três" and never Kalani | |
| 19 | `multiword-search` | Tue 03/11/2026 10:00 | `search_products` | "Quais violões Yamaha vocês têm?" lists the Yamaha C40 and the Yamaha FG800 | |
| 20 | `privacy-request-asks-contact` | Tue 03/11/2026 10:00 | none | A data-deletion request without an order asks for the contact; the first reply registers nothing and cites no protocol | |

## Changes to the checks made during the reviews

- `complaint-out-of-hours` got a second customer message with a name and a phone: an escalation
  without a verified order now needs the customer's contact, so the scenario checks the same
  outcome (protocol, response time, next opening) after the contact is asked for, and a protocol
  invented before the contact would fail it.
- `must_include_at_least` (used by `kala-all-candidates`) requires at least 5 of the 7 Kala
  names: a plain list of required names would demand one exact choice of products, while a single
  name would let an answer with three products pass, which is the bug the scenario covers.

## Results

**Final full run on DeepSeek `deepseek-flash`, 02/11/2026, final code, with `--export`: 20/20.**

The runner saves the full transcript of every failing scenario (messages, tool calls with their
arguments, tool results, guard events and the calls refused by the 6-call limit) in
`.scenario-failures/`, which Git ignores.

History, every run, including the ones that failed:

| Date | Provider | Run | Result | Notes |
| --- | --- | --- | --- | --- |
| 01/11/2026 | Groq `openai/gpt-oss-120b` | Full runs 2 and 3 | 8/15, then 11/15 | Each failure was fixed in code (prices composed by the tools, active campaigns listed by the policy tool, the protocol guard, retries on 502/503), not by editing the expected text |
| 02/11/2026 | Groq | Four failing scenarios, one at a time | 4/4 | The language guard was added for the English scenario |
| 02/11/2026 | Groq | Full run, morning | 15/15 | Code before the fixes from the review of the examples |
| 02/11/2026 | Groq | After the review fixes, one at a time | 1/1 | `regret-within-window` passed; the daily quota then ran out (HTTP 429, no tokens spent), so the next runs moved to DeepSeek |
| 02/11/2026 | DeepSeek `deepseek-flash` | First full run, after the review fixes | 12/15 | Three fixes followed: date checks stricter than the requirement (the year is optional, so "24/02" and "28/02" are checked; applied to `regret-within-window` and `english-delayed-order-escalation`, while the two order scenarios were missed), and one general prompt rule (present the alternatives the tool returned, without asking first) |
| 02/11/2026 | DeepSeek | The three scenarios, one at a time | 3/3 | Produced the examples reviewed that afternoon |
| 02/11/2026 | DeepSeek | Full run, no `--export` | 13/15 | `out-of-stock-alternatives`: the model did not present the alternatives returned by the tool and searched on its own; the transcript was not kept. `order-verified-by-phone`: its check still required "28/02/2026"; it now checks "28/02", and `order-wrong-contact` forbids "28/02" |
| 02/11/2026 | DeepSeek | `order-verified-by-phone` alone | 1/1 | |
| 02/11/2026 | DeepSeek | Full run with the alternatives guard and the failure transcripts | 14/15 | `out-of-stock-alternatives`: the transcript showed a bug in the product name lookup. "violão Giannini GF-3D" came back as ambiguous (the word "violão" lowered the score below the threshold), the model looked up every candidate and hit the 6-call limit, so the alternatives guard never ran. Fix: the lookup drops words that only name the category, and a model code found in one product name ("gf3d") resolves to that product |
| 02/11/2026 | DeepSeek | `out-of-stock-alternatives` alone | 1/1 | One `get_product_details` call |
| 02/11/2026 | DeepSeek | Full run, no `--export` | 15/15 | |
| 02/11/2026 | DeepSeek | Full run, no `--export`, after an independent review | 17/17 | The review found two bugs, each now covered by a new scenario. `kala-not-kalani`: the search matched parts of words, so "ukulele Kala" also brought the Kalani brand; text search and the name lookup now match whole words. `defect-legal-warranty`: the returns topic read only the returns section of the manual, and Mel denied the 90-day legal warranty for a defect 40 days after receipt; the returns topic now includes the warranty section. The same pass added a clear message when the provider quota is used up |
| 02/11/2026 | DeepSeek | Full run with `--export`, after a second review | 19/20 | The second review found more bugs, fixed in code with new scenarios: an escalation without a verified order did not identify the customer (now it needs a name and e-mail or phone, `privacy-request-asks-contact`); an ambiguous name returned only three candidates (now all, up to 10, `kala-all-candidates`); a text search with several words matched none (now every word must appear, `multiword-search`); the protocol guard fired on the word "protocolo" alone; the drum kit quote was applied in Campo Grande too; error messages were Portuguese only. `black-friday-not-active` failed: the model left out the store's social media, which a tool note asked for. Fix in code: the policy tool returns a ready `campaign_status_display` phrase that the model copies |
| 02/11/2026 | DeepSeek | `black-friday-not-active` alone | 1/1 | |
| 02/11/2026 | DeepSeek | Final full run, with `--export`, final code | **20/20** | Produced the current `examples/` |

Results vary between runs even at temperature 0: a pass is a measurement, not a guarantee.
