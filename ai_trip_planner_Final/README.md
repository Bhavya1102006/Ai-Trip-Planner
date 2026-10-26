# AI Trip Planner (Stage 2 prototype)

Turns a free-text trip brief into a day-wise itinerary that respects opening
hours, travel time, pace and a daily budget.

    python -m tripplanner "3 days in Jaipur, budget 3000 per day, heritage and food, relaxed pace"
    python -m tripplanner "2 days in Jaipur, budget 2500 per day, shopping" --json
    python -m tripplanner "..." -v          # progress logs on stderr (-vv for debug)
    python -m unittest discover -v          # run all tests
    python -m scripts.evaluate              # SDG distance / CO2 comparison

Layout: `tripplanner/` (parser, geo, planner, explainer, cli, places, llm, storage, server, service), `data/` (POI data),
`tests/`, `scripts/`. Python 3.10+. The planner and tests need only the standard library; hosting and PostgreSQL use requirements.txt.

Cloud features (live places, language model, web API, database) are optional; see DEPLOYMENT.md.

    python -m tripplanner "..." --live --llm     # live places + language model
    python -m tripplanner.server                  # JSON web API
    python -m scripts.check_cloud                 # test your real keys

Note: the bundled POI file is a small curated sample (approximate coordinates and hours).
The Places/Weather API connectors from the Stage 1 design replace it later.
