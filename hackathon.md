# Outofoffice — neatHack 2026 build log

Oct 10–12, 2026. Python 3.12 · LangGraph · neatlogs · Entire · cfo.ai.

## Evidence

| What | Link / ID |
| --- | --- |
| First Entire checkpoint | `01M4K4X70XBEWAR585HJYFT0FD` |
| Trace 1 — failure (export: `evidence/trace-1-before-failure.md`) | https://app.neatlogs.com/traces/41e85866c817110ff13d29aa5187e9ed?orgId=a2292b54-4117-446e-a9eb-91a59c618856&projectId=6c876950-22f7-4cc3-b50e-c789cde07355 |
| Trace 2 — recovery (export and screenshot: `evidence/trace-2-recovery.*`) | https://app.neatlogs.com/traces/718e0562e66dcc7519c40cabcae58525?orgId=a2292b54-4117-446e-a9eb-91a59c618856&projectId=6c876950-22f7-4cc3-b50e-c789cde07355 |
| Trace 3 — before / after (export and screenshot: `evidence/trace-3-after.*`) | before: Trace 1 above · after: https://app.neatlogs.com/traces/a78e09b753d992ced6513b1226bdf962?orgId=a2292b54-4117-446e-a9eb-91a59c618856&projectId=6c876950-22f7-4cc3-b50e-c789cde07355 |
| Fix commit checkpoint | `01M4P95QFYBZCETGSRHD212NQ0` (commit `05ea6d0`) |
| Business plan | `business-plan.md` |
| Demo video | |

## Improvement loop (Section 10)

Scenario: `OO_SCENARIO=closed python -m app.graph`, where every venue in range must be refused.
Numbers are read from the neatlogs trace of one run; neatlogs could not price the model
(`pricing_status: unpriced`), so cost is given as a share and not in money.

How the evidence is presented (mentor's guidance, Mon Oct 12): judges accept trace links or
screenshots, so both are given. The README links the three specific runs, shows screenshots,
and carries the before and after table. Full exports of the three traces and the Investigate
answer are in `evidence/`, which opens without a neatlogs login.

| | before (trace `41e85866…`) | after (trace `a78e09b7…`) | change |
| --- | --- | --- | --- |
| trace latency | 5.4 s | 2.8 s | 48% faster |
| wall clock per run, 5 runs, incl. start-up | 7.3 s | 4.3 s | 41% faster |
| model calls | 3 | 1 | |
| tokens | 1,956 (1,799 in, 157 out) | 678 (626 in, 52 out) | 65% fewer |
| cost | not priced by neatlogs (`unknown_model`) | same | about 65% lower at any per-token price |
| search radius per pass | 15 km, 15 km, 15 km | 15 km, 37.5 km, 60 km | |
| venues checked by the verifier | 2 | 4 | |
| outcome | silent, correctly, 5 of 5 | silent, correctly, 5 of 5 | |

Wall clock is the gap between consecutive "SDK shutdown complete" lines in the terminal. Cost
is not a measured figure: input and output tokens both fell by about two thirds on the same
model, so the cost fell by the same share whatever the price per token is.

In the after trace each replan took 5 ms: the first found Chapora Fort (refused, closed) and
the second found Cabo de Rama Fort (refused, its reel places it about 55 km away).

neatlogs Investigate on that trace (asked: why did it replan, was the replanning useful, what
should change):

- Root cause: on both replans the scout's model kept the same 15 km radius, so the search
  returned only venues that were already refused. Both replans produced `candidates: []` and
  the run ended. Two venues just outside the radius (Chapora Fort, Cabo de Rama Fort) were
  returned by every search and never checked.
- Recommended fix 1: widen the radius on each replan. Done, without a model call: a replan
  reaches halfway to the limit, then all the way, and adds categories not tried yet. When
  nothing is left to change the run ends at once.
- Recommended fix 2: do not look for evening things in the morning, or judge a venue by when
  it would be visited. The scout is now told the local time. The verifier stays strict on
  purpose: a nudge means "go now", so a market that opens at 18:00 is rightly refused at 09:00.
- One correction to the report: in this fixture the two further venues are also unusable (one
  closed, one misplaced by its reel), so the fixed run still ends silent. What changes is that
  it checks all four venues with one model call instead of two venues with three.

Detections created in neatlogs (Mon Oct 12): "Venue refused: closed" (regex on the verifier's
output, 88 steps flagged), "Too many model calls in one run" (trace rule, 19 runs flagged: the
before runs are in it and the five after runs are not), "Scout fell back to default query"
(regex, nothing flagged) and "Personal data in prompts" (PII on LLM spans, nothing flagged).
Screenshots are in `evidence/`.

Recovery trace (`python -m app.graph goa_2day`, trace `718e0562…`): the traveller wants a trek
and a sunset with two days left. The planner first put Dudhsagar Falls and Chapora Fort on day 1;
the verifier refused the plan ("Dudhsagar Falls ends 16:00 and Chapora Fort is 2.3 h away:
arrival 18:20, needed by 17:30"); the replan moved the sunset to day 2 and the verifier passed
it. 3.2 s, 34 spans, one model call (935 tokens). The replan itself took 4 ms and no model call.

## Log

### Sat Oct 10

- Section 0: tooling — Entire, neatlogs, repo skeleton.

### Sun Oct 11

- Plan change: the agent must work on live input, not only on fixtures. `app/runtime.py`
  takes one location update and returns at most one nudge; the simulator and any live
  client both call it. With `OO_FIXTURES=0` places come from OpenStreetMap (Overpass) and
  weather from Open-Meteo. Events, videos and shops have no live provider yet.
- Plan change: a final section is added after Section 12's build work. The traveller talks
  to the agent over WhatsApp (messages in, nudges out), on top of the same runtime.
