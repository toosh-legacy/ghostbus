# Methodology

Published verbatim from spec sections 9.4–9.5 (served live at `/v1/methodology` and on the web `/methodology` page).
Phase B: the developer should add their own reasoning for each threshold below.

## Reliability metrics (metrics-def v1.0)

- **on_time_performance** — On-time performance (schedule-standard trips): Share of timepoint events with `actual` between 1 minute early and 6 minutes late of `scheduled` at midpoints/endpoints, and departing no more than 1 minute early at startpoints. The thresholds are config values; document the ones used. [Thresholds used: -60 s to +360 s at all point types (startpoints use the same late bound).]
- **headway_adherence** — Headway adherence (headway-standard trips): Share of events where the actual headway is within ±50% of the scheduled headway (configurable).
- **excess_wait_time** — Excess wait time: Average wait implied by actual headways minus the wait implied by scheduled headways, for headway-standard service. Average wait = sum(h²) / (2 · sum(h)).
- **bunching_rate** — Bunching rate: Share of events where the actual headway is below 25% of the scheduled headway.
- **missed_trip_rate** — Missed-trip rate: Missed trips / scheduled trips over route-days that are not unknown (section 9.4).
- **coverage_of_intervals** — Coverage of intervals: Share of observed arrivals that fell inside the predicted 80% range.

Thresholds in use: `{'otp_early_s': -60, 'otp_late_s': 360, 'headway_tolerance': 0.5, 'bunching_fraction': 0.25, 'completeness_min': 0.7}`

## Ghost and missed-trip definitions (ghost-def v1.1)

- **historical_missed_trip** — Historical missed trip (from the MBTA Bus Arrival Departure Times data): A scheduled trip of a pilot route on a service date has no timepoint events at all. Route-days whose completeness score is below 70% are marked unknown, not missed, because the data itself is incomplete. [Implementation: a trip with no events = a half-trip whose rows all have a null `actual`; completeness = half-trips with at least one observed event / GTFS scheduled trips for that route and date.]
- **high** — High: Scheduled departure from the trip's first stop was more than 5 minutes ago; no vehicle has been matched to the trip; the feed is healthy.
- **elevated** — Elevated: Departure is within 10 minutes and no vehicle is assigned yet, or the trip appears in trip updates with a prediction but no vehicle has been seen for more than 3 minutes.
- **unknown** — Unknown: the feed is unhealthy (no successful fetch in 2 minutes, or the message rate is below 30% of the same hour's typical rate).
- **low** — Low: a vehicle is matched and progressing.
- **reassignment_v1_1** — v1.1 addition: a trip that would be High is Elevated instead when a vehicle on the same route and direction is running its claimed trip more than 10 minutes ahead of schedule and departed within 15 minutes of this trip's scheduled departure (a likely wrong trip assignment, not a missing bus).
- **prior** — The missed-trip classifier supplies the prior probability, which the UI shows with the rule state.

Version history: v1.0 = spec 9.4 rules; v1.1 adds the wrong-trip-assignment rule (High → Elevated). Stored ghost evaluations keep the version that produced them.

## Data sources and licenses

- [MBTA static GTFS (current + archive)](https://cdn.mbta.com/MBTA_GTFS.zip) — MassDOT Developers License Agreement. Schedule data courtesy of MassDOT/MBTA.
- [MBTA Bus Arrival Departure Times 2025](https://gis.data.mass.gov/content/924df13d845f4907bb6a6c3ed380d57a) — CC0. Massachusetts Bay Transportation Authority (public domain dedication).
- [MBTA GTFS-Realtime (VehiclePositions, TripUpdates)](https://cdn.mbta.com/realtime/) — MassDOT Developers License Agreement. Real-time data courtesy of MassDOT/MBTA.
- [OpenStreetMap tiles (map display)](https://www.openstreetmap.org/copyright) — ODbL. © OpenStreetMap contributors

## Known data gaps

- The MBTA notes arrival/departure data is not guaranteed complete for any stop or date; route-days below 70% completeness are reported as unknown.
- Arrival/departure data covers timepoints only, not every stop.
- Live recording started 2026-11-23; replays only cover recorded periods.

## Evidence

- Backtests: `ml/reports/latest.md`
- Evaluation gates: `evals/reports/latest.md`
- Data quality: `data/reports/arrdep_profile.md`
