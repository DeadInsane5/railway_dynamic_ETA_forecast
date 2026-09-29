# Demo recording guide — 60 seconds

Teleprompter script + click cues for a hackathon-judge demo of the Dynamic ETA
Forecast prototype. Everything on screen is **simulated**; say so in the first
beat. Narration totals 66 words (~26 s spoken), leaving ~34 s for the clicks
and the pauses between them.

Read the **bold** text aloud. Everything else is direction for you.

---

## Pre-flight (do all of this BEFORE you press record)

1. **Reset to a clean state.** The audit log currently holds 346 entries from
   testing and the clock will not be at 10:00. With the server stopped:

   ```bash
   rm -f data/audit.jsonl data/learning_state.json
   ```

   This resets the clock to 10:00 and recreates the chain on next boot. The
   audit page then shows a short, readable trail instead of a wall of
   `jidoka_fallback` rows.

2. **Start the server** and wait for it to settle (~2 s import, plus one-time
   model load — give it 10 s before you open the browser):

   ```bash
   .venv/bin/python -m uvicorn app.main:app --port 8000
   ```

3. **Check your internet.** All three pages load UX4G CSS and JS from
   `https://cdn.ux4g.gov.in/UX4G@3.2.0/`. No connection means an unstyled page
   and a dead demo. Confirm by loading `http://localhost:8000` and seeing the
   navy/saffron header — not raw HTML.

4. **Browser setup.** Zoom to **100%** (the delay matrix is 41 columns wide and
   will not fit at 125%). Use a 1920×1080 window at full screen so the KPI strip
   and the matrix are both visible without scrolling. Close any notification
   popups and bookmark bar.

5. **Have the train dropdown ready to go.** The two most likely failures in this
   demo are both about train selection — see *Troubleshooting* below.

---

## The 60-second script

### Beat 1 — Control room, on boot · `0:00–0:12`

**Open `http://localhost:8000` and leave the mouse still.** The header KPI strip
and the delay-matrix heatmap are already populated.

> **"Dynamic arrival-time forecast. Twelve stations, forty trains, one
> corridor. All data simulated."**

*Direction:* let it land. Do not click yet. The heatmap filling the lower half
of the screen does the visual work for you.

---

### Beat 2 — Time moves · `0:12–0:20`

**Click `Advance 5 min`** (top-right of the Trains card). The clock flips
10:00 → 10:05 and every ETA row shifts.

> **"Time moves. Every forecast recomputes."**

*Direction:* one click only. If you click twice the heatmap diff gets muddy.

---

### Beat 3 — Not a black box · `0:20–0:31`

**Open the `Train` dropdown and select `T113`.** The ETA table and the
Explanation panel repopulate.

> **"Not a black box — it names the driver. Restriction near station twelve,
> five minutes."**

*Direction:* the explanation panel sits bottom-right. The words
*"+5 min from restriction near S12"* are genuinely on screen — point at them if
the shot is wide enough.

> **This is the single most important click in the demo.** Skipping it breaks
> Beats 4 and 5.

---

### Beat 4 — What-if · `0:31–0:39`

**Click `Hold 10 min at mid-journey station`.** The result panel fills with the
downstream effect.

> **"What-if: hold this train ten minutes. The delay absorbs and decays."**

*Direction:* the numbers you are pointing at are **S9 +6.5 min, S10 +3.0,
S11 +0.5** — the hold is absorbed, not passed on whole. That decay *is* the
story; mention it.

---

### Beat 5 — The passenger sees it · `0:39–0:50`

**Click `Passenger` in the nav.** The table shows T113's upcoming arrivals.
**Click the `HI` button.**

> **"And the same forecast reaches the passenger, in their language."**

*Direction:* thirteen labels switch to Hindi together. Pause half a beat after
the click so the change registers — this is the most visually satisfying moment
in the video and it happens in under a second.

---

### Beat 6 — Signed and tamper-evident · `0:50–1:00`

**Click `Audit` in the nav, then click `Verify chain`.**

> **"Signed, tamper-evident. Every decision on the record."**

*Direction:* the green **"Verified — chain intact"** banner appears. End on it.
Optionally close with a single line:

> **"Production architecture. Simulated data."**

---

## Things NOT to say (they are not true)

I checked these against the code so you don't get caught by a judge who reads
the README.

- **Do not say approving a Jidoka item changes the forecast.** It does not. The
  router keys off feed age and quarantine state, not queue status — I verified
  the ETA payload is byte-identical before and after an approval. The six
  Jidoka items in the queue are *waiting on a human*; that is the honest
  framing, and it is the framing the script above uses.
- **Do not quote MAE or coverage numbers as accuracy.** They are fitted on
  simulated data. The README says this explicitly and so should you.
- **Do not say the Laya system is real.** It is an sklearn stand-in behind a
  Laya-shaped interface. If a judge asks, answer straight — you'll get further
  than by bluffing.
- **Do not call the 33 fallback trains failures.** See below.

---

## The "33 fallback" KPI — know your answer

The header shows **Fallback count: 33** out of 40 trains, and a judge will read
that as "a third of the fleet is broken."

It isn't. **26 of those 33 are trains that haven't departed yet** — the router
sends them to the published schedule because there is no data for them yet,
which is correct behaviour. Counted over the **14 trains actually running**, it
is **7 fallback / 7 full**.

If asked, say:

> **"Thirty-three trains are on the schedule fallback, but twenty-six of those
> haven't departed yet — there's no live data for them, so schedule is the
> honest answer. Of the fourteen running, seven are on the full model and seven
> have gone to fallback because their feed went stale."**

That answer is a strength, not a defence. It shows you know the system's
boundaries.

---

## Troubleshooting

| What you see | Cause | Fix |
|---|---|---|
| Passenger table is empty | T101 is the default and its journey is **complete** (stop 11 of 11) | Select T113 in the dropdown on the Control Room first. Beat 3. |
| What-if shows *"this one completed its journey"* | Same cause — same fix | Select T113 |
| Page is unstyled / white | CDN blocked, no internet | Check connectivity, reload |
| "Fallback count" and matrix look different from these notes | Server was not reset | Stop server, `rm -f data/audit.jsonl data/learning_state.json`, restart |
| `Advance 5 min` seems to do nothing | Button is disabled while the request is in flight | Wait a second; it re-enables itself |
| Learning says "frozen by Jidoka line-stop" | Drift guard tripped | `rm -f data/learning_state.json data/residual.pkl`, restart |

---

## Appendix — the learning cycle (optional extra clip)

Not part of the 60 seconds. It takes **~30 seconds** and is one-shot: the first
press promotes, the second rejects, then the script is exhausted until you
reset. Record it separately if you want it.

**Reset first:**

```bash
rm -f data/learning_state.json data/residual.pkl
```

**Then, on the Control Room, scroll to the Learning card:**

1. Click `Run learning cycle`. A spinner appears for ~30 s. **Talk over it** —
   this is the one place you have dead air to fill.
   > "The challenger model trains on a rolling holdout. It has to beat the
   > champion on error *and* hold coverage near eighty percent."
2. First press returns **`PROMOTED`** (green). The `Model version` KPI advances
   to `champion · cycle 1`.
3. Click again. Returns **`REJECTED`** (amber) — the degraded challenger's
   bands are too narrow, so it fails the coverage gate even though its median
   is fine.

That two-beat promote-then-reject is the strongest single piece of evidence
that the learning loop has a real acceptance gate and not just a rubber stamp.
It is worth 30 seconds of its own clip.
