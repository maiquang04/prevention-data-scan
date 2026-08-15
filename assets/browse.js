/* browse.html - every source in the scan, filtered in the browser.
   Filter state lives in the URL query string so a narrowed view can be copied
   out of the address bar and emailed to someone. */

(function () {
  "use strict";

  var S = window.scan;
  var studies = [];
  var meta = null;
  var expanded = {};

  // key -> how to read the value off a study, and what to call it on screen.
  var FACETS = [
    { key: "app", legend: "Application", multi: true,
      values: function () { return ["1", "5", "6"]; },
      label: function (v) { return "Application " + v; },
      of: function (s) { return [String(s.app)]; } },

    { key: "access", legend: "Can we get the data?", multi: true,
      values: function () { return meta.accessValues; },
      label: function (v) { return v; },
      of: function (s) { return [s.access]; } },

    { key: "geo", legend: "Geographic level", multi: true,
      values: function () {
        return meta.geoTags.filter(function (t) { return countAll("geo", t) > 0; });
      },
      label: function (v) { return v; },
      of: function (s) { return s.geoTags; } },

    { key: "region", legend: "Where it is from", multi: true,
      values: function () { return meta.regions; },
      label: function (v) { return v; },
      of: function (s) { return s.regions; } },

    { key: "priority", legend: "Relevance", multi: true,
      values: function () { return ["highly-relevant", "relevant", "unmarked"]; },
      label: function (v) { return S.priorityLabel[v]; },
      of: function (s) { return [s.priority]; } },

    { key: "type", legend: "Source type", multi: true,
      values: function () { return meta.sourceGroups; },
      label: function (v) { return v; },
      of: function (s) { return [s.sourceGroup]; } }
  ];

  function facetByKey(key) {
    for (var i = 0; i < FACETS.length; i++) if (FACETS[i].key === key) return FACETS[i];
    return null;
  }

  /* ---------- URL state ---------- */

  function readState() {
    var params = new URLSearchParams(window.location.search);
    var state = { q: params.get("q") || "" };
    FACETS.forEach(function (facet) {
      var raw = params.get(facet.key);
      state[facet.key] = raw ? raw.split(",").filter(Boolean) : [];
    });
    return state;
  }

  function writeState(state) {
    var params = new URLSearchParams();
    FACETS.forEach(function (facet) {
      if (state[facet.key].length) params.set(facet.key, state[facet.key].join(","));
    });
    if (state.q) params.set("q", state.q);
    var query = params.toString();
    history.replaceState(null, "", query ? "?" + query : window.location.pathname);
  }

  var state = { q: "" };

  /* ---------- filtering ---------- */

  function matchesFacet(study, facet, selected) {
    if (!selected.length) return true;
    var mine = facet.of(study);
    for (var i = 0; i < selected.length; i++) {
      if (mine.indexOf(selected[i]) !== -1) return true;
    }
    return false;
  }

  function matchesSearch(study, query) {
    if (!query) return true;
    var haystack = [study.reference, study.task, study.metrics, study.dataSources,
      study.keyAttributes, study.country, study.geoLevel].join(" ").toLowerCase();
    return query.toLowerCase().split(/\s+/).every(function (word) {
      return haystack.indexOf(word) !== -1;
    });
  }

  /* Studies matching everything except one facet group - so the count beside each
     checkbox says how many results ticking it would give, not a fixed total. */
  function subsetExcluding(skipKey) {
    return studies.filter(function (study) {
      if (!matchesSearch(study, state.q)) return false;
      return FACETS.every(function (facet) {
        return facet.key === skipKey || matchesFacet(study, facet, state[facet.key]);
      });
    });
  }

  function countAll(key, value) {
    var facet = facetByKey(key);
    return studies.filter(function (s) { return facet.of(s).indexOf(value) !== -1; }).length;
  }

  function currentResults() {
    return subsetExcluding(null);
  }

  /* ---------- rendering ---------- */

  function renderFilters() {
    var host = document.getElementById("filters");
    var html = "";
    FACETS.forEach(function (facet) {
      var subset = subsetExcluding(facet.key);
      var values = facet.values();
      if (!values.length) return;
      html += '<fieldset><legend>' + S.escapeHtml(facet.legend) + "</legend>";
      values.forEach(function (value) {
        var n = subset.filter(function (s) { return facet.of(s).indexOf(value) !== -1; }).length;
        var checked = state[facet.key].indexOf(value) !== -1;
        var id = "f-" + facet.key + "-" + value.replace(/[^a-z0-9]+/gi, "-");
        html += '<label for="' + id + '">' +
          '<input type="checkbox" id="' + id + '" data-facet="' + facet.key +
          '" value="' + S.escapeHtml(value) + '"' + (checked ? " checked" : "") + ">" +
          "<span>" + S.escapeHtml(facet.label(value)) + "</span>" +
          '<span class="count">' + n + "</span></label>";
      });
      html += "</fieldset>";
    });
    host.innerHTML = html;
  }

  function renderResults() {
    var results = currentResults();
    var bar = document.getElementById("resultbar");
    var active = FACETS.reduce(function (n, f) { return n + state[f.key].length; }, 0) + (state.q ? 1 : 0);

    bar.innerHTML = "<span><b>" + results.length + "</b> of " + studies.length + " sources</span>" +
      (active ? '<button type="button" class="linkish" id="clear">Clear all filters</button>' : "");

    var list = document.getElementById("results");
    if (!results.length) {
      list.innerHTML = '<li class="empty">Nothing matches that combination.</li>';
      return;
    }

    list.innerHTML = results.map(function (study) {
      var open = !!expanded[study.id];
      return '<li class="result">' +
        '<button type="button" class="result__head" data-id="' + study.id + '" aria-expanded="' +
          open + '" aria-controls="d-' + study.id + '">' +
          '<span class="result__num">' + S.priorityDot(study.priority) + study.app + "." + study.num + "</span>" +
          '<span class="result__body">' +
            '<span class="result__ref">' + S.escapeHtml(study.reference) + "</span>" +
            '<span class="result__task">' + S.escapeHtml(trim(study.task, 210)) + "</span>" +
            '<span class="meta-row">' + S.metaRow(study) + "</span>" +
          "</span>" +
          '<span class="result__chev" aria-hidden="true">' + (open ? "&#9650;" : "&#9660;") + "</span>" +
        "</button>" +
        '<div class="detail" id="d-' + study.id + '"' + (open ? "" : " hidden") + ">" +
          (open ? detailHtml(study) : "") + "</div></li>";
    }).join("");
  }

  function trim(text, max) {
    if (!text || text.length <= max) return text || "";
    return text.slice(0, max).replace(/\s+\S*$/, "") + "…";
  }

  function row(term, value) {
    if (!value) return "";
    return "<dt>" + S.escapeHtml(term) + "</dt><dd>" + value + "</dd>";
  }

  function detailHtml(study) {
    var links = study.dataLinks.map(function (link) {
      if (!link.url) return "<li>" + S.escapeHtml(link.label) + "</li>";
      return "<li>" + S.externalLink(link.url, link.label || S.shortHost(link.url)) + "</li>";
    }).join("");

    return "<dl>" +
      row("What it does", S.escapeHtml(study.task)) +
      row("What goes in", S.escapeHtml(study.inputData)) +
      row("What comes out", S.escapeHtml(study.output)) +
      row("Measures used", S.escapeHtml(study.metrics)) +
      row("Geographic level", S.escapeHtml(study.geoLevel)) +
      row("Data behind it", S.escapeHtml(study.dataSources)) +
      row("Can we get it?", S.accessBadge(study.access) +
        (study.accessNote ? " " + S.escapeHtml(study.accessNote) : "")) +
      row("Where to get it", links ? "<ul>" + links + "</ul>" : "") +
      row("How big it is", S.escapeHtml(study.scale)) +
      row("What is in it", S.escapeHtml(study.keyAttributes)) +
      row("Country or system", S.escapeHtml(study.country)) +
      row("Source type", S.escapeHtml(study.sourceType)) +
      "</dl>" +
      '<p class="small">' +
        (study.link ? S.externalLink(study.link, "Read the source") + " &nbsp;&middot;&nbsp; " : "") +
        '<a href="study.html?id=' + encodeURIComponent(study.id) + '">Open its own page</a>' +
      "</p>";
  }

  function render() {
    renderFilters();
    renderResults();
    writeState(state);
  }

  /* ---------- events ---------- */

  function wire() {
    document.getElementById("filters").addEventListener("change", function (event) {
      var input = event.target;
      if (!input.dataset || !input.dataset.facet) return;
      var list = state[input.dataset.facet];
      var at = list.indexOf(input.value);
      if (input.checked && at === -1) list.push(input.value);
      if (!input.checked && at !== -1) list.splice(at, 1);
      render();
    });

    var search = document.getElementById("search");
    var timer = null;
    search.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () { state.q = search.value.trim(); render(); }, 150);
    });

    document.getElementById("resultbar").addEventListener("click", function (event) {
      if (event.target.id !== "clear") return;
      FACETS.forEach(function (f) { state[f.key] = []; });
      state.q = "";
      search.value = "";
      render();
    });

    document.getElementById("results").addEventListener("click", function (event) {
      var head = event.target.closest(".result__head");
      if (!head) return;
      expanded[head.dataset.id] = !expanded[head.dataset.id];
      renderResults();
      var again = document.querySelector('.result__head[data-id="' + head.dataset.id + '"]');
      if (again) again.focus();
    });
  }

  /* ---------- start ---------- */

  S.loadData(["studies.json", "meta.json"]).then(function (loaded) {
    studies = loaded[0];
    meta = loaded[1];
    state = readState();
    S.stampGenerated(meta);
    document.getElementById("search").value = state.q;
    wire();
    render();
  }).catch(function (err) {
    S.showLoadError(document.getElementById("browse-main"), err);
  });
})();
