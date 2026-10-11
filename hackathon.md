# Outofoffice — neatHack 2026 build log

Oct 10–12, 2026. Python 3.12 · LangGraph · neatlogs · Entire · cfo.ai.

## Evidence

| What | Link / ID |
| --- | --- |
| First Entire checkpoint | `01M4K4X70XBEWAR585HJYFT0FD` |
| Trace 1 — failure | |
| Trace 2 — recovery | |
| Trace 3 — before / after | |
| Fix commit checkpoint | |
| Business plan | `business-plan.md` |
| Demo video | |

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
