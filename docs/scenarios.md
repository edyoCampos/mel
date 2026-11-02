# Conversation scenarios

`tests/scenarios.yaml` holds 15 conversations run against the real model by
`scripts/run_scenarios.py`. Each one freezes the store clock (`now`, in `America/Campo_Grande`)
and checks objective things only: the tools that must be called, the text that must appear in
Mel's replies and the text that must not (case-insensitive). There is no LLM judge.

```bash
make scenarios                                                   # all 15
uv run python scripts/run_scenarios.py --only <id>               # one scenario
uv run python scripts/run_scenarios.py --export                  # also write examples/
```

One full run costs about 108k tokens on Groq's free tier, which fits about one run a day (see
"Token budget" in the README).

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

## Results

> **To be updated after the 03/11 run.** The fixes from the review of the examples on 02/11 and
> the stricter checks above have not yet been through a full run.

History so far (Groq `openai/gpt-oss-120b`):

| Date | Run | Result | Notes |
| --- | --- | --- | --- |
| 01/11/2026 | Full runs 2 and 3 | 8/15, then 11/15 | Each failure was fixed in code (prices composed by the tools, active campaigns listed by the policy tool, the protocol guard, retries on 502/503), not by editing the expected text |
| 02/11/2026 | Four failing scenarios, one at a time | 4/4 | The language guard was added for the English scenario |
| 02/11/2026 | Full run with `--export` | 15/15 | 41 calls, about 108k tokens; produced the current `examples/` |
| 02/11/2026 | After the review fixes, one at a time | 1 run, 1 pass | `regret-within-window` passed; the rest waits for the daily quota |

Results vary between runs even at temperature 0: a pass is a measurement, not a guarantee.
