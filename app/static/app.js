/* T12 control-room dashboard. Vanilla JS + fetch, no build step.
 * Every panel reads the INDEX-contract API (all responses simulated). */
(function () {
  "use strict";

  var state = { trains: [], selected: null, eta: null };

  /* Regime table: trains that are actually under way, capped so the card
   * stays above the fold. Two categories are dropped, for the same reasons as
   * the Jidoka queue: a train that has not departed has no observed regime and
   * no path decision worth watching (the router only sends it to fallback
   * because there is nothing to forecast from), and a train that has reached
   * its last stop is finished. The cap is a safety valve -- on the demo data
   * the en-route count sits at 10-12 on its own. */
  var REGIME_ROWS_MAX = 12;

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function hhmm(minutes) {
    var m = Number(minutes);
    if (!isFinite(m)) return "--:--";
    m = ((m % 1440) + 1440) % 1440;
    var h = Math.floor(m / 60), mm = Math.floor(m % 60);
    return (h < 10 ? "0" + h : "" + h) + ":" + (mm < 10 ? "0" + mm : "" + mm);
  }

  function getJSON(url, opts) {
    return fetch(url, opts).then(function (r) {
      if (!r.ok) throw new Error(url + " -> HTTP " + r.status);
      return r.json();
    });
  }

  function pageError(msg) {
    $("page-error").innerHTML =
      '<div class="ux4g-alert ux4g-alert-error"><div class="ux4g-alert-title">Error</div>' +
      '<div class="ux4g-alert-message">' + esc(msg) + "</div></div>";
  }

  function regimeBadge(regime) {
    var map = { grows: "warning", stable: "success", recovers: "info" };
    var cls = map[regime] || "neutral";
    return '<span class="ux4g-badge-digit-' + cls + ' ux4g-badge-m">' + esc(regime || "?") + "</span>";
  }

  function pathBadge(path) {
    var map = { full: "primary", fallback: "warning", cheap: "info" };
    var cls = map[path] || "neutral";
    return '<span class="ux4g-badge-digit-' + cls + ' ux4g-badge-m">' + esc(path || "?") + "</span>";
  }

  /* -- panels ---------------------------------------------------------- */

  function renderKPIs(jidoka) {
    var trains = state.trains;
    var onTime = trains.filter(function (t) { return Number(t.cur_delay) <= 5; }).length;
    var fallback = trains.filter(function (t) { return t.path === "fallback"; }).length;
    $("kpi-trains").textContent = trains.length;
    $("kpi-ontime").textContent = trains.length
      ? Math.round((100 * onTime) / trains.length) + "%" : "–";
    $("kpi-fallback").textContent = fallback;
    $("kpi-jidoka").textContent = jidoka && jidoka.pending ? jidoka.pending.length : "–";
  }

  function renderModelVersion() {
    // Side-effect-free version label: latest promotion/rejection cycle in audit.
    getJSON("./api/audit?limit=50").then(function (d) {
      var entries = (d.entries || []).filter(function (e) {
        return e.kind === "promotion" || e.kind === "rejection";
      });
      var last = entries[entries.length - 1];
      var label = "champion · cycle 0";
      if (last && last.payload && last.payload.cycle != null) {
        label = "champion · cycle " + last.payload.cycle;
      }
      $("kpi-model").textContent = label;
    }).catch(function () { $("kpi-model").textContent = "champion · cycle 0"; });
  }

  function renderTrainOptions() {
    var sel = $("train-select");
    sel.innerHTML = state.trains.map(function (t) {
      return '<option value="' + esc(t.train_id) + '">' +
        esc(t.train_id + " — " + (t.name || "")) + "</option>";
    }).join("");
    if (!state.selected && state.trains.length) state.selected = state.trains[0].train_id;
    sel.value = state.selected;
  }

  function renderRegimeTable() {
    var underWay = state.trains.filter(function (t) {
      return Number(t.cur_ts) > 0 && t.next_station != null;
    });
    var shown = underWay.slice(0, REGIME_ROWS_MAX);
    var rows = shown.map(function (t) {
      return "<tr><td>" + esc(t.train_id) + "</td><td>" + regimeBadge(t.regime) +
        "</td><td>" + pathBadge(t.path) + "</td><td>" +
        esc(Number(t.cur_delay).toFixed(1)) + " min</td></tr>";
    }).join("");
    $("regime-table").querySelector("tbody").innerHTML = rows ||
      '<tr><td colspan="4">No trains under way.</td></tr>';
    $("regime-count").textContent = shown.length < underWay.length
      ? "Showing " + shown.length + " of " + underWay.length +
        " trains under way (" + state.trains.length + " tracked)"
      : underWay.length + " of " + state.trains.length + " trains under way";
  }

  function renderETA() {
    var tb = $("eta-table").querySelector("tbody");
    var eta = state.eta;
    if (!eta || !eta.stations || !eta.stations.length) {
      tb.innerHTML = '<tr><td colspan="5">No ETA data.</td></tr>';
      $("explanation").textContent = "–";
      return;
    }
    var upcoming = eta.stations.filter(function (s) { return s.seq > -1; });
    var lo = Math.min.apply(null, upcoming.map(function (s) { return s.p10; }));
    var hi = Math.max.apply(null, upcoming.map(function (s) { return s.p90; }));
    if (!(hi > lo)) hi = lo + 1;
    tb.innerHTML = upcoming.map(function (s) {
      var l = (100 * (s.p10 - lo)) / (hi - lo);
      var w = Math.max(2, (100 * (s.p90 - s.p10)) / (hi - lo));
      var m = (100 * (s.p50 - lo)) / (hi - lo);
      return "<tr><td>" + esc(s.station) + "</td><td>" + hhmm(s.sched_arr) +
        " → <strong>" + hhmm(s.p50) + "</strong></td>" +
        '<td><div class="eta-bar" role="img" aria-label="' + esc(s.station) +
        " p10 " + hhmm(s.p10) + " p50 " + hhmm(s.p50) + " p90 " + hhmm(s.p90) + '">' +
        '<div class="eta-bar-fill" style="left:' + l.toFixed(1) + "%;width:" + w.toFixed(1) + '%"></div>' +
        '<div class="eta-bar-marker" style="left:' + m.toFixed(1) + '%"></div></div></td>' +
        "<td>" + esc(Number(s.delay_p50).toFixed(1)) + " min</td>" +
        "<td>" + esc(Number(s.p_on_time).toFixed(2)) + "</td></tr>";
    }).join("");
    $("explanation").textContent = eta.explanation || ("Path " + eta.path + ", regime " + eta.regime + ".");
  }

  function heatClass(v) {
    v = Number(v);
    if (!(v > 0)) return "heat-0";
    if (v <= 5) return "heat-1";
    if (v <= 15) return "heat-2";
    if (v <= 30) return "heat-3";
    return "heat-4";
  }

  function renderMatrix() {
    return getJSON("./api/matrix").then(function (d) {
      var head = "<tr><th scope=\"col\">Station</th>" + d.trains.map(function (t) {
        return '<th scope="col">' + esc(t) + "</th>";
      }).join("") + "</tr>";
      $("matrix-table").querySelector("thead").innerHTML = head;
      var body = d.stations.map(function (st, r) {
        var cells = d.values[r].map(function (v) {
          return '<td class="heat-cell ' + heatClass(v) + '">' + esc(Number(v).toFixed(0)) + "</td>";
        }).join("");
        return "<tr><td>" + esc(st) + "</td>" + cells + "</tr>";
      }).join("");
      $("matrix-table").querySelector("tbody").innerHTML = body;
    });
  }

  function renderRouter() {
    return getJSON("./api/metrics").then(function (d) {
      var c = d.router || {};
      $("router-counts").innerHTML =
        "Paths — full " + pathBadge("full") + " <strong>" + esc(c.full || 0) + "</strong>" +
        " · fallback " + pathBadge("fallback") + " <strong>" + esc(c.fallback || 0) + "</strong>" +
        " · cheap " + pathBadge("cheap") + " <strong>" + esc(c.cheap || 0) + "</strong>";
      var res = d.residual || {};
      var rows = Object.keys(res).map(function (h) {
        var m = res[h] || {};
        var cov = m.coverage == null ? "n/a" : Number(m.coverage).toFixed(2);
        return "<tr><td>" + esc(h) + "</td><td>" + esc(m.mae_model) + "</td><td>" +
          esc(m.mae_baseline) + "</td><td>" + esc(cov) + "</td><td>" + esc(m.n) + "</td></tr>";
      }).join("");
      $("score-table").querySelector("tbody").innerHTML = rows ||
        '<tr><td colspan="5">No metrics yet.</td></tr>';
    });
  }

  function renderJidoka() {
    return getJSON("./api/jidoka").then(function (d) {
      renderKPIs(d);
      var box = $("jidoka-list");
      var pend = d.pending || [];
      if (!pend.length) {
        box.innerHTML = '<div class="ux4g-alert ux4g-alert-success">' +
          '<div class="ux4g-alert-title">Queue clear</div>' +
          '<div class="ux4g-alert-message">No open Jidoka items.</div></div>';
        return;
      }
      box.innerHTML = pend.map(function (it) {
        return '<div class="ux4g-alert ux4g-alert-warning">' +
          '<div class="ux4g-alert-title">' + esc(it.id + " · " + it.kind + " · " + (it.train_id || "—")) + "</div>" +
          '<div class="ux4g-alert-message">' + esc(it.reason) + "</div>" +
          '<div class="dash-toolbar"><button class="ux4g-btn ux4g-btn-primary ux4g-btn-sm" type="button" data-approve="' +
          esc(it.id) + '">Approve</button>' +
          '<button class="ux4g-btn ux4g-btn-outline-primary ux4g-btn-sm" type="button" data-dismiss="' +
          esc(it.id) + '">Dismiss</button></div></div>';
      }).join("");
      box.querySelectorAll("[data-approve]").forEach(function (b) {
        b.addEventListener("click", function () { decideJidoka(b.getAttribute("data-approve"), "approve"); });
      });
      box.querySelectorAll("[data-dismiss]").forEach(function (b) {
        b.addEventListener("click", function () { decideJidoka(b.getAttribute("data-dismiss"), "dismiss"); });
      });
    });
  }

  function decideJidoka(id, action) {
    getJSON("./api/jidoka/" + encodeURIComponent(id) + "/" + action, { method: "POST" })
      .then(function () { return refreshAll(); })
      .catch(function (e) { pageError("Jidoka " + action + " failed: " + e.message); });
  }

  function loadTrains() {
    return getJSON("./api/trains").then(function (d) {
      state.trains = d.trains || [];
      $("clock").textContent = hhmm(d.now) + " (sim)";
      renderTrainOptions();
      renderRegimeTable();
    });
  }

  function loadETA() {
    if (!state.selected) return Promise.resolve();
    return getJSON("./api/eta/" + encodeURIComponent(state.selected)).then(function (d) {
      state.eta = d.eta;
      renderETA();
    });
  }

  function refreshAll() {
    return loadTrains().then(loadETA).then(function () {
      return Promise.all([renderMatrix(), renderRouter(), renderJidoka()]);
    }).then(renderModelVersion).catch(function (e) { pageError(e.message); });
  }

  /* -- actions --------------------------------------------------------- */

  $("train-select").addEventListener("change", function (e) {
    state.selected = e.target.value;
    loadETA().catch(function (err) { pageError(err.message); });
  });

  $("btn-advance").addEventListener("click", function () {
    var btn = $("btn-advance");
    btn.disabled = true;
    getJSON("./api/tick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ minutes: 5 })
    }).then(function () { return refreshAll(); })
      .catch(function (e) { pageError("Advance failed: " + e.message); })
      .then(function () { btn.disabled = false; });
  });

  $("btn-whatif").addEventListener("click", function () {
    var eta = state.eta;
    if (!eta || !eta.stations || eta.stations.length < 3) {
      pageError("What-if needs a selected train with downstream stations.");
      return;
    }
    // Mid-journey *downstream* station (ticket: one "hold 10 min" button).
    // Past stations reject with 400 ("already passed"), and the last station
    // yields delta 0, so filter to upcoming stations first.
    var upcoming = eta.stations.filter(function (s) { return s.p50 >= eta.now - 1; });
    if (!upcoming.length) {
      pageError("What-if needs an en-route train — this one completed its journey.");
      return;
    }
    var mid = upcoming[Math.min(upcoming.length - 1, Math.floor(upcoming.length / 2))];
    if (mid === upcoming[upcoming.length - 1] && upcoming.length > 1) {
      mid = upcoming[upcoming.length - 2];
    }
    getJSON("./api/whatif", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ train_id: eta.train_id, station: mid.station, hold_min: 10 })
    }).then(function (d) {
      var diffs = (d.diffs || []).map(function (x) {
        return esc(x.station) + " +" + esc(x.delta_min) + " min";
      }).join("; ");
      var worst = Math.max.apply(null, (d.diffs || []).map(function (x) { return Number(x.delta_min) || 0; }));
      $("whatif-result").innerHTML = '<div class="ux4g-alert ux4g-alert-info">' +
        '<div class="ux4g-alert-title">Scenario: hold 10 min at ' + esc(d.station) + "</div>" +
        '<div class="ux4g-alert-message">' + esc(diffs || "no downstream effect") +
        " (worst +" + worst.toFixed(1) + " min).</div></div>";
    }).catch(function (e) { pageError("What-if failed: " + e.message); });
  });

  $("btn-learn").addEventListener("click", function () {
    var btn = $("btn-learn");
    btn.disabled = true;
    $("learn-status").innerHTML =
      '<span class="ux4g-spinner ux4g-spinner-md" role="status" aria-label="Learning cycle running"></span> Training challenger (~30s)…';
    $("learn-result").innerHTML = "";
    getJSON("./api/learning/run", { method: "POST" }).then(function (d) {
      var ok = d.status === "PROMOTED";
      $("learn-result").innerHTML = '<div class="ux4g-alert ' +
        (ok ? "ux4g-alert-success" : "ux4g-alert-warning") + '">' +
        '<div class="ux4g-alert-title">Learning: ' + esc(d.status) + "</div>" +
        '<div class="ux4g-alert-message">Cycle ' + esc(d.cycle) + " (" + esc(d.branch) +
        "). Champion " + esc(d.champion) + " vs challenger " + esc(d.challenger) + ".</div></div>";
      return renderRouter().then(renderModelVersion);
    }).catch(function (e) { pageError("Learning failed: " + e.message); })
      .then(function () {
        btn.disabled = false;
        $("learn-status").innerHTML = "";
      });
  });

  $("theme-toggle").addEventListener("click", function () {
    var html = document.documentElement;
    var dark = html.getAttribute("data-theme") !== "dark";
    html.setAttribute("data-theme", dark ? "dark" : "light");
    $("theme-toggle").textContent = dark ? "Light theme" : "Dark theme";
  });

  refreshAll();
})();
