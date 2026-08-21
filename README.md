# Covent Garden air-quality site model

Interactive evidence-led site model for the rear roofscape at 74–75 County
Street, London. It brings together five-minute PM2.5 readings, roof weather,
site geometry and supplied photographs in one self-contained page.

**Live site:** https://air-aware-labs.github.io/covent-garden-air-quality/

## What it is for

- compare the latest and historic AirGradient records;
- select the outdoor average or an individual monitor;
- inspect the wind at the selected time;
- play or scrub the five-minute record while the PM graph and wind rose remain visible;
- explore the installed network and compare it with the historic co-location;
- check the photographs and spatial assumptions behind the model.

## Read this first

The plume is on by default because exploring its wind-driven movement is a main
purpose of the model. It remains an illustration driven by the chosen wind and
an assumed operating profile—not measured emissions, a CFD result, or proof
that an outlet was operating—and can be switched off under Layers. Unit 5 is
indoors and is interpreted on its own scale. Outlet locations, monitor heights
and plan positions are approximate rather than surveyed. The installed hosts and
mounting surfaces are confirmed in the supplied 19 August photographs: units 1
and 4 share the timber rail beside Tempest, unit 2 is at 81a beside the solar
panels, unit 3 is on the western roof terrace, and unit 5 remains indoors.

The page is rebuilt from both provider APIs four times a day, at roughly 00:07,
06:07, 12:07 and 18:07 UTC. Each rebuild collects every five-minute observation
recorded since the previous one, so the record itself stays complete and at full
resolution—it is the most recent hours that can be missing, by up to six. It is
also a static publication artifact rather than a browser-to-provider connection:
API observations themselves can arrive several minutes behind real time, and
scheduled GitHub runs can occasionally be delayed. The interface always shows
the actual latest timestamp included, in London local time (BST/GMT), so what is
on the page is never in doubt.

## Publication boundary

This repository contains the generated publication page and the stripped source
needed to reproduce its scheduled refresh. API credentials, provider IDs,
instrument serial numbers, the private monitor registry and rolling databases
are not included. The precise site context, supplied photographs and indoor
Unit 5 record are published with the site owner's approval.

Editable AirGradient dashboard names are not used as identity keys. The private
registry maps stable provider identifiers to unit numbers, so renaming a sensor
does not silently move its measurements to another scene position.

See [NOTICE.md](NOTICE.md) for data, image and software attribution.
