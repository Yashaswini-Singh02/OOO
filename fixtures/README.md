# Fixtures

Stand-in tool responses used while `OO_FIXTURES=1`. They are test data, not facts:

- Place names are real; coordinates are approximate.
- Costs, durations, distances and `open_now` values are made up for testing.
- Shop names and video URLs are placeholders.

Files are named `<kind>_<scenario>.json` and `OO_SCENARIO` picks the scenario:

- `goa` (default): the normal run, where one event is closed and the rest pass.
- `closed`: every candidate fails the verifier (two closed, one that does not exist, one
  whose reel places it about 55 km from where it is). "Hidden Lagoon Viewpoint" is invented.

`distance_m` is measured from the simulated start point in Fontainhas, Panaji.

`route_goa.json` is the simulated walk: 15 timestamped points from Panaji to Arpora, three
saved spots, and a rain shower at 13:10. The route and its times are invented for the demo.
