/* ask.html - search the scan by asking a question in plain words.

   The question goes to the search server on this computer (search/serve.py).
   It asks a small language model to choose, from this site's own tags, the ones a
   useful source would carry, then ranks every source by how many of those tags it
   carries. The model's reasoning streams in while it is being written, so the reader
   sees what the search is about to look for before any result arrives: the point of
   showing the working is that a reader can tell a good search from a bad one.

   The reader can remove any tag the model chose and the list is ranked again at once,
   without asking the model a second time.

   Every badge and tag on a result comes from common.js, as on the browse page, so a
   source looks the same in both places. */

(function () {
  "use strict";

  var S = window.scan;
  var esc = S.escapeHtml;

  // The order the model answers in, which is also the browse page's filter order.
  var GROUPS = ["topic", "domain", "indicator", "geo", "access", "fee", "type", "region"];

  // The browse page's own names for these filters, so a tag reads the same in both.
  var GROUP_LABEL = {
    topic: "Focus area", domain: "Health focus", indicator: "Indicator",
    geo: "Geography", access: "Access", fee: "Application fee",
    type: "Kind of source", region: "Region"
  };

  var byId = {};
  var models = [];
  var defaultModel = "";
  var asking = 0;          // counts questions, so a late reply to an old one is ignored
  var controller = null;   // cancels the question in flight when a new one is asked
  var clock = null;
  var expanded = {};

  var state = blank();

  function blank() {
    return {
      question: "", model: "", modelName: "",
      running: false, started: 0,
      reasoning: "", error: "",
      original: null,      // the model's own tags, so the reader's edits can be undone
      result: null         // the latest answer from the server
    };
  }

  function el(id) { return document.getElementById(id); }

  // ---------- tags ----------

  function valueLabel(group, value) {
    if (group === "domain") return S.domainLabel(value);
    if (group === "access") return S.accessLabel(value);
    if (group === "fee") return S.feeLabel(value) || value;
    if (group === "type") return S.sourceGroupLabel(value);
    return value;
  }

  // LGA, PHN and HHS keep their full names on hover, the same as everywhere else.
  function valueHtml(group, value) {
    var full = group === "geo" ? S.tagTitle(value) : "";
    return full ? '<abbr title="' + esc(full) + '">' + esc(value) + "</abbr>"
                : esc(valueLabel(group, value));
  }

  function copyTags(tags) {
    var out = {};
    GROUPS.forEach(function (g) {
      if (tags && tags[g] && tags[g].length) out[g] = tags[g].slice();
    });
    return out;
  }

  function tagCount(tags) {
    return GROUPS.reduce(function (n, g) { return n + ((tags && tags[g]) || []).length; }, 0);
  }

  function removedTags() {
    if (!state.original || !state.result) return [];
    var now = state.result.tags || {};
    var out = [];
    GROUPS.forEach(function (g) {
      (state.original[g] || []).forEach(function (v) {
        if ((now[g] || []).indexOf(v) === -1) out.push(valueLabel(g, v));
      });
    });
    return out;
  }

  /* One row per group. Values in the same group are joined by "or" because that is
     how they are matched, here and on the browse page: a source needs any one of
     them. Each value can be removed on its own. */
  function tagsHtml(tags) {
    var groups = GROUPS.filter(function (g) { return tags[g] && tags[g].length; });
    if (!groups.length) {
      return '<p class="ask-none">No tags left, so the sources below are ranked by the ' +
        "words in your question.</p>";
    }
    return '<dl class="ask-tags">' + groups.map(function (g) {
      return "<dt>" + esc(GROUP_LABEL[g]) + "</dt><dd>" + tags[g].map(function (v) {
        return '<span class="ask-chip">' + valueHtml(g, v) +
          '<button type="button" class="ask-chip__x" data-group="' + esc(g) +
            '" data-value="' + esc(v) + '" aria-label="Remove ' + esc(valueLabel(g, v)) +
            '" title="Remove this tag">&times;</button></span>';
      }).join('<span class="ask-or">or</span>') + "</dd>";
    }).join("") + "</dl>";
  }

  // ---------- the panel above the results ----------

  function renderReading() {
    var box = el("reading");
    if (!state.running && !state.result && !state.error) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    var r = state.result;
    var html = "";

    if (state.error) {
      el("reading-body").innerHTML = '<div class="error"><p><b>The search could not finish.</b></p>' +
        "<p>" + esc(state.error) + "</p></div>";
      return;
    }

    if (state.reasoning) {
      html += '<div class="ask-why"><h3>Why these tags</h3><p>' + esc(state.reasoning) +
        (state.running ? '<span class="ask-caret" aria-hidden="true"></span>' : "") + "</p></div>";
    }

    if (state.running) {
      html += '<p class="ask-wait">' + (state.reasoning ? "Choosing tags" : "Reading your question") +
        ' <span class="ask-clock" id="clock"></span></p>';
    }

    if (r && !state.running) {
      if (r.usedModel === false) {
        html += '<div class="error ask-fallback"><p><b>The model could not be asked.</b> ' +
          esc(r.note || "") + "</p><p>These sources are ranked only by the words in your " +
          "question.</p></div>";
      } else if (!tagCount(state.original)) {
        html += '<p class="ask-none">The model chose no tags: nothing in this site\'s lists ' +
          "fits the question. The sources below are ranked only by the words in it, so they " +
          "may not be relevant.</p>";
      } else {
        html += "<h3>Tags chosen</h3>" + tagsHtml(r.tags);
        var gone = removedTags();
        if (gone.length) {
          html += '<p class="ask-removed">You removed ' + esc(gone.join(", ")) + ". " +
            '<button type="button" class="linkish" id="restore">Put back the model\'s tags</button></p>';
        } else if (tagCount(r.tags) > 1) {
          html += '<p class="ask-hint">Remove a tag with its &times; to widen the search.</p>';
        }
        if (r.rejected && r.rejected.length) {
          html += '<p class="small muted">Left out, because they are not tags on this site: ' +
            esc(r.rejected.join(", ")) + ".</p>";
        }
      }
      if (r.usedModel !== false && r.modelName) {
        html += '<p class="ask-meta">Answered by ' + esc(r.modelName) + " in " +
          Number(r.seconds || 0).toFixed(1) + " seconds, on this computer.</p>";
      }
    }

    el("reading-body").innerHTML = html;
    tick();
  }

  // ---------- results ----------

  function barHtml(r) {
    var n = r.results.length;
    var full = r.fullMatches;
    var text;
    if (!n) return "<span>Nothing in the scan matches this question, by tag or by word.</span>";
    if (!tagCount(r.tags)) {
      text = "No tags to search by, so these " + n + " are ranked by the words in your question.";
    } else if (!full) {
      text = "No source carries every tag. These " + n + " come closest.";
    } else if (full >= n) {
      text = "<b>" + full + "</b> " + (full === 1 ? "source carries" : "sources carry") +
        " every tag" + (full > n ? ". The first " + n + " are shown." : ".");
    } else {
      text = "<b>" + full + "</b> " + (full === 1 ? "source carries" : "sources carry") +
        " every tag, listed first. The rest carry some of them.";
    }
    return "<span>" + text + "</span>" + (r.url
      ? '<a class="ask-browse" href="' + esc(r.url) + '">Open ' +
        (full === 1 ? "it" : "these " + full) + " on the browse page</a>"
      : "");
  }

  /* Which of the chosen tags this source carries, and which it does not. This is the
     line that answers "why is this here", so it sits above the source's own tags. */
  function matchHtml(row) {
    if (!row.asked) return "";
    var whole = row.matched === row.asked;
    var bits = [];
    GROUPS.forEach(function (g) {
      (row.hits[g] || []).forEach(function (v) {
        bits.push('<span class="ask-hit">' + esc(valueLabel(g, v)) + "</span>");
      });
    });
    GROUPS.forEach(function (g) {
      if (!row.misses[g]) return;
      bits.push('<span class="ask-miss">' + esc(row.misses[g].map(function (v) {
        return valueLabel(g, v);
      }).join(" or ")) + "</span>");
    });
    return '<span class="ask-match' + (whole ? " ask-match--full" : "") + '">' +
      '<span class="ask-match__count">' +
        (whole ? "Carries every tag" : "Carries " + row.matched + " of " + row.asked) +
      "</span>" + bits.join("") + "</span>";
  }

  function trim(text, max) {
    if (!text || text.length <= max) return text || "";
    return text.slice(0, max).replace(/\s+\S*$/, "") + "…";
  }

  function detailRow(term, value) {
    if (!value) return "";
    return "<dt>" + esc(term) + "</dt><dd>" + value + "</dd>";
  }

  function detailHtml(study) {
    return "<dl>" +
      detailRow(S.label("topics"), S.topicTags(study)) +
      detailRow(S.label("healthDomain"), S.tagList((study.domains || []).map(S.domainLabel))) +
      detailRow(S.label("indicators"), S.tagList(study.indicators || [])) +
      detailRow(S.label("summary"), esc(study.task)) +
      detailRow(S.label("dataSources"), esc(study.dataSources)) +
      detailRow(S.label("dataYears"), esc(S.dataYearsText(study))) +
      detailRow(S.label("geography"), esc(study.geoLevel)) +
      detailRow(S.label("access"), S.accessBadge(study.access) +
        S.tagList(S.feeTags(study).map(S.feeLabel), S.feeTagClass(study)) +
        (study.accessNote ? " " + esc(study.accessNote) : "")) +
      detailRow(S.label("sourceLinks"), S.sourceLink(study)) +
      "</dl>" +
      '<p class="small"><a href="study.html?id=' + encodeURIComponent(study.id) +
        '">Open on its own page</a></p>';
  }

  function resultHtml(row) {
    var study = byId[row.id];
    if (!study) return "";
    var open = !!expanded[row.id];
    var years = S.dataYearsText(study);
    return '<li class="result">' +
      '<button type="button" class="result__head" data-id="' + esc(row.id) +
        '" aria-expanded="' + open + '" aria-controls="d-' + esc(row.id) + '">' +
        '<span class="result__body">' +
          '<span class="result__ref">' + esc(study.reference) + "</span>" +
          (years ? '<span class="result__years">Data covers ' + esc(years) + "</span>" : "") +
          '<span class="result__task">' + esc(trim(study.task, 210)) + "</span>" +
          matchHtml(row) +
          '<span class="meta-row">' + S.metaRow(study) + "</span>" +
        "</span>" +
        '<span class="result__chev" aria-hidden="true">' + (open ? "&#9650;" : "&#9660;") + "</span>" +
      "</button>" +
      '<div class="detail" id="d-' + esc(row.id) + '"' + (open ? "" : " hidden") + ">" +
        (open ? detailHtml(study) : "") + "</div></li>";
  }

  function renderAnswer() {
    var r = state.result;
    el("answer").hidden = !r;
    if (!r) {
      el("resultbar").innerHTML = "";
      el("results").innerHTML = "";
      return;
    }
    el("resultbar").innerHTML = barHtml(r);
    el("results").innerHTML = r.results.map(resultHtml).join("");
  }

  function render() {
    renderReading();
    renderAnswer();
  }

  function announce(text) {
    el("announce").textContent = text;
  }

  function summaryText(r) {
    var n = r.results.length;
    if (!n) return "No sources found.";
    return n + " sources listed" +
      (r.fullMatches ? ", " + r.fullMatches + " carrying every tag." : ".");
  }

  // ---------- the clock shown while the model works ----------

  function tick() {
    var node = el("clock");
    if (node && state.running) {
      node.textContent = ((Date.now() - state.started) / 1000).toFixed(1) + " s";
    }
  }

  function startClock() {
    stopClock();
    clock = setInterval(tick, 100);
  }

  function stopClock() {
    if (clock) clearInterval(clock);
    clock = null;
  }

  // ---------- talking to the server ----------

  /* The answer arrives as one JSON object per line, a line at a time, so the
     reasoning can be drawn while the model is still writing it. */
  function readLines(response, onLine) {
    if (!response.body || !response.body.getReader) {
      return response.text().then(function (text) {
        text.split("\n").forEach(function (line) { if (line.trim()) onLine(line); });
      });
    }
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    function pump() {
      return reader.read().then(function (chunk) {
        if (chunk.done) {
          buffer += decoder.decode();
          if (buffer.trim()) onLine(buffer);
          return undefined;
        }
        buffer += decoder.decode(chunk.value, { stream: true });
        var lines = buffer.split("\n");
        buffer = lines.pop();
        lines.forEach(function (line) { if (line.trim()) onLine(line); });
        return pump();
      });
    }
    return pump();
  }

  function onEvent(line) {
    var ev;
    try { ev = JSON.parse(line); } catch (err) { return; }
    if (ev.type === "start") {
      state.modelName = ev.modelName || "";
    } else if (ev.type === "reasoning") {
      state.reasoning = ev.text || "";
      renderReading();
    } else if (ev.type === "result") {
      finish(ev);
    } else if (ev.type === "error") {
      fail(ev.message || "Something went wrong.");
    }
  }

  function friendly(err) {
    var message = err && err.message ? err.message : String(err);
    // What the browser says when it cannot connect at all.
    if (/failed to fetch|networkerror|load failed/i.test(message)) {
      return "Could not reach the search server on this computer. Is serve.py still running?";
    }
    return message;
  }

  function ask(question, model) {
    if (controller) controller.abort();
    controller = typeof AbortController === "function" ? new AbortController() : null;
    var mine = ++asking;
    expanded = {};
    state = blank();
    state.question = question;
    state.model = model;
    state.running = true;
    state.started = Date.now();
    writeUrl();
    render();
    announce("Reading your question.");
    startClock();

    fetch("api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question, model: model }),
      signal: controller ? controller.signal : undefined
    }).then(function (response) {
      if (!response.ok) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          throw new Error(body.error || "The search server answered " + response.status + ".");
        });
      }
      return readLines(response, function (line) { if (mine === asking) onEvent(line); });
    }).then(function () {
      if (mine === asking && state.running) {
        fail("The search server stopped before it finished answering.");
      }
    }).catch(function (err) {
      if (mine !== asking || (err && err.name === "AbortError")) return;
      fail(friendly(err));
    });
  }

  function finish(payload) {
    stopClock();
    state.running = false;
    state.result = payload;
    state.reasoning = payload.reasoning || state.reasoning;
    state.original = copyTags(payload.tags);
    render();
    announce(summaryText(payload));
    saveCache();
  }

  function fail(message) {
    stopClock();
    state.running = false;
    state.error = message;
    state.result = null;
    render();
    announce("The search could not finish.");
  }

  /* Rank again for tags the reader has edited. The model is not asked again: its
     reasoning, its name and its time all stay as they were. */
  function rerank(tags) {
    var before = state.result;
    if (!before) return;
    var mine = asking;
    fetch("api/rank", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: state.question, tags: tags })
    }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) throw new Error(body.error || "The search server answered " + response.status + ".");
        return body;
      });
    }).then(function (payload) {
      if (mine !== asking) return;
      ["reasoning", "model", "modelName", "seconds", "usedModel", "rejected", "note"]
        .forEach(function (key) { payload[key] = before[key]; });
      state.result = payload;
      expanded = {};
      render();
      announce(summaryText(payload));
      saveCache();
      var restore = el("restore");
      if (restore) restore.focus();
    }).catch(function (err) {
      if (mine === asking) fail(friendly(err));
    });
  }

  function removeTag(group, value) {
    if (!state.result) return;
    var tags = copyTags(state.result.tags);
    tags[group] = (tags[group] || []).filter(function (v) { return v !== value; });
    rerank(copyTags(tags));
  }

  // ---------- the address bar, and coming back to the page ----------

  function writeUrl() {
    var params = new URLSearchParams();
    params.set("q", state.question);
    if (state.model && state.model !== defaultModel) params.set("model", state.model);
    try { history.replaceState(null, "", "ask.html?" + params.toString()); } catch (err) { /* file:// */ }
  }

  /* The answer is kept for this browser tab, so opening a source and coming back
     shows the same list at once rather than asking the model again. */
  function cacheKey(question, model) { return "ask:" + model + ":" + question; }

  function saveCache() {
    try {
      sessionStorage.setItem(cacheKey(state.question, state.model),
        JSON.stringify({ result: state.result, original: state.original }));
    } catch (err) { /* storage unavailable; nothing lost but the shortcut */ }
  }

  function restoreCache(question, model) {
    var saved = null;
    try { saved = JSON.parse(sessionStorage.getItem(cacheKey(question, model)) || "null"); }
    catch (err) { saved = null; }
    if (!saved || !saved.result) return false;
    state = blank();
    state.question = question;
    state.model = model;
    state.modelName = saved.result.modelName || "";
    state.reasoning = saved.result.reasoning || "";
    state.original = saved.original;
    state.result = saved.result;
    render();
    return true;
  }

  // ---------- the form ----------

  function currentModel() {
    return el("model").value || defaultModel;
  }

  function setModel(id) {
    var pick = el("model");
    for (var i = 0; i < pick.options.length; i++) {
      if (pick.options[i].value === id) pick.value = id;
    }
  }

  function describeModel() {
    var line = el("model-status");
    var chosen = models.filter(function (m) { return m.id === currentModel(); })[0];
    if (!chosen || !chosen.available) return;
    line.className = "ask-status ask-status--ok";
    line.textContent = chosen.name + " is ready" + (chosen.loaded === false
      ? ". It is not loaded into memory yet, so the first answer will take longer."
      : ".");
  }

  function loadStatus() {
    return fetch("api/status", { cache: "no-store" }).then(function (response) {
      return response.json();
    }).then(function (st) {
      models = st.models || [];
      defaultModel = st.default || "";
      var usable = models.filter(function (m) { return m.available; });
      el("model").innerHTML = (usable.length ? usable : models).map(function (m) {
        return '<option value="' + esc(m.id) + '"' + (m.id === st.default ? " selected" : "") + ">" +
          esc(m.name) + (m.recommended ? " (recommended)" : "") + "</option>";
      }).join("");
      el("model-pick").hidden = usable.length < 2;
      if (st.ok) {
        describeModel();
      } else {
        var line = el("model-status");
        line.className = "ask-status ask-status--warn";
        line.textContent = "The model is not available. " + (st.message || "") +
          " You can still search: results will be ranked by the words in your question.";
      }
    }).catch(function () {
      var line = el("model-status");
      line.className = "ask-status ask-status--warn";
      line.textContent = "Could not reach the search server to check the model.";
    });
  }

  function loadExamples() {
    return fetch("api/examples", { cache: "no-store" }).then(function (response) {
      return response.json();
    }).then(function (body) {
      var list = body.examples || [];
      if (!list.length) return;
      el("examples").innerHTML = '<span class="ask-examples__label">Try one of these:</span>' +
        list.map(function (ex) {
          return '<button type="button" class="ask-example" data-question="' + esc(ex.question) +
            '">' + esc(ex.question) + "</button>";
        }).join("");
      el("examples").hidden = false;
    }).catch(function () { /* the examples are a convenience; the page works without them */ });
  }

  function submit() {
    var question = el("question").value.replace(/\s+/g, " ").trim();
    if (!question) {
      el("question").focus();
      return;
    }
    ask(question, currentModel());
  }

  function wire() {
    el("ask-form").addEventListener("submit", function (event) {
      event.preventDefault();
      submit();
    });

    // Enter asks, as in any search box; Shift+Enter still starts a new line.
    el("question").addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        submit();
      }
    });

    el("model").addEventListener("change", describeModel);

    el("examples").addEventListener("click", function (event) {
      var button = event.target.closest("[data-question]");
      if (!button) return;
      el("question").value = button.getAttribute("data-question");
      submit();
    });

    el("reading").addEventListener("click", function (event) {
      var remove = event.target.closest(".ask-chip__x");
      if (remove) {
        removeTag(remove.getAttribute("data-group"), remove.getAttribute("data-value"));
        return;
      }
      if (event.target.closest("#restore")) rerank(copyTags(state.original));
    });

    el("results").addEventListener("click", function (event) {
      var head = event.target.closest(".result__head");
      if (!head) return;
      var id = head.getAttribute("data-id");
      expanded[id] = !expanded[id];
      renderAnswer();
      var again = document.querySelector('.result__head[data-id="' + id + '"]');
      if (again) again.focus();
    });
  }

  S.loadData(["studies.json", "meta.json"]).then(function (loaded) {
    loaded[0].forEach(function (s) { byId[s.id] = s; });
    S.setTopics(loaded[1].topics);
    S.stampGenerated(loaded[1]);
    wire();
    return Promise.all([loadStatus(), loadExamples()]);
  }).then(function () {
    // A question in the address bar is asked straight away, unless this tab already
    // has its answer from before.
    var question = S.param("q");
    if (!question) return;
    var model = S.param("model");
    if (model) setModel(model);
    el("question").value = question;
    if (!restoreCache(question, currentModel())) ask(question, currentModel());
  }).catch(function (err) {
    S.showLoadError(el("ask-main"), err);
  });
})();
