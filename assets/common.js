/* Shared helpers. Loaded by every page before its own script. */

(function (global) {
  "use strict";

  var ACCESS_CLASS = {
    "Public": "badge--public",
    "Public (aggregate only)": "badge--aggregate",
    "Restricted - application": "badge--restricted",
    "Not a dataset": "badge--notdata",
    "Not yet assessed": "badge--unassessed"
  };

  var ACCESS_LABEL = {
    "Public": "Public data",
    "Public (aggregate only)": "Public summaries",
    "Restricted - application": "Apply for access",
    "Not a dataset": "No dataset",
    "Not yet assessed": "Access unchecked"
  };

  // Shown next to the badge so a reader who has never met the word "restricted"
  // in this context still knows what it means.
  var ACCESS_MEANING = {
    "Public": "Ready to download.",
    "Public (aggregate only)": "Published tables, maps or summaries are available; record-level data is not.",
    "Restricted - application": "The data exists, but you need approval from the data holder.",
    "Not a dataset": "A paper, guide or method rather than data to download.",
    "Not yet assessed": "Access has not been checked yet."
  };

  var FIELD_LABEL = {
    topics: "Related focus areas",
    healthDomain: "Health focus",
    indicators: "Indicators",
    summary: "Purpose",
    inputs: "Input",
    outputs: "Output",
    measures: "Measures",
    geography: "Geography",
    geographyTags: "Geography tags",
    dataSources: "Underlying data",
    access: "Access",
    sourceLinks: "Source links",
    scale: "Scale",
    keyAttributes: "Key details",
    country: "Country or system",
    sourceType: "Specific source type",
    sourceKind: "Kind of source"
  };

  var DOMAIN_LABEL = {
    "Physical Activity": "Physical activity",
    "Healthy Eating": "Healthy eating",
    "Implementation": "Implementation",
    "Social": "Social connection",
    "Equity": "Equity",
    "Prosperity & Productivity": "Prosperity and productivity",
    "Mental Wellbeing": "Mental wellbeing"
  };

  var SOURCE_GROUP_LABEL = {
    "Peer-reviewed": "Peer-reviewed paper",
    "Government report": "Government report",
    "Published dataset": "Published dataset",
    "International agency report": "International agency report",
    "Academic or institutional report": "Academic or institutional source"
  };

  /* The three geography tags that are abbreviations. They stay short on screen and
     expand on hover: the short form is what the workbook, the filter and the URL all
     call them, and the full names would push every card taller. Same teach-by-hover
     as ACCESS_MEANING above. Anything not listed here returns "" and is drawn plain. */
  var TAG_TITLE = {
    "LGA": "Local government area",
    "PHN": "Primary Health Network",
    "HHS": "Hospital and Health Service"
  };

  function tagTitle(value) {
    return TAG_TITLE[value] || "";
  }

  /* The topic list, set once from meta.json by every page after it loads. Kept here
     rather than passed around so all four pages render a topic's name the same way -
     the same reasoning as metaRow() below for a source's badge and tags. */
  var TOPICS = [];
  var TOPIC_BY_ID = {};
  var LEGACY_APP_TO_TOPIC = {};

  function setTopics(topics) {
    TOPICS = topics || [];
    TOPIC_BY_ID = {};
    LEGACY_APP_TO_TOPIC = {};
    TOPICS.forEach(function (t) {
      TOPIC_BY_ID[t.id] = t;
      if (t.legacyApp != null) LEGACY_APP_TO_TOPIC[String(t.legacyApp)] = t.id;
    });
    // The header link to the grid view is hardcoded HTML ("Topic view"), because
    // it exists on several pages and none of them can know the topic list before
    // this data arrives. Point it at whichever topic actually has a grid, so a
    // second one being built later needs no page edited by hand. data-grid-nav
    // carries the wording to use in front of the topic's title, if any.
    var gridded = TOPICS.filter(function (t) { return t.hasGrid; })[0];
    if (gridded) {
      var links = document.querySelectorAll("[data-grid-nav]");
      for (var i = 0; i < links.length; i++) {
        var prefix = links[i].getAttribute("data-grid-nav");
        links[i].textContent = (prefix ? prefix + " " : "") + gridded.title;
        links[i].setAttribute("href", "application.html?topic=" + gridded.id);
      }
    }

    // markCurrentNav already ran once on DOMContentLoaded, before this data existed,
    // so any nav link carrying ?topic= or the legacy ?app= could not be matched yet.
    // Re-run now that a topic id can actually be resolved, and now that the grid
    // link's own href has just been corrected above.
    markCurrentNav();
  }

  function topicTitle(id) {
    var t = TOPIC_BY_ID[id];
    return t ? t.title : id;
  }

  // The old ?app=1 style links already sent out map to exactly one topic id
  // each, via the sheet each topic was seeded from.
  function legacyAppToTopic(appNumber) {
    return LEGACY_APP_TO_TOPIC[String(appNumber)] || null;
  }

  /* A source's topics, rendered as the same tag markup used for its geography. */
  function topicTags(study) {
    return tagList((study.topics || []).map(topicTitle));
  }

  function label(key) {
    return FIELD_LABEL[key] || key;
  }

  function accessLabel(access) {
    return ACCESS_LABEL[access] || access || "Access unchecked";
  }

  function domainLabel(domain) {
    return DOMAIN_LABEL[domain] || domain;
  }

  function sourceGroupLabel(group) {
    return SOURCE_GROUP_LABEL[group] || group;
  }

  /* The topic id meant by the current page: ?topic= first, then the legacy ?app=
     link already sent out, then the first topic with a built grid, then
     whatever topic is listed first. */
  function currentTopicId() {
    var topic = param("topic");
    if (topic && TOPIC_BY_ID[topic]) return topic;

    var app = param("app");
    if (app && LEGACY_APP_TO_TOPIC[app]) return LEGACY_APP_TO_TOPIC[app];

    var gridded = TOPICS.filter(function (t) { return t.hasGrid; })[0];
    if (gridded) return gridded.id;

    return TOPICS[0] ? TOPICS[0].id : null;
  }

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function accessBadge(access) {
    var cls = ACCESS_CLASS[access] || "badge--notdata";
    return '<span class="badge ' + cls + '" title="' + escapeHtml(ACCESS_MEANING[access] || "") +
      '">' + escapeHtml(accessLabel(access)) + "</span>";
  }

  /* modifier is an extra class on every pill in the list, for callers that need one
     kind of tag to stay distinguishable from another in the same row. */
  function tagList(tags, modifier) {
    var cls = "tag" + (modifier ? " " + modifier : "");
    return (tags || []).map(function (tag) {
      var full = tagTitle(tag);
      var text = full
        ? '<abbr title="' + escapeHtml(full) + '">' + escapeHtml(tag) + "</abbr>"
        : escapeHtml(tag);
      return '<span class="' + cls + '">' + text + "</span>";
    }).join("");
  }

  /* Every checkbox group in the browse filter panel has a matching pill here, so a
     reader can see why a row came back without opening it. Relevance is the one
     exception and stays the coloured dot beside the row number.

     A pill is still not a button. The whole row sits inside a <button> on both the
     browse list and the quadrant cards, and an <a> cannot nest in one, so these look
     like the filter they belong to without being clickable. The study page is where
     they are links, because nothing wraps them there.

     Indicators go last. One source carries ten of them, and put anywhere earlier they
     would push the short, always-present tags off the first line. */
  function metaRow(study) {
    return accessBadge(study.access) +
      tagList((study.topics || []).map(topicTitle), "tag--topic") +
      tagList((study.domains || []).map(domainLabel), "tag--domain") +
      tagList(study.geoTags) +
      tagList(study.regions || [], "tag--region") +
      tagList(study.sourceGroup ? [sourceGroupLabel(study.sourceGroup)] : [], "tag--type") +
      tagList(study.indicators || [], "tag--indicator");
  }

  function externalLink(url, label) {
    if (!url) return escapeHtml(label || "");
    return '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer">' +
      escapeHtml(label || url) + "</a>";
  }

  function shortHost(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, "");
    } catch (err) {
      return url;
    }
  }

  /* Load the generated JSON. Everything the site draws comes from here, so the
     failure message has to be useful - the usual cause is opening the HTML file
     directly, which browsers block fetch() for. */
  function loadData(files) {
    return Promise.all(files.map(function (name) {
      return fetch("data/" + name, { cache: "no-cache" }).then(function (response) {
        if (!response.ok) throw new Error(name + ": HTTP " + response.status);
        return response.json();
      });
    }));
  }

  function showLoadError(target, err) {
    var isFileProtocol = global.location.protocol === "file:";
    var html = '<div class="error"><p><b>Could not load the data.</b></p>';
    if (isFileProtocol) {
      html += "<p>This page was opened straight from the file system, and browsers block " +
        "pages from reading local files that way. Start a small local server instead: open a " +
        "terminal in this folder, run <code>python -m http.server 8000</code>, then visit " +
        "<code>http://localhost:8000</code>.</p>";
    } else {
      // A stale cached script has caused this before, and the symptom looks like a
      // data failure, so offer the hard reload first.
      html += "<p>If the page was working a moment ago, the browser may be holding an " +
        "old copy of one of its files. A hard reload usually clears it: " +
        "<b>Ctrl+Shift+R</b>, or <b>Cmd+Shift+R</b> on a Mac.</p>" +
        "<p class=\"small\">If that does not help, run <code>python build/export_data.py</code> " +
        "to regenerate the data files.</p>";
    }
    html += '<p class="small">' + escapeHtml(err && err.message ? err.message : String(err)) + "</p></div>";
    target.innerHTML = html;
  }

  function markCurrentNav() {
    var here = global.location.pathname.split("/").pop() || "index.html";
    var params = new URLSearchParams(global.location.search);
    var links = document.querySelectorAll(".site-nav a");

    for (var i = 0; i < links.length; i++) {
      var parts = links[i].getAttribute("href").split("?");
      if (parts[0] !== here) continue;

      // A nav link carrying a query has to match it too, or one topic's link would
      // light up while another topic is open. Compare by topic id rather than the
      // raw parameter, so a page reached via the legacy ?app= link still matches
      // the ?topic= link in the header.
      var matches = true;
      if (parts[1]) {
        new URLSearchParams(parts[1]).forEach(function (value, key) {
          if (key !== "topic" && key !== "app") {
            if (params.get(key) !== value) matches = false;
            return;
          }
          var linkTopic = key === "topic" ? value : LEGACY_APP_TO_TOPIC[value];
          if (currentTopicId() !== linkTopic) matches = false;
        });
      }
      if (matches) links[i].setAttribute("aria-current", "page");
    }
  }

  function stampGenerated(meta) {
    var nodes = document.querySelectorAll("[data-generated]");
    if (!nodes.length || !meta || !meta.generated) return;
    var parts = meta.generated.split("-");
    var months = ["January", "February", "March", "April", "May", "June",
      "July", "August", "September", "October", "November", "December"];
    var pretty = Number(parts[2]) + " " + months[Number(parts[1]) - 1] + " " + parts[0];
    for (var i = 0; i < nodes.length; i++) nodes[i].textContent = pretty;
  }

  function param(name) {
    return new URLSearchParams(global.location.search).get(name);
  }

  global.scan = {
    escapeHtml: escapeHtml,
    accessBadge: accessBadge,
    accessLabel: accessLabel,
    accessMeaning: ACCESS_MEANING,
    setTopics: setTopics,
    topicTitle: topicTitle,
    topicTags: topicTags,
    label: label,
    domainLabel: domainLabel,
    sourceGroupLabel: sourceGroupLabel,
    currentTopicId: currentTopicId,
    legacyAppToTopic: legacyAppToTopic,
    tagList: tagList,
    tagTitle: tagTitle,
    metaRow: metaRow,
    externalLink: externalLink,
    shortHost: shortHost,
    loadData: loadData,
    showLoadError: showLoadError,
    markCurrentNav: markCurrentNav,
    stampGenerated: stampGenerated,
    param: param
  };

  document.addEventListener("DOMContentLoaded", markCurrentNav);
})(window);
