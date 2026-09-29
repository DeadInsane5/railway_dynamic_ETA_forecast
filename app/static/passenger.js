/* T13 passenger view. Vanilla JS + fetch, no build step.
 * Train picker + next-station ETAs ("expected 14:32 (14:28-14:41)" style
 * rows) + EN/HI/MR toggle over a 12-string static dictionary.
 * UX4G gap note: the design system ships no i18n/translation utility, so
 * the string dictionary below is application content, not a token override.
 * Language buttons reuse ux4g-btn primary/outline-primary variants. */
(function () {
  "use strict";

  var STR = {
    en: {
      heading: "Passenger arrivals", sub: "pick a train, see what's next",
      selectTitle: "Choose your train", trainLabel: "Train",
      nextStations: "Upcoming stations", colStation: "Station",
      colExpected: "Expected", colRange: "Likely range",
      colOnTime: "On-time chance", whyTitle: "Why this time?",
      whyHeading: "Explanation", loading: "Loading…",
      simNote: "All data simulated. API docs at /docs."
    },
    hi: {
      heading: "यात्री आगमन", sub: "ट्रेन चुनें, आगे के स्टेशन देखें",
      selectTitle: "अपनी ट्रेन चुनें", trainLabel: "ट्रेन",
      nextStations: "आगामी स्टेशन", colStation: "स्टेशन",
      colExpected: "अपेक्षित समय", colRange: "संभावित सीमा",
      colOnTime: "समय पर संभावना", whyTitle: "यह समय क्यों?",
      whyHeading: "स्पष्टीकरण", loading: "लोड हो रहा है…",
      simNote: "सभी डेटा सिमुलेटेड है। API दस्तावेज़ /docs पर।"
    },
    mr: {
      heading: "प्रवासी आगमन", sub: "ट्रेन निवडा, पुढील स्थानके पहा",
      selectTitle: "तुमची ट्रेन निवडा", trainLabel: "ट्रेन",
      nextStations: "येणारी स्थानके", colStation: "स्थानक",
      colExpected: "अपेक्षित वेळ", colRange: "संभाव्य श्रेणी",
      colOnTime: "वेळेवर शक्यता", whyTitle: "ही वेळ का?",
      whyHeading: "स्पष्टीकरण", loading: "लोड होत आहे…",
      simNote: "सर्व डेटा सिम्युलेटेड आहे. API दस्तऐवज /docs वर."
    }
  };

  var state = { trains: [], selected: null, lang: "en" };

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

  function getJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error(url + " -> HTTP " + r.status);
      return r.json();
    });
  }

  function setLang(lang) {
    if (!STR[lang]) return;
    state.lang = lang;
    var dict = STR[lang];
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      var key = el.getAttribute("data-i18n");
      if (dict[key] != null) el.textContent = dict[key];
    });
    document.querySelectorAll("[data-lang]").forEach(function (b) {
      var active = b.getAttribute("data-lang") === lang;
      b.setAttribute("aria-pressed", active ? "true" : "false");
      b.className = "ux4g-btn ux4g-btn-sm " +
        (active ? "ux4g-btn-primary" : "ux4g-btn-outline-primary");
    });
  }

  function renderETA(eta) {
    var tb = $("arr-table").querySelector("tbody");
    if (!eta || !eta.stations) {
      tb.innerHTML = '<tr><td colspan="4">' + esc(STR[state.lang].loading) + "</td></tr>";
      return;
    }
    var curSeq = -1;
    var train = null;
    for (var i = 0; i < state.trains.length; i++) {
      if (state.trains[i].train_id === state.selected) { train = state.trains[i]; break; }
    }
    if (train && train.cur_seq != null) curSeq = Number(train.cur_seq);
    var upcoming = eta.stations.filter(function (s) { return s.seq > curSeq; });
    tb.innerHTML = upcoming.map(function (s) {
      var pct = Math.round(Number(s.p_on_time) * 100);
      return "<tr><td>" + esc(s.station) + "</td>" +
        "<td><strong>" + esc(hhmm(s.p50)) + "</strong></td>" +
        "<td>" + esc(hhmm(s.p10)) + "–" + esc(hhmm(s.p90)) + "</td>" +
        "<td>" + esc(isFinite(pct) ? pct + "%" : "–") + "</td></tr>";
    }).join("") || '<tr><td colspan="4">–</td></tr>';
    $("explanation").textContent = eta.explanation || "–";
  }

  function loadETA() {
    if (!state.selected) return;
    getJSON("./api/eta/" + encodeURIComponent(state.selected)).then(function (d) {
      renderETA(d.eta);
    }).catch(function (e) {
      $("page-error").innerHTML =
        '<div class="ux4g-alert ux4g-alert-error"><div class="ux4g-alert-title">Error</div>' +
        '<div class="ux4g-alert-message">' + esc(e.message) + "</div></div>";
    });
  }

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
  }).catch(function (e) {
    $("page-error").innerHTML =
      '<div class="ux4g-alert ux4g-alert-error"><div class="ux4g-alert-title">Error</div>' +
      '<div class="ux4g-alert-message">' + esc(e.message) + "</div></div>";
  });

  $("train-select").addEventListener("change", function (e) {
    state.selected = e.target.value;
    loadETA();
  });

  document.querySelectorAll("[data-lang]").forEach(function (b) {
    b.addEventListener("click", function () { setLang(b.getAttribute("data-lang")); });
  });

  $("theme-toggle").addEventListener("click", function () {
    var html = document.documentElement;
    var dark = html.getAttribute("data-theme") !== "dark";
    html.setAttribute("data-theme", dark ? "dark" : "light");
    $("theme-toggle").textContent = dark ? "Light theme" : "Dark theme";
  });
})();
