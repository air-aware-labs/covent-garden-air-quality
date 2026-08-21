# Live site builder

This directory contains the public-safe source required to refresh the deployed
standalone model. GitHub Actions calls the AirGradient and Tempest APIs, rebuilds
the five-minute records, assembles the page, and deploys only the generated HTML.

Provider credentials, provider location/device identifiers, the private monitor
registry, SQLite archives, and generated API exports are excluded from Git. They
are supplied to Actions through encrypted repository secrets. A rolling Actions
cache retains the SQLite archives between otherwise ephemeral runners.

The checked-in geometry is the same reduced, identity-free geometry that enters
the browser. It carries 81a's pitched solar roof and raised rear garden, and a
single east-facing communal deck-access gallery beside the private western roof
garden. Twelve selected supplied photographs—including the 19 August
installation evidence for units 1–4—are already present in the published
standalone page and are retained here only so the automated builder can reproduce
that approved artifact.
