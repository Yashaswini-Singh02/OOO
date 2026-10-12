# OOO (Outofoffice) business plan

Modelled in cfo.ai on Oct 12, 2026, from token counts measured in neatlogs and costs billed by
OpenRouter. The model is three tables on the "OOO business plan" page of the cfo.ai workspace
(cost inputs, pricing, and scale and runway) and two scenarios: today's costs, and cached lookups. This file is the export, so it opens without a
cfo.ai login.

All amounts are US dollars per month.

## What the plan says

1. **One traveller costs about $2.14 a month to serve, and 98% of that is paid place and web
   lookups.** The model is $0.04.
2. **$3 does not work yet.** It leaves 16% gross margin. A $3 plan reaches 70% only if cost to
   serve falls to $0.51, which means cutting paid lookups by about 78%. Caching them is the next
   step: with lookups cut by 80%, the $3 plan reaches 72%.
3. **$8 is the only price close to the 70% target** at today's cost: 67%, or 69% with affiliate
   revenue. $5 gives 48%.
4. **At 100 paying travellers, $3 loses money and $5 and $8 do not.** On $500 of starting cash
   the $3 plan lasts about 9 months. From 1,000 travellers every price covers the fixed spend.
5. **The neatlogs fix is a line in this plan.** It cut the billed model cost of a nudge check
   from $0.000256 to $0.000088, 66% lower.

So the work that decides this business is not the model bill. It is how often a traveller's
activity triggers a paid lookup.

## Which numbers are measured

| Input | Value | Basis |
| --- | --- | --- |
| Tokens per nudge check | 626 in, 52 out | Measured, neatlogs trace `a78e09b7…` |
| Tokens per nudge check before the fix | 1,799 in, 157 out | Measured, neatlogs trace `41e85866…` |
| Tokens per trip plan | 636 in, 299 out | Measured, neatlogs trace `718e0562…` |
| Model price per 1M tokens | $0.10 in, $0.50 out | Measured: fitted to 32 billed calls in the OpenRouter activity export, to within $0.000001 a call |
| Tokens per chat model call | 1,246 in, 308 out | Measured: OpenRouter totals for Oct 11 (217 calls, 250K prompt, 59K completion and reasoning) less the 32 exported calls |
| Paid lookups per model call | 0.33 place, 0.12 web | Measured: the app's usage log for Oct 11 (72 place, 26 web) over 217 model calls. Some of those calls were fixture runs with no lookups, so live use is higher |
| Model calls per chat message | 3 | Assumption |
| Place lookup price | $0.032 | Assumption |
| Web search price | $0.008 | Assumption |
| Use per traveller per month | 60 nudge checks, 2 trip plans, 40 chat messages | Assumption |
| Payment fees | 3% + $0.30 | Assumption |
| Affiliate revenue per traveller | $0.50 | Assumption |
| Fixed spend | $100 | The builder's figure: $50 of running costs and a $50 neatlogs subscription |
| Starting cash | $500 | The builder's figure: their own money |
| Gross margin target | 70% | Chosen |

One whole day of building and testing (Oct 11) cost $0.05 in model calls. The trace links are in
[`README.md`](README.md).

## Cost to serve one traveller

| | Per event | Per month |
| --- | --- | --- |
| Model, per nudge check | $0.000089 | |
| Model, per trip plan | $0.000213 | |
| Model, per chat message (3 calls) | $0.000836 | |
| Model, total | | $0.04 |
| Lookups, per model call | $0.0115 | |
| Lookups, total (182 model calls) | | $2.10 |
| **Cost to serve** | | **$2.14** |

## Pricing

| | $3 plan | $5 plan | $8 plan |
| --- | --- | --- | --- |
| Payment fee | $0.39 | $0.45 | $0.54 |
| Gross margin per traveller | $0.47 | $2.41 | $5.32 |
| Gross margin | 16% | 48% | 67% |
| Gross margin with affiliate revenue | 28% | 53% | 69% |
| Cost to serve that gives 70% | $0.51 | $1.05 | $1.86 |

## Scale and runway

Net is gross profit less the $100 fixed spend. Runway is $500 of starting cash divided by the
monthly loss, and is shown only where the case loses money.

| Price | Paid travellers | Revenue | Gross profit | Net | Net with affiliate | Runway |
| --- | --- | --- | --- | --- | --- | --- |
| $3 | 100 | $300 | $47 | -$53 | -$3 | 9.5 months (about break-even with affiliate) |
| $3 | 1,000 | $3,000 | $474 | $374 | $874 | not burning |
| $3 | 10,000 | $30,000 | $4,742 | $4,642 | $9,642 | not burning |
| $5 | 100 | $500 | $241 | $141 | $191 | not burning |
| $5 | 1,000 | $5,000 | $2,414 | $2,314 | $2,814 | not burning |
| $5 | 10,000 | $50,000 | $24,142 | $24,042 | $29,042 | not burning |
| $8 | 100 | $800 | $532 | $432 | $482 | not burning |
| $8 | 1,000 | $8,000 | $5,324 | $5,224 | $5,724 | not burning |
| $8 | 10,000 | $80,000 | $53,242 | $53,142 | $58,142 | not burning |

Affiliate revenue matters most at $3: at 100 travellers it takes the plan from losing $53 a
month to about break-even, and at 1,000 it lifts net from $374 to $874. Without it, the $3 plan
depends entirely on cutting lookup cost.

## Next step: cache the place lookups

The place search is the one paid lookup the app does not cache: every scout run asks Google
again, even when the traveller has not moved. Caching it by area (and working out "open now"
from stored opening hours) is the change this plan points to. It is not built yet.

The cfo.ai model has a second scenario, "Cached lookups", with paid lookups per model call cut
by 80% (0.33 to 0.066 place, 0.12 to 0.024 web). The 80% is a target, not a measurement.

| | Today | Cached lookups |
| --- | --- | --- |
| Lookup cost per traveller | $2.10 | $0.42 |
| Cost to serve per traveller | $2.14 | $0.46 |
| Gross margin at $3 | 16% | 72% |
| Gross margin at $5 | 48% | 82% |
| Gross margin at $8 | 67% | 88% |
| Net at 100 travellers on the $3 plan | -$53 | $115 |
| Net at 1,000 travellers on the $3 plan | $374 | $2,051 |
| Net at 10,000 travellers on the $3 plan | $4,642 | $21,415 |

With cached lookups the $3 plan clears the 70% target and stops losing money at 100 travellers.
A shared cache also means cost per traveller falls as more travellers visit the same places.

## What this plan does not cover

- **How travellers are won.** There is no cost of acquiring a traveller and no conversion from
  free to paid, so the scale table says what each size is worth, not how to reach it.
- **Travel is not monthly.** A traveller uses OOO hard for a few days and then not at all, apart
  from the touch-grass nudges at home. Charging per trip may fit better than a monthly plan and
  is the next thing to model.
- **Lookups in live use.** The lookup rate comes from one day of testing by the builder. It is
  the number that decides the $3 plan and needs measuring on real travellers.
- **The builder's time.** Fixed spend counts running costs only, with no pay for the builder.
