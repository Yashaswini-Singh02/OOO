# Outofoffice — neatHack 2026 build log

Oct 10–12, 2026. Python 3.12 · LangGraph · neatlogs · Entire · cfo.ai.

## Evidence

| What | Link / ID |
| --- | --- |
| First Entire checkpoint | `01M4K4X70XBEWAR585HJYFT0FD` |
| Trace 1 — failure | https://app.neatlogs.com/traces/41e85866c817110ff13d29aa5187e9ed?orgId=a2292b54-4117-446e-a9eb-91a59c618856&projectId=6c876950-22f7-4cc3-b50e-c789cde07355 (app link; replace with the public share link before submitting) |
| Trace 2 — recovery | |
| Trace 3 — before / after | |
| Fix commit checkpoint | |
| Business plan | `business-plan.md` |
| Demo video | |

## Improvement loop (Section 10)

Scenario: `OO_SCENARIO=closed python -m app.graph`, where every venue in range must be refused.
Numbers are read from the neatlogs trace of one run; neatlogs could not price the model
(`pricing_status: unpriced`), so cost is still to be filled in from OpenRouter.

| | before (trace `41e85866…`) | after |
| --- | --- | --- |
| latency | 5.4 s | |
| model calls | 3 | |
| tokens | 1,956 (1,799 in, 157 out) | |
| cost | to fill from OpenRouter | |
| venues checked by the verifier | 2 | |
| outcome | silent, correctly | |

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
