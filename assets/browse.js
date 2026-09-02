/* browse.html - every source in the scan, filtered in the browser.
   Filter state lives in the URL query string so a narrowed view can be copied
   out of the address bar and emailed to someone. */

(function () {
  "use strict";

  var S = window.scan;
  var studies = [];
  var meta = null;
  var expanded = {};
  var collapsedFacets = {};

  /* Results are paged. Without it the results column runs to several screens while
     the filter panel beside it is one screen tall, so the filters scroll out of
     reach long before the list ends - the panel is pinned, but a pinned panel
     taller than the window still hides its own bottom. Ten rows keeps the page
     about as tall as the filters, so both columns end at roughly the same place. */
  var PAGE_SIZE = 10;

  // key -> how to read the value off a study, and what to call it on screen.
  var FACETS = [
    // Values are topic ids; a source matches if it carries any of the ticked
    // topics, which the shared matchesFacet() below already handles - a source
    // with more than one topic needs no special case here.
    { key: "topic", legend: "Topic", multi: true,
      values: function () { return meta.topics.map(function (t) { return t.id; }); },
      label: function (v) { return S.topicTitle(v); },
      of: function (s) { return s.topics; } },

    { key: "access", legend: "Can we get the data?", multi: true,
      values: function () { return meta.accessValues; },
      label: function (v) { return v; },
      of: function (s) { return [s.access]; } },

    /* The only facet that spells its abbreviations out in full. Everywhere else on
       the site LGA, PHN and HHS stay short with the full name on hover, but hover
       does not exist on a phone and a tag on a result row sits inside the button
       that opens it. This label is neither, so it is the one place the vocabulary
       can be taught on any device. Display only - the value, the id and the URL all
       still carry the short form. */
    { key: "geo", legend: "Geographic level", multi: true,
      values: function () {
        return meta.geoTags.filter(function (t) { return countAll("geo", t) > 0; });
      },
      label: function (v) { var full = S.tagTitle(v); return full ? v + " - " + full : v; },
      of: function (s) { return s.geoTags; } },

    /* Which health issue a source speaks to - see the README for where the seven
       domains come from and why they are worded the way they are. Plenty of
       sources have none (a method text, a paper about hospital admissions), so this
       facet is narrower than the others: ticking a box hides everything untagged.
       Values with no sources are hidden the same way the geographic level does it,
       so a domain nothing covers yet does not sit there reading as a dead end. */
    { key: "domain", legend: "Health domain", multi: true, collapsed: true,
      values: function () {
        return meta.domains.filter(function (d) { return countAll("domain", d) > 0; });
      },
      label: function (v) { return v; },
      of: function (s) { return s.domains; } },

    /* Same hidden-when-empty rule as the two facets above. A bucket exists in the
       vocabulary before anything lands in it - "Statistical agency release" was added
       ahead of the datasets it is for - and an option reading zero is a dead end. */
    { key: "type", legend: "Source type", multi: true, collapsed: true,
      values: function () {
        return meta.sourceGroups.filter(function (g) { return countAll("type", g) > 0; });
      },
      label: function (v) { return v; },
      of: function (s) { return [s.sourceGroup]; } },

    { key: "region", legend: "Where it is from", multi: true, collapsed: true,
      values: function () { return meta.regions; },
      label: function (v) { return v; },
      of: function (s) { return s.regions; } },

    { key: "priority", legend: "Relevance", multi: true, collapsed: true,
      values: function () { return ["highly-relevant", "relevant", "unmarked"]; },
      label: function (v) { return S.priorityLabel[v]; },
      of: function (s) { return [s.priority]; } }
  ];

  function facetByKey(key) {
    for (var i = 0; i < FACETS.length; i++) if (FACETS[i].key === key) return FACETS[i];
    return null;
  }

  /* ---------- URL state ---------- */

  function readState() {
    var params = new URLSearchParams(window.location.search);
    var state = { q: params.get("q") || "", page: Math.max(1, parseInt(params.get("page"), 10) || 1) };
    FACETS.forEach(function (facet) {
      var raw = params.get(facet.key);
      state[facet.key] = raw ? raw.split(",").filter(Boolean) : [];
    });

    // ?app=1 is a link already sent out and must keep working. Fold it into
    // the topic facet rather than keeping it as a separate parameter, so a page
    // reached either way ends up in exactly the same state.
    var legacyApp = params.get("app");
    if (legacyApp) {
      legacyApp.split(",").forEach(function (n) {
        var id = S.legacyAppToTopic(n.trim());
        if (id && state.topic.indexOf(id) === -1) state.topic.push(id);
      });
    }
    return state;
  }

  function writeState(state) {
    var params = new URLSearchParams();
    FACETS.forEach(function (facet) {
      if (state[facet.key].length) params.set(facet.key, state[facet.key].join(","));
    });
    if (state.q) params.set("q", state.q);
    // Page 1 is the default, so leave it out and keep the common address short.
    if (state.page > 1) params.set("page", state.page);
    var query = params.toString();
    history.replaceState(null, "", query ? "?" + query : window.location.pathname);
  }

  var state = { q: "", page: 1 };

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
      study.keyAttributes, study.country, study.geoLevel,
      (study.domains || []).join(" ")].join(" ").toLowerCase();
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
      var selected = state[facet.key].length;
      var collapsed = isFacetCollapsed(facet);
      var bodyId = "facet-" + facet.key;
      html += '<fieldset class="facet' + (collapsed ? " is-collapsed" : "") + '">' +
        '<legend><button type="button" class="facet-toggle" data-toggle-facet="' +
        facet.key + '" aria-expanded="' + (!collapsed) + '" aria-controls="' + bodyId + '">' +
        '<span>' + S.escapeHtml(facet.legend) + "</span>" +
        (selected ? '<span class="facet-selected">' + selected + " selected</span>" : "") +
        '<span class="facet-chevron" aria-hidden="true">' + (collapsed ? "&#9660;" : "&#9650;") +
        "</span></button></legend>" +
        '<div class="facet-options" id="' + bodyId + '"' + (collapsed ? " hidden" : "") + ">";
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
      html += "</div></fieldset>";
    });
    host.innerHTML = html;
  }

  function isFacetCollapsed(facet) {
    if (Object.prototype.hasOwnProperty.call(collapsedFacets, facet.key)) {
      return collapsedFacets[facet.key];
    }
    return !!facet.collapsed && !state[facet.key].length;
  }

  function setFacetCollapsed(button, collapsed) {
    var fieldset = button.closest(".facet");
    var body = document.getElementById(button.getAttribute("aria-controls"));
    if (!fieldset || !body) return;
    fieldset.classList.toggle("is-collapsed", collapsed);
    body.hidden = collapsed;
    button.setAttribute("aria-expanded", String(!collapsed));
    var chevron = button.querySelector(".facet-chevron");
    if (chevron) chevron.innerHTML = collapsed ? "&#9660;" : "&#9650;";
  }

  function pageCount(total) {
    return Math.max(1, Math.ceil(total / PAGE_SIZE));
  }

  /* Numbered buttons, with the ends always shown and an ellipsis standing in for
     any stretch skipped over. 27 sources is only three pages today, but the list
     grows, and a row of forty numbers is its own problem. */
  function pageNumbers(current, last) {
    var wanted = {1: 1};
    wanted[last] = 1;
    for (var i = current - 1; i <= current + 1; i++) if (i >= 1 && i <= last) wanted[i] = 1;
    var pages = Object.keys(wanted).map(Number).sort(function (a, b) { return a - b; });
    var out = [];
    pages.forEach(function (n, i) {
      if (i && n - pages[i - 1] > 1) out.push(null);   // a gap - render an ellipsis
      out.push(n);
    });
    return out;
  }

  function pagerHtml(current, last) {
    if (last < 2) return "";
    var step = function (label, target, enabled, rel) {
      return '<button type="button" class="pager__step" data-page="' + target + '"' +
        (enabled ? "" : " disabled") + (rel ? ' rel="' + rel + '"' : "") + ">" +
        label + "</button>";
    };
    var numbers = pageNumbers(current, last).map(function (n) {
      if (n === null) return '<span class="pager__gap" aria-hidden="true">&hellip;</span>';
      return '<button type="button" class="pager__num" data-page="' + n + '"' +
        (n === current ? ' aria-current="page"' : "") +
        ' aria-label="Page ' + n + '">' + n + "</button>";
    }).join("");

    return '<nav class="pager" aria-label="Result pages">' +
      step("&larr; Previous", current - 1, current > 1, "prev") +
      '<span class="pager__nums">' + numbers + "</span>" +
      step("Next &rarr;", current + 1, current < last, "next") +
      "</nav>";
  }

  function renderResults() {
    var results = currentResults();
    var bar = document.getElementById("resultbar");
    var active = FACETS.reduce(function (n, f) { return n + state[f.key].length; }, 0) + (state.q ? 1 : 0);

    // Filtering can leave the current page past the end of a shorter list.
    var last = pageCount(results.length);
    if (state.page > last) state.page = last;
    var from = (state.page - 1) * PAGE_SIZE;
    var page = results.slice(from, from + PAGE_SIZE);

    // Say which slice is on screen, not just the total, or the count reads as a
    // contradiction of the ten rows underneath it.
    var shown = results.length > PAGE_SIZE
      ? "<span>Showing <b>" + (from + 1) + "&ndash;" + (from + page.length) +
        "</b> of <b>" + results.length + "</b> sources</span>"
      : "<span><b>" + results.length + "</b> of " + studies.length + " sources</span>";

    bar.innerHTML = shown +
      (active ? '<button type="button" class="linkish" id="clear">Clear all filters</button>' : "");
    // How many matched, independent of how many are drawn on this page. The bar says
    // it in prose two lines up; this is the same number somewhere it can be read back.
    bar.setAttribute("data-total", results.length);
    bar.setAttribute("data-page", state.page);
    bar.setAttribute("data-pages", last);

    var list = document.getElementById("results");
    var pager = document.getElementById("pager");
    if (!results.length) {
      list.innerHTML = '<li class="empty">Nothing matches that combination.</li>';
      pager.innerHTML = "";
      return;
    }

    pager.innerHTML = pagerHtml(state.page, last);

    list.innerHTML = page.map(function (study) {
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
      row("Topics", S.topicTags(study)) +
      row("Health domain", S.tagList(study.domains)) +
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
    syncFilterHeight();
  }

  function syncFilterHeight() {
    var form = document.getElementById("filter-form");
    if (!form) return;
    var top = Math.max(16, Math.round(form.getBoundingClientRect().top));
    form.style.setProperty("--filters-viewport-top", top + "px");
  }

  /* ---------- events ---------- */

  function wire() {
    window.addEventListener("scroll", syncFilterHeight, { passive: true });
    window.addEventListener("resize", syncFilterHeight);

    document.getElementById("filters").addEventListener("change", function (event) {
      var input = event.target;
      if (!input.dataset || !input.dataset.facet) return;
      var list = state[input.dataset.facet];
      var at = list.indexOf(input.value);
      if (input.checked && at === -1) list.push(input.value);
      if (!input.checked && at !== -1) list.splice(at, 1);
      // A different result set means the old page number means nothing.
      state.page = 1;
      render();
    });

    document.getElementById("filters").addEventListener("click", function (event) {
      var button = event.target.closest("[data-toggle-facet]");
      if (!button) return;
      var facet = facetByKey(button.dataset.toggleFacet);
      if (!facet) return;
      collapsedFacets[facet.key] = !isFacetCollapsed(facet);
      setFacetCollapsed(button, collapsedFacets[facet.key]);
      syncFilterHeight();
    });

    var search = document.getElementById("search");
    var timer = null;
    search.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () { state.q = search.value.trim(); state.page = 1; render(); }, 150);
    });

    document.getElementById("resultbar").addEventListener("click", function (event) {
      if (event.target.id !== "clear") return;
      FACETS.forEach(function (f) { state[f.key] = []; });
      state.q = "";
      state.page = 1;
      search.value = "";
      render();
    });

    document.getElementById("pager").addEventListener("click", function (event) {
      var button = event.target.closest("[data-page]");
      if (!button || button.disabled) return;
      state.page = Number(button.dataset.page);
      render();
      // Put the reader at the top of the new page rather than wherever the old one
      // left them, which on a short last page is below everything there is to see.
      document.getElementById("resultbar").scrollIntoView({ block: "start" });
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
    S.setTopics(meta.topics);   // before readState(): it resolves the ?app= alias
    state = readState();
    S.stampGenerated(meta);
    document.getElementById("search").value = state.q;
    wire();
    render();
  }).catch(function (err) {
    S.showLoadError(document.getElementById("browse-main"), err);
  });
})();
