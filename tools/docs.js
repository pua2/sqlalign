// Docs chrome: theme toggle, version selector, on-this-page highlighting, and
// search over the generated index. No dependencies -- the site is static files
// on GitHub Pages, so anything that needs a server or a CDN cannot be used.
(function () {
  var root = document.documentElement;

  // ---- theme -------------------------------------------------------------
  var stored = null;
  try { stored = localStorage.getItem("sqlalign-theme"); } catch (e) { /* private mode */ }
  if (stored) root.setAttribute("data-theme", stored);
  var toggle = document.getElementById("theme");
  if (toggle) toggle.addEventListener("click", function () {
    var dark = root.getAttribute("data-theme") === "dark" ||
      (!root.getAttribute("data-theme") &&
        window.matchMedia("(prefers-color-scheme: dark)").matches);
    var next = dark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("sqlalign-theme", next); } catch (e) { /* ignore */ }
  });

  // ---- version selector --------------------------------------------------
  var version = document.querySelector(".version");
  if (version) version.addEventListener("change", function () {
    // Every version is a sibling directory holding the same page names, so the
    // reader stays on the page they were reading.
    var page = location.pathname.split("/").pop() || "index.html";
    location.href = "../" + version.value + "/" + page;
  });

  // ---- on-this-page ------------------------------------------------------
  var links = [].slice.call(document.querySelectorAll(".toc a"));
  if (links.length && "IntersectionObserver" in window) {
    var byId = {};
    links.forEach(function (a) { byId[a.getAttribute("href").slice(1)] = a; });
    var seen = {};
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { seen[e.target.id] = e.isIntersecting; });
      var current = null;
      Object.keys(byId).forEach(function (id) { if (seen[id] && !current) current = id; });
      links.forEach(function (a) { a.classList.remove("here"); });
      if (current && byId[current]) byId[current].classList.add("here");
    }, { rootMargin: "-70px 0px -75% 0px" });
    Object.keys(byId).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) io.observe(el);
    });
  }

  // ---- search ------------------------------------------------------------
  var box = document.getElementById("q");
  var panel = document.getElementById("results");
  if (!box || !panel) return;
  var docs = null, cursor = -1;

  function load() {
    if (docs) return Promise.resolve(docs);
    return fetch("search-index.json")
      .then(function (r) { return r.json(); })
      .then(function (d) { docs = d; return d; });
  }

  function escape(s) {
    return s.replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function snippet(text, query) {
    var i = text.toLowerCase().indexOf(query);
    if (i < 0) return "";
    var from = Math.max(0, i - 45), to = Math.min(text.length, i + query.length + 75);
    return (from ? "…" : "") + escape(text.slice(from, i)) +
      "<mark>" + escape(text.slice(i, i + query.length)) + "</mark>" +
      escape(text.slice(i + query.length, to)) + (to < text.length ? "…" : "");
  }

  function search(query) {
    var hits = [];
    docs.forEach(function (d) {
      // A heading match beats a body match: someone typing `river_gutter` wants
      // the setting, not the paragraph that mentions it.
      d.headings.forEach(function (h) {
        if (h.text.toLowerCase().indexOf(query) >= 0) {
          hits.push({ rank: 0, page: d.title, href: d.slug + ".html#" + h.id,
                      label: h.text, hint: "" });
        }
      });
      if (d.title.toLowerCase().indexOf(query) >= 0) {
        hits.push({ rank: 1, page: d.title, href: d.slug + ".html",
                    label: d.title, hint: "" });
      }
      var s = snippet(d.text, query);
      if (s) hits.push({ rank: 2, page: d.title, href: d.slug + ".html",
                         label: d.title, hint: s });
    });
    hits.sort(function (a, b) { return a.rank - b.rank; });
    return hits.slice(0, 12);
  }

  function render(hits) {
    cursor = -1;
    if (!hits.length) {
      panel.innerHTML = '<p class="empty">No matches.</p>';
    } else {
      // Everything interpolated here is escaped. The page/label/href come from
      // the build-time index (slugs are [a-z0-9-] by construction), and the
      // hint is built by `snippet`, which escapes each slice before inserting
      // its own <mark>. The query itself is never interpolated raw -- only the
      // matching span of already-escaped document text is.
      panel.innerHTML = hits.map(function (h) {
        return '<a href="' + escape(h.href) + '"><span class="r-page">' + escape(h.page) +
          "</span><br>" + escape(h.label) +
          (h.hint ? '<br><span class="r-hit">' + h.hint + "</span>" : "") + "</a>";
      }).join("");
    }
    panel.hidden = false;
  }

  box.addEventListener("input", function () {
    var query = box.value.trim().toLowerCase();
    if (query.length < 2) { panel.hidden = true; return; }
    load().then(function () { render(search(query)); });
  });

  box.addEventListener("keydown", function (e) {
    var items = [].slice.call(panel.querySelectorAll("a"));
    if (e.key === "Escape") { panel.hidden = true; box.blur(); return; }
    if (!items.length || panel.hidden) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      cursor += e.key === "ArrowDown" ? 1 : -1;
      if (cursor < 0) cursor = items.length - 1;
      if (cursor >= items.length) cursor = 0;
      items.forEach(function (a) { a.classList.remove("on"); });
      items[cursor].classList.add("on");
      items[cursor].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" && cursor >= 0) {
      e.preventDefault();
      location.href = items[cursor].getAttribute("href");
    }
  });

  document.addEventListener("click", function (e) {
    if (!panel.contains(e.target) && e.target !== box) panel.hidden = true;
  });

  // `/` focuses search, the convention every docs site with a search box uses.
  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && document.activeElement !== box &&
        !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
      e.preventDefault();
      box.focus();
    }
  });
})();
