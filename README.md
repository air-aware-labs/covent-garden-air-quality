# Covent Garden air-quality site model

Interactive evidence-led site model for the rear roofscape at 74–75 County
Street, London. It brings together five-minute PM2.5 readings, roof weather,
site geometry and supplied photographs in one self-contained page.

**Live site:** https://air-aware-labs.github.io/covent-garden-air-quality/

## What it is for

- compare the latest and historic AirGradient records;
- select the outdoor average or an individual monitor;
- inspect the wind at the selected time;
- compare the co-location and proposed deployment layouts;
- check the photographs and spatial assumptions behind the model.

## Read this first

The plume is off by default. If selected, it is an illustration driven by the
chosen wind and an assumed operating profile—not measured emissions, a CFD
result, or proof that an outlet was operating. Unit 5 is indoors and is interpreted on its own scale. Outlet
locations, monitor heights and proposed positions are approximate rather than
surveyed.

The published page is a static snapshot, not a live API connection. Its
interface shows the latest timestamp included in the snapshot in London local
time (BST/GMT).

## Publication boundary

This repository contains only the generated publication page and required
attribution. API credentials, provider IDs and instrument serial numbers are
not included. The precise site context, supplied photographs and indoor Unit 5
record are published with the site owner's approval.

See [NOTICE.md](NOTICE.md) for data, image and software attribution.
