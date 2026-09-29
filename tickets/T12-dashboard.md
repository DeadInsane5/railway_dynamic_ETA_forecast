# T12 Control room dashboard (UX4G)
**File:** app/static/index.html (+ style.css, app.js). **Budget:** 15 min. **Depends:** T09.
Design: Government of India UX4G look. Use the official UX4G CSS/tokens if they can be downloaded (ux4g.gov.in) and vendored into `static/vendor/`; the check was not made here, so if unavailable, emulate: header with tricolour accent strip and "Ministry of Railways" title, Noto Sans, navy primary, 8px grid, cards, accessible contrast (WCAG AA), visible focus states. State this honestly in the README.
Panels (single page, top nav tabs: Control Room | Passenger | Audit):
1. Header KPIs: trains tracked, on-time %, fallback count, model version, "Simulated data" badge.
2. Train table: select a train -> ETA range bars per station (p10-p90 with p50 marker).
3. Delay matrix heatmap (stations x trains, from `/api/matrix`).
4. Regime + router activity (counters, per-train regime chip).
5. Jidoka queue with Approve/Dismiss buttons.
6. Explanation card for the selected train + What-if button ("hold 10 min at loop").
7. Buttons: Advance 5 min, Run learning cycle; scoreboard table of model vs baseline MAE per horizon.
Vanilla JS + fetch, no build tools. Mobile-friendly.
**Done:** all panels populate from the API; Advance visibly changes ETAs.
