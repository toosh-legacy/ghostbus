---
name: classify
version: 1
---
Classify a question for a read-only MBTA bus reliability copilot.

kind:
- "data": asks for a reliability number (on-time performance, headway adherence, excess wait time, bunching,
  missed trips) for the pilot bus routes in months $first_month to $last_month.
- "methodology": asks how something is measured, defined or sourced ("how is bunching measured", "what counts as a
  ghost bus", "where does the data come from").
- "out_of_scope": anything else: ridership, crowding, fares, weather, subway or rail, rider data, opinions, and any
  forecast or month after $last_month.

Every month from $first_month to $last_month is in the past and answerable.
Give a one-sentence reason.

Question: $question
