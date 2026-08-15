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

  // Shown next to the badge so a reader who has never met the word "restricted"
  // in this context still knows what it means for them.
  var ACCESS_MEANING = {
    "Public": "The data can be downloaded now, at no cost.",
    "Public (aggregate only)": "The published results and maps are free. The records behind them are not released.",
    "Restricted - application": "The data exists but you have to apply to whoever holds it.",
    "Not a dataset": "A review, guide or set of principles rather than data you can download.",
    "Not yet assessed": "We have not checked how obtainable this one is yet."
  };

  var PRIORITY_LABEL = {
    "highly-relevant": "Highly relevant",
    "relevant": "Relevant",
    "unmarked": "Not marked"
  };

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function accessBadge(access) {
    var cls = ACCESS_CLASS[access] || "badge--notdata";
    return '<span class="badge ' + cls + '" title="' + escapeHtml(ACCESS_MEANING[access] || "") +
      '">' + escapeHtml(access || "Not yet assessed") + "</span>";
  }

  function priorityDot(priority) {
    var label = PRIORITY_LABEL[priority] || "Not marked";
    return '<span class="dot dot--' + escapeHtml(priority) + '" role="img" aria-label="' +
      escapeHtml(label) + '" title="' + escapeHtml(label) + '"></span>';
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
      html += "<p>Run <code>python build/export_data.py</code> to regenerate the data files, " +
        "then reload.</p>";
    }
    html += '<p class="small">' + escapeHtml(err && err.message ? err.message : String(err)) + "</p></div>";
    target.innerHTML = html;
  }

  function markCurrentNav() {
    var here = global.location.pathname.split("/").pop() || "index.html";
    var links = document.querySelectorAll(".site-nav a");
    for (var i = 0; i < links.length; i++) {
      if (links[i].getAttribute("href") === here) links[i].setAttribute("aria-current", "page");
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
    accessMeaning: ACCESS_MEANING,
    priorityDot: priorityDot,
    priorityLabel: PRIORITY_LABEL,
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
