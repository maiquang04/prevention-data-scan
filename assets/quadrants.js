/* application.html?app=1 - the four-quadrant view.

   Rows are People and Places, columns are Actions and Measures. Cards are
   hand-authored in data/quadrants.json; the studies behind each card, and how
   obtainable their data is, come from studies.json. */

(function () {
  "use strict";

  var S = window.scan;
  var byId = {};
  var quad = null;
  var app = null;
  var openCard = null;
  var geoOrder = [];

  // Most obtainable first. A card's badge is the best case among its studies,
  // because the question a reader is asking is "could I act on this now?".
  var ACCESS_ORDER = [
    "Public",
    "Public (aggregate only)",
    "Restricted - application",
    "Not yet assessed",
    "Not a dataset"
  ];

  function bestAccess(ids) {
    var best = ACCESS_ORDER.length;
    ids.forEach(function (id) {
      var study = byId[id];
      if (!study) return;
      var at = ACCESS_ORDER.indexOf(study.access);
      if (at !== -1 && at < best) best = at;
    });
    return ACCESS_ORDER[best] || "Not yet assessed";
  }

  /* Every geography its sources cover, listed in the vocabulary's own order so no
     two cards order the same tags differently. On the card face rather than hidden
     behind a click: whether something is available for a council-sized area is the
     first thing anyone wants to know, and it should not need opening to find out. */
  function geoTagsFor(ids) {
    return geoOrder.filter(function (tag) {
      return ids.some(function (sid) {
        return byId[sid] && byId[sid].geoTags.indexOf(tag) !== -1;
      });
    });
  }

  function cardHtml(card, cellKey, index) {
    var id = cellKey + "-" + index;
    var known = card.studies.filter(function (sid) { return byId[sid]; });
    return '<button type="button" class="qcard" data-card="' + id +
      '" aria-expanded="false" aria-controls="p-' + id + '">' +
      '<span class="qcard__name">' + S.escapeHtml(card.name) + "</span>" +
      '<span class="qcard__note">' + S.escapeHtml(card.note) + "</span>" +
      '<span class="qcard__meta">' + S.accessBadge(bestAccess(card.studies)) +
        S.tagList(geoTagsFor(card.studies)) +
        "<span>" + known.length + (known.length === 1 ? " source" : " sources") + "</span>" +
      "</span></button>";
  }

  function panelHtml(card, id) {
    var items = card.studies.map(function (sid) {
      var study = byId[sid];
      if (!study) {
        return "<li>Unknown study id <code>" + S.escapeHtml(sid) + "</code></li>";
      }
      var where = study.dataLinks.filter(function (l) { return l.url; }).map(function (l) {
        return S.externalLink(l.url, l.label || S.shortHost(l.url));
      }).join(", ");
      return "<li>" + S.priorityDot(study.priority) +
        '<a href="study.html?id=' + encodeURIComponent(study.id) + '">' +
          S.escapeHtml(study.reference) + "</a>" +
        '<span class="meta-row">' + S.metaRow(study) + "</span>" +
        '<span class="small">' +
          (study.accessNote ? S.escapeHtml(study.accessNote) : S.escapeHtml(S.accessMeaning[study.access] || "")) +
          (where ? "<br>Where to get it: " + where : "") +
        "</span></li>";
    }).join("");

    return '<div class="panel" id="p-' + id + '">' +
      '<button type="button" class="panel__close" data-close="' + id +
        '" aria-label="Close">&times;</button>' +
      "<h3>" + S.escapeHtml(card.name) + "</h3>" +
      "<p class=\"small\">" + S.escapeHtml(card.note) + "</p>" +
      "<ul>" + items + "</ul></div>";
  }

  function render() {
    document.getElementById("app-title").textContent = app.title;
    document.getElementById("app-question").textContent = app.question;
    document.getElementById("app-intro").textContent = app.intro || "";
    document.title = app.title + " - Prevention data scan";

    var rows = quad.rows, cols = quad.cols;
    // data-key drives the header colours in the stylesheet, so adding a row or a
    // column to quadrants.json means adding one rule rather than editing markup.
    var html = '<div class="quad">' +
      '<div class="quad__corner"></div>' +
      cols.map(function (c) {
        return '<div class="quad__colhead" data-key="' + S.escapeHtml(c.key) + '">' +
          S.escapeHtml(c.label) + "<span>" + S.escapeHtml(c.note) + "</span></div>";
      }).join("");

    rows.forEach(function (r) {
      html += '<div class="quad__rowhead" data-key="' + S.escapeHtml(r.key) + '">' +
        S.escapeHtml(r.label) + "<span>" + S.escapeHtml(r.note) + "</span></div>";
      cols.forEach(function (c) {
        var key = r.key + "-" + c.key;
        var cards = (app.cells && app.cells[key]) || [];
        html += '<div class="quad__cell" data-row="' + S.escapeHtml(r.key) +
          '" data-col="' + S.escapeHtml(c.key) +
          '" data-label="' + S.escapeHtml(r.label + " - " + c.label) + '">' +
          (cards.length
            ? cards.map(function (card, i) { return cardHtml(card, key, i); }).join("")
            : '<p class="small muted">Nothing here yet.</p>') +
          "</div>";
      });
    });

    document.getElementById("grid").innerHTML = html + "</div>";
    renderStudyList();
  }

  function renderStudyList() {
    var mine = Object.keys(byId).map(function (k) { return byId[k]; })
      .filter(function (s) { return String(s.app) === String(S.param("app") || "1"); })
      .sort(function (a, b) { return a.num - b.num; });

    document.getElementById("studies").innerHTML = mine.map(function (s) {
      return "<li>" + S.priorityDot(s.priority) +
        '<a href="study.html?id=' + encodeURIComponent(s.id) + '">' + S.escapeHtml(s.reference) + "</a>" +
        "<p>" + S.escapeHtml(s.task) + "</p>" +
        '<span class="meta-row">' + S.metaRow(s) + "</span></li>";
    }).join("");
    document.getElementById("studies-count").textContent = mine.length;
  }

  function closePanel() {
    var panel = document.querySelector(".panel");
    if (panel) panel.parentNode.removeChild(panel);
    var open = document.querySelector('.qcard[aria-expanded="true"]');
    if (open) open.setAttribute("aria-expanded", "false");
    openCard = null;
  }

  function wire() {
    document.getElementById("grid").addEventListener("click", function (event) {
      var close = event.target.closest("[data-close]");
      if (close) {
        var id = close.dataset.close;
        closePanel();
        var button = document.querySelector('.qcard[data-card="' + id + '"]');
        if (button) button.focus();
        return;
      }

      var button = event.target.closest(".qcard");
      if (!button) return;
      var id = button.dataset.card;
      var wasOpen = openCard === id;
      closePanel();
      if (wasOpen) return;

      var parts = id.split("-");
      var index = Number(parts.pop());
      var cellKey = parts.join("-");
      var card = app.cells[cellKey][index];

      button.setAttribute("aria-expanded", "true");
      button.insertAdjacentHTML("afterend", panelHtml(card, id));
      openCard = id;
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && openCard) {
        var button = document.querySelector('.qcard[data-card="' + openCard + '"]');
        closePanel();
        if (button) button.focus();
      }
    });
  }

  S.loadData(["studies.json", "meta.json", "quadrants.json"]).then(function (loaded) {
    loaded[0].forEach(function (s) { byId[s.id] = s; });
    S.stampGenerated(loaded[1]);
    geoOrder = loaded[1].geoTags || [];
    quad = loaded[2];

    var wanted = S.param("app") || "1";
    app = quad.applications[wanted];
    if (!app) {
      document.getElementById("app-main").innerHTML =
        '<div class="error"><p><b>Application ' + S.escapeHtml(wanted) +
        " does not have a quadrant view yet.</b></p><p>Application 1 is the one built so far. " +
        'Everything scanned for Applications 5 and 6 is on the <a href="browse.html">browse page</a>.</p></div>';
      return;
    }
    render();
    wire();
  }).catch(function (err) {
    S.showLoadError(document.getElementById("app-main"), err);
  });
})();
