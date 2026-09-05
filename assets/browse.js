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
  var collapsedSubs = {};

  /* Results are paged. Without it the results column runs to several screens while
     the filter panel beside it is one screen tall, so the filters scroll out of
     reach long before the list ends - the panel is pinned, but a pinned panel
     taller than the window still hides its own bottom. Ten rows keeps the page
     about as tall as the filters, so both columns end at roughly the same place. */
  var PAGE_SIZE = 10;

  /* Newest first is the default. The workbook's own order used to be, and it is gone:
     it meant something to whoever built the sheet and nothing to a reader, and a
     shared ?page= link that depends on it is not worth keeping.

     A source with no year is an ongoing collection - the National Health Survey,
     PLIDA, the Social Health Atlas - not an old one. It sorts to the bottom under
     both year orders rather than being handed a date it does not have. 20 of the 61
     are like this, so under the default order none of them is on page 1.

     A to Z runs on the reference, which is the line the reader sees: author surname
     for a paper, agency name for a dataset. */
  var DEFAULT_SORT = "newest";

  var SORTS = {
    newest: { label: "Newest first", compare: byYear(-1) },
    oldest: { label: "Oldest first", compare: byYear(1) },
    az: { label: "A to Z", compare: byReference(1) },
    za: { label: "Z to A", compare: byReference(-1) }
  };

  function byYear(direction) {
    return function (a, b) {
      if (!a.year !== !b.year) return a.year ? -1 : 1;
      if (!a.year) return 0;
      return direction * (a.year - b.year);
    };
  }

  function byReference(direction) {
    return function (a, b) {
      return direction * a.reference.localeCompare(b.reference, "en", { sensitivity: "base" });
    };
  }

  // key -> how to read the value off a study, and what to call it on screen.
  var FACETS = [
    // Values are topic ids; a source matches if it carries any of the ticked
    // topics, which the shared matchesFacet() below already handles - a source
    // with more than one topic needs no special case here.
    { key: "topic", legend: "Focus area", multi: true,
      values: function () { return meta.topics.map(function (t) { return t.id; }); },
      label: function (v) { return S.topicTitle(v); },
      of: function (s) { return s.topics; } },

    /* Open at the start, with Access collapsed below it. Whether a source is a paper,
       a government report or a dataset is the cut most readers make first, and a
       source's access is on its row as a coloured badge whether or not that filter is
       open, so collapsing Access hides the checkboxes and none of the information.

       Hides a bucket holding nothing, the same rule Geography and Health focus follow.
       A bucket exists in the vocabulary before anything lands in it - "Published
       dataset" was added ahead of the datasets it is for - and an option reading zero
       is a dead end. */
    { key: "type", legend: "Kind of source", multi: true,
      values: function () {
        return meta.sourceGroups.filter(function (g) { return countAll("type", g) > 0; });
      },
      label: function (v) { return S.sourceGroupLabel(v); },
      of: function (s) { return [s.sourceGroup]; } },

    /* The only facet that spells its abbreviations out in full. Everywhere else on
       the site LGA, PHN and HHS stay short with the full name on hover, but hover
       does not exist on a phone and a tag on a result row sits inside the button
       that opens it. This label is neither, so it is the one place the vocabulary
       can be taught on any device. Display only - the value, the id and the URL all
       still carry the short form. */
    { key: "geo", legend: "Geography", multi: true, collapsed: true,
      values: function () {
        return meta.geoTags.filter(function (t) { return countAll("geo", t) > 0; });
      },
      label: function (v) { var full = S.tagTitle(v); return full ? v + " - " + full : v; },
      of: function (s) { return s.geoTags; } },

    /* Which health issue a source speaks to - see the README for where the seven
       domains come from and why they are worded the way they are. Plenty of
       sources have none (a method text, a paper about hospital admissions), so this
       facet is narrower than the others: ticking a box hides everything untagged.

       Unlike the geography and source-type facets, this one keeps a value with no
       sources. It has to: each domain now owns the agency's measures that sit under
       it, and hiding the domain would hide its measures with it. The measures are
       the reason - see the indicator facet below. */
    { key: "domain", legend: "Health focus", multi: true, collapsed: true,
      values: function () { return meta.domains; },
      label: function (v) { return S.domainLabel(v); },
      of: function (s) { return s.domains; },

      /* The agency's 61 measures hang off the domain each one belongs to, rather
         than sitting in a filter group of their own. Their spreadsheet is built the
         same way - a Domain column and an Indicator column inside it - so a reader
         who knows the source document finds them where they expect. It also stops
         61 checkboxes from becoming an eighth group taller than the other seven put
         together. */
      childKey: "indicator",
      children: function (domain) {
        return meta.indicators.filter(function (i) { return i.domain === domain; })
          .map(function (i) { return i.name; });
      } },

    /* Not drawn as a group of its own - renderFilters skips it and the domain facet
       above draws its values nested underneath. It stays in this list because that
       is what gives it a state key, a share of the URL, and its counts.

       This is the one facet that deliberately keeps its zeros. Everywhere else an
       option reading zero is a dead end and gets hidden; here the zero is the
       finding. 34 of the 61 measures have no source in the scan, and that gap list
       is a thing the agency asked for, so ticking one gives a message saying so
       rather than the generic "nothing matches". */
    { key: "indicator", legend: "Indicators", multi: true, nested: true,
      values: function () {
        return meta.indicators.map(function (i) { return i.name; });
      },
      label: function (v) { return v; },
      of: function (s) { return s.indicators || []; } },

    { key: "access", legend: "Access", multi: true, collapsed: true,
      values: function () { return meta.accessValues; },
      label: function (v) { return S.accessLabel(v); },
      of: function (s) { return [s.access]; } },

    { key: "region", legend: "Region", multi: true, collapsed: true,
      values: function () { return meta.regions; },
      label: function (v) { return v; },
      of: function (s) { return s.regions; } }
  ];

  function facetByKey(key) {
    for (var i = 0; i < FACETS.length; i++) if (FACETS[i].key === key) return FACETS[i];
    return null;
  }

  function optionId(key, value) {
    return "f-" + key + "-" + value.replace(/[^a-z0-9]+/gi, "-");
  }

  function addSelected(key, value) {
    if (state[key].indexOf(value) === -1) state[key].push(value);
  }

  function removeSelected(key, value) {
    var at = state[key].indexOf(value);
    if (at !== -1) state[key].splice(at, 1);
  }

  function setSelected(key, value, selected) {
    if (selected) addSelected(key, value);
    else removeSelected(key, value);
  }

  function indicatorDomain(name) {
    for (var i = 0; i < meta.indicators.length; i++) {
      if (meta.indicators[i].name === name) return meta.indicators[i].domain;
    }
    return null;
  }

  function indicatorsForDomain(domain) {
    var facet = facetByKey("domain");
    return facet && facet.children ? facet.children(domain) : [];
  }

  function setDomainTree(domain, selected) {
    setSelected("domain", domain, selected);
    indicatorsForDomain(domain).forEach(function (name) {
      setSelected("indicator", name, selected);
    });
    if (selected && !Object.prototype.hasOwnProperty.call(collapsedSubs, domain)) {
      collapsedSubs[domain] = true;
    }
  }

  function syncDomainFromIndicators(domain) {
    var kids = indicatorsForDomain(domain);
    if (!kids.length) return;
    var selected = kids.filter(function (name) {
      return state.indicator.indexOf(name) !== -1;
    }).length;
    setSelected("domain", domain, selected === kids.length);
    if (selected) collapsedSubs[domain] = false;
  }

  function normaliseNestedState() {
    meta.domains.forEach(function (domain) {
      if (state.domain.indexOf(domain) !== -1) {
        setDomainTree(domain, true);
      } else {
        syncDomainFromIndicators(domain);
      }
    });
  }

  function sameFilterGroup(a, b) {
    if (a === b) return true;
    return (a === "domain" && b === "indicator") || (a === "indicator" && b === "domain");
  }

  function matchesHealthTree(study, domains, indicators) {
    if (!domains.length && !indicators.length) return true;
    var mineDomains = study.domains || [];
    var mineIndicators = study.indicators || [];
    for (var i = 0; i < domains.length; i++) {
      if (mineDomains.indexOf(domains[i]) !== -1) return true;
    }
    for (var j = 0; j < indicators.length; j++) {
      if (mineIndicators.indexOf(indicators[j]) !== -1) return true;
    }
    return false;
  }

  function selectedCount(facet) {
    if (facet.nested) return 0;
    var count = state[facet.key].length;
    var child = facet.childKey ? facetByKey(facet.childKey) : null;
    if (!child) return count;
    state[child.key].forEach(function (name) {
      if (state[facet.key].indexOf(indicatorDomain(name)) === -1) count++;
    });
    return count;
  }

  function activeFilterCount() {
    return FACETS.reduce(function (n, facet) {
      return n + selectedCount(facet);
    }, 0);
  }

  /* ---------- URL state ---------- */

  function readState() {
    var params = new URLSearchParams(window.location.search);
    var sort = params.get("sort") || DEFAULT_SORT;
    if (!Object.prototype.hasOwnProperty.call(SORTS, sort)) sort = DEFAULT_SORT;
    var state = { q: params.get("q") || "", sort: sort,
                  page: Math.max(1, parseInt(params.get("page"), 10) || 1) };
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
      var values = state[facet.key];
      if (facet.key === "indicator") {
        values = values.filter(function (name) {
          return state.domain.indexOf(indicatorDomain(name)) === -1;
        });
      }
      if (values.length) params.set(facet.key, values.join(","));
    });
    if (state.q) params.set("q", state.q);
    if (state.sort !== DEFAULT_SORT) params.set("sort", state.sort);
    // Page 1 is the default, so leave it out and keep the common address short.
    if (state.page > 1) params.set("page", state.page);
    var query = params.toString();
    history.replaceState(null, "", query ? "?" + query : window.location.pathname);
  }

  var state = { q: "", sort: DEFAULT_SORT, page: 1 };

  /* ---------- filtering ---------- */

  function matchesFacet(study, facet, selected) {
    if (facet.key === "domain") return matchesHealthTree(study, selected, state.indicator);
    if (facet.key === "indicator") return true;
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
      (study.domains || []).join(" "),
      (study.domains || []).map(S.domainLabel).join(" "),
      // Indicators also stay searchable, including when the reader does not know
      // which health focus they sit under.
      (study.indicators || []).join(" "),
      S.sourceGroupLabel(study.sourceGroup)].join(" ").toLowerCase();
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
        return (skipKey && sameFilterGroup(skipKey, facet.key)) ||
          matchesFacet(study, facet, state[facet.key]);
      });
    });
  }

  function countAll(key, value) {
    var facet = facetByKey(key);
    return studies.filter(function (s) { return facet.of(s).indexOf(value) !== -1; }).length;
  }

  function currentResults() {
    // subsetExcluding hands back a fresh array, so this does not reorder the loaded
    // data. Array.sort is stable, so sources sharing a year stay in workbook order
    // underneath the year sort rather than shuffling between renders.
    return subsetExcluding(null).sort(SORTS[state.sort].compare);
  }

  /* ---------- rendering ---------- */

  function renderFilters() {
    var host = document.getElementById("filters");
    var html = "";
    FACETS.forEach(function (facet) {
      if (facet.nested) return;               // drawn inside its parent, not on its own
      var subset = subsetExcluding(facet.key);
      var values = facet.values();
      if (!values.length) return;
      var child = facet.childKey ? facetByKey(facet.childKey) : null;
      var childSubset = child ? subsetExcluding(child.key) : null;
      var selected = selectedCount(facet);
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

      function optionHtml(which, pool, value) {
        var n = pool.filter(function (s) { return which.of(s).indexOf(value) !== -1; }).length;
        var checked = state[which.key].indexOf(value) !== -1;
        var id = optionId(which.key, value);
        return '<label for="' + id + '">' +
          '<input type="checkbox" id="' + id + '" data-facet="' + which.key +
          '" value="' + S.escapeHtml(value) + '"' + (checked ? " checked" : "") + ">" +
          "<span>" + S.escapeHtml(which.label(value)) + "</span>" +
          '<span class="count">' + n + "</span></label>";
      }

      values.forEach(function (value) {
        html += optionHtml(facet, subset, value);
        if (!child) return;

        /* Each domain's measures, folded away until asked for. Seven domains open at
           once would be 61 checkboxes and the group would be taller than the rest of
           the panel together. A domain with a ticked measure under it starts open, so
           an active filter is never hidden behind a fold. */
        var kids = facet.children(value);
        if (!kids.length) return;
        var subId = "sub-" + facet.key + "-" + value.replace(/[^a-z0-9]+/gi, "-");
        var shut = isSubCollapsed(value, kids);
        var ticked = kids.filter(function (k) { return state[child.key].indexOf(k) !== -1; }).length;
        html += '<div class="facet-sub' + (shut ? " is-collapsed" : "") + '">' +
          '<button type="button" class="facet-sub-toggle" data-toggle-sub="' +
            S.escapeHtml(value) + '" aria-expanded="' + (!shut) + '" aria-controls="' + subId + '">' +
            "<span>" + kids.length + " measure" + (kids.length === 1 ? "" : "s") +
            (ticked ? ", " + ticked + " selected" : "") + "</span>" +
            '<span class="facet-sub-chevron" aria-hidden="true">' + (shut ? "&#9660;" : "&#9650;") +
            "</span>" +
          "</button>" +
          '<div class="facet-sub-options" id="' + subId + '"' + (shut ? " hidden" : "") + ">";
        kids.forEach(function (kid) { html += optionHtml(child, childSubset, kid); });
        html += "</div></div>";
      });
      html += "</div></fieldset>";
    });
    host.innerHTML = html;
    syncNestedParents();
  }

  function syncNestedParents() {
    meta.domains.forEach(function (domain) {
      var input = document.getElementById(optionId("domain", domain));
      if (!input) return;
      var kids = indicatorsForDomain(domain);
      var selected = kids.filter(function (name) {
        return state.indicator.indexOf(name) !== -1;
      }).length;
      input.indeterminate = selected > 0 && selected < kids.length;
    });
  }

  function isFacetCollapsed(facet) {
    if (Object.prototype.hasOwnProperty.call(collapsedFacets, facet.key)) {
      return collapsedFacets[facet.key];
    }
    /* A ticked measure lives inside Health focus, so it has to hold that group open
       too - otherwise arriving on a shared ?indicator= link shows a shut group and no
       sign of what is filtering the list. */
    var child = facet.childKey ? facetByKey(facet.childKey) : null;
    var anySelected = state[facet.key].length || (child && state[child.key].length);
    return !!facet.collapsed && !anySelected;
  }

  /* Per-domain fold state for the nested measures. Shut unless the reader opened it
     or something inside it is ticked. */
  function isSubCollapsed(value, kids) {
    if (Object.prototype.hasOwnProperty.call(collapsedSubs, value)) {
      return collapsedSubs[value];
    }
    return !kids.some(function (k) { return state.indicator.indexOf(k) !== -1; });
  }

  function setSubCollapsed(button, collapsed) {
    var wrap = button.closest(".facet-sub");
    var body = document.getElementById(button.getAttribute("aria-controls"));
    if (!wrap || !body) return;
    wrap.classList.toggle("is-collapsed", collapsed);
    body.hidden = collapsed;
    button.setAttribute("aria-expanded", String(!collapsed));
    var chevron = button.querySelector(".facet-sub-chevron");
    if (chevron) chevron.innerHTML = collapsed ? "&#9660;" : "&#9650;";
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

  /* The order control, drawn into the result bar so it sits with the count it
     reorders. */
  function sortHtml() {
    var options = "";
    for (var key in SORTS) {
      if (!Object.prototype.hasOwnProperty.call(SORTS, key)) continue;
      options += '<option value="' + key + '"' + (state.sort === key ? " selected" : "") +
        ">" + S.escapeHtml(SORTS[key].label) + "</option>";
    }
    return '<span class="sortbox">' +
      '<label for="sort" class="small muted">Order</label>' +
      '<select id="sort">' + options + "</select></span>";
  }

  function renderResults() {
    var results = currentResults();
    var bar = document.getElementById("resultbar");
    var active = activeFilterCount() + (state.q ? 1 : 0);

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
      (active ? '<button type="button" class="linkish" id="clear">Clear all filters</button>' : "") +
      sortHtml();
    // How many matched, independent of how many are drawn on this page. The bar says
    // it in prose two lines up; this is the same number somewhere it can be read back.
    bar.setAttribute("data-total", results.length);
    bar.setAttribute("data-page", state.page);
    bar.setAttribute("data-pages", last);

    var list = document.getElementById("results");
    var pager = document.getElementById("pager");
    if (!results.length) {
      /* Ticking a measure nothing covers is the commonest way to land here, and it
         is not a dead end - it is the answer. Say which measure, so the reader knows
         the scan looked and found nothing rather than that the page broke. */
      var barren = state.indicator.filter(function (name) { return countAll("indicator", name) === 0; });
      list.innerHTML = '<li class="empty">' + (barren.length
        ? "No source identified for " + (barren.length > 1 ? "these measures" : "this measure") +
          " in the current scan: " + S.escapeHtml(barren.join(", ")) + "."
        : "Nothing matches that combination.") + "</li>";
      pager.innerHTML = "";
      return;
    }

    pager.innerHTML = pagerHtml(state.page, last);

    list.innerHTML = page.map(function (study) {
      var open = !!expanded[study.id];
      return '<li class="result">' +
        '<button type="button" class="result__head" data-id="' + study.id + '" aria-expanded="' +
          open + '" aria-controls="d-' + study.id + '">' +
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
      row(S.label("topics"), S.topicTags(study)) +
      row(S.label("healthDomain"), S.tagList((study.domains || []).map(S.domainLabel))) +
      row(S.label("indicators"), S.tagList(study.indicators || [])) +
      row(S.label("summary"), S.escapeHtml(study.task)) +
      row(S.label("inputs"), S.escapeHtml(study.inputData)) +
      row(S.label("outputs"), S.escapeHtml(study.output)) +
      row(S.label("measures"), S.escapeHtml(study.metrics)) +
      row(S.label("geography"), S.escapeHtml(study.geoLevel)) +
      row(S.label("dataSources"), S.escapeHtml(study.dataSources)) +
      row(S.label("access"), S.accessBadge(study.access) +
        (study.accessNote ? " " + S.escapeHtml(study.accessNote) : "")) +
      row(S.label("sourceLinks"), links ? "<ul>" + links + "</ul>" : "") +
      row(S.label("scale"), S.escapeHtml(study.scale)) +
      row(S.label("keyAttributes"), S.escapeHtml(study.keyAttributes)) +
      row(S.label("country"), S.escapeHtml(study.country)) +
      row(S.label("sourceType"), S.escapeHtml(study.sourceType)) +
      "</dl>" +
      '<p class="small">' +
        (study.link ? S.externalLink(study.link, "Read the source") + " &nbsp;&middot;&nbsp; " : "") +
        '<a href="study.html?id=' + encodeURIComponent(study.id) + '">Open source page</a>' +
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
      if (input.dataset.facet === "domain") {
        setDomainTree(input.value, input.checked);
      } else if (input.dataset.facet === "indicator") {
        setSelected("indicator", input.value, input.checked);
        var domain = indicatorDomain(input.value);
        if (domain) syncDomainFromIndicators(domain);
      } else {
        setSelected(input.dataset.facet, input.value, input.checked);
      }
      // A different result set means the old page number means nothing.
      state.page = 1;
      render();
    });

    document.getElementById("filters").addEventListener("click", function (event) {
      var sub = event.target.closest("[data-toggle-sub]");
      if (sub) {
        var value = sub.dataset.toggleSub;
        var open = sub.getAttribute("aria-expanded") === "true";
        collapsedSubs[value] = open;
        setSubCollapsed(sub, open);
        syncFilterHeight();
        return;
      }

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

    document.getElementById("resultbar").addEventListener("change", function (event) {
      if (event.target.id !== "sort") return;
      state.sort = event.target.value;
      state.page = 1;
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
    normaliseNestedState();
    S.stampGenerated(meta);
    document.getElementById("search").value = state.q;
    wire();
    render();
  }).catch(function (err) {
    S.showLoadError(document.getElementById("browse-main"), err);
  });
})();
