/* T13 audit view. Vanilla JS + fetch, no build step.
 * "Verify chain" calls GET /api/audit/verify (added in T13; the T09
 * GET /api/audit already returned {valid, first_bad_seq} — the dedicated
 * endpoint is a payload-light alias the ticket asked for). Table shows the
 * latest 50 entries with shortened hash/sig; the explanation panel shows
 * the currently selected ETA's summary text. */
(function () {
  "use strict";

  var state = { trains: [], selected: null };

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function short(h) {
    h = String(h == null ? "" : h);
    return h.length > 12 ? h.slice(0, 8) + "…" + h.slice(-4) : h;
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

  function detailOf(e) {
    var p = e.payload || {};
    if (e.kind === "promotion" || e.kind === "rejection") {
      return "cycle " + (p.cycle != null ? p.cycle : "?") + " · " + (p.branch || p.champion || "");
    }
    if (e.kind === "jidoka_approve") return (e.train_id || p.train_id || "") + " " + (p.item || "");
    try { return JSON.stringify(p).slice(0, 80); } catch (err) { return ""; }
  }

  function loadAudit() {
    return getJSON("./api/audit?limit=50").then(function (d) {
      var rows = (d.entries || []).map(function (e) {
        return "<tr><td>" + esc(e.seq) + "</td><td>" + esc(e.ts) + "</td>" +
          "<td>" + esc(e.kind) + "</td>" +
          '<td><code title="' + esc(e.hash) + '">' + esc(short(e.hash)) + "</code></td>" +
          '<td><code title="' + esc(e.sig) + '">' + esc(short(e.sig)) + "</code></td>" +
          "<td>" + esc(detailOf(e)) + "</td></tr>";
      }).join("");
      $("audit-table").querySelector("tbody").innerHTML = rows ||
        '<tr><td colspan="6">No audit entries yet.</td></tr>';
      return d;
    });
  }

  function loadETA() {
    if (!state.selected) return;
    getJSON("./api/eta/" + encodeURIComponent(state.selected)).then(function (d) {
      var eta = d.eta || {};
      $("expl-title").textContent = "Explanation — " + (eta.train_id || state.selected) +
        " (path " + (eta.path || "?") + ", regime " + (eta.regime || "?") + ")";
      $("explanation").textContent = eta.explanation || "–";
    }).catch(function (e) { pageError("ETA failed: " + e.message); });
  }

  $("btn-verify").addEventListener("click", function () {
    var btn = $("btn-verify");
    btn.disabled = true;
    $("verify-result").innerHTML = "";
    getJSON("./api/audit/verify").then(function (d) {
      var ok = !!d.valid;
      $("verify-summary").textContent = ok ? "Chain OK" : "Chain BROKEN at seq " + d.first_bad_seq;
      $("verify-result").innerHTML = '<div class="ux4g-alert ' +
        (ok ? "ux4g-alert-success" : "ux4g-alert-error") + '">' +
        '<div class="ux4g-alert-title">' + (ok ? "Verified — chain intact" : "Verification failed") + "</div>" +
        '<div class="ux4g-alert-message">' + (ok
          ? "Every hash and signature links back to genesis."
          : "First bad entry at seq " + esc(d.first_bad_seq) +
            ". Edit data/audit.jsonl and re-verify to see this state.") + "</div></div>";
    }).catch(function (e) { pageError("Verify failed: " + e.message); })
      .then(function () { btn.disabled = false; });
  });

  $("train-select").addEventListener("change", function (e) {
    state.selected = e.target.value;
    loadETA();
  });

  $("theme-toggle").addEventListener("click", function () {
    var html = document.documentElement;
    var dark = html.getAttribute("data-theme") !== "dark";
    html.setAttribute("data-theme", dark ? "dark" : "light");
    $("theme-toggle").textContent = dark ? "Light theme" : "Dark theme";
  });

  getJSON("./api/trains").then(function (d) {
    state.trains = d.trains || [];
    $("clock").textContent = hhmm(d.now) + " (sim)";
    var sel = $("train-select");
    sel.innerHTML = state.trains.map(function (t) {
      return '<option value="' + esc(t.train_id) + '">' +
        esc(t.train_id + " — " + (t.name || "")) + "</option>";
    }).join("");
    if (state.trains.length) state.selected = state.trains[0].train_id;
    sel.value = state.selected;
    loadETA();
  }).catch(function (e) { pageError("Trains failed: " + e.message); });

  loadAudit().catch(function (e) { pageError("Audit failed: " + e.message); });
})();
