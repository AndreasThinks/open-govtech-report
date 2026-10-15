/* Open GovTech Index: a static site over the /v1 API.
 *
 * Data comes from the API configured in config.js (same origin by default). When the API is
 * unreachable, everything falls back to sample.js; when the API is up but no decision-model run
 * is stored, only the theme views fall back. A banner says which.
 *
 * Routing: when the API serves the site it sets window.OGI_PATHS, and pages live at real paths
 * (/theme/geospatial) that can be shared and indexed. Copied elsewhere as static files, the site
 * routes by hash (#/theme/geospatial). Templates always write "#/..." links; fixLinks() and the
 * click handler turn them into paths.
 */
(function () {
  "use strict";
  const CONFIG = window.OGI_CONFIG || {};
  const API = (CONFIG.api || "").replace(/\/$/, "");
  const PATHS = !!window.OGI_PATHS;
  const CONTACT = CONFIG.contact || "contact@andreasthinks.me";
  const SAMPLE = window.OGI_SAMPLE;
  const app = document.getElementById("app");
  const tip = document.getElementById("tip");
  const state = { live: null, sampleThemes: false, overview: null, themes: null, countries: null, taxonomy: null, places: null };

  // ------------------------------------------------------------------ utilities
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const num = n => (n ?? 0).toLocaleString("en-GB");
  const pct = (g, d = 0) => g == null ? "new" : (g > 0 ? "+" : "") + g.toFixed(d) + "%";
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const NS = "http://www.w3.org/2000/svg";
  const el = (n, a = {}) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); return e; };
  const txt = (attrs, s) => { const t = el("text", attrs); t.textContent = s; return t; };
  const enc = encodeURIComponent;
  const date = s => s ? new Date(s).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "unknown";
  const monthLabel = m => new Date(m + "-01T00:00:00Z").toLocaleDateString("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });
  function ago(s) {
    if (!s) return "unknown";
    const d = (Date.now() - new Date(s)) / 864e5;
    if (d < 1) return "today"; if (d < 2) return "yesterday"; if (d < 14) return Math.floor(d) + " days ago";
    if (d < 60) return Math.floor(d / 7) + " weeks ago"; if (d < 730) return Math.floor(d / 30) + " months ago";
    return Math.floor(d / 365) + " years ago";
  }
  // Repos from other forges have a host-qualified owner: "gitlab.com/group/sub/name".
  const encPath = s => s.split("/").map(enc).join("/");
  const repoHref = r => `#/repo/${encPath(r.full_name)}`;
  const themeHref = id => `#/theme/${enc(id)}`;
  const countryHref = slug => `#/country/${enc(slug)}`;
  const accountHref = owner => `#/account/${encPath(owner)}`;
  const ownerOf = r => r.full_name.split("/").slice(0, -1).join("/");
  const hostOf = url => { try { return new URL(url).host; } catch (e) { return "github.com"; } };
  const hostName = url => { const h = hostOf(url); return h === "github.com" ? "GitHub" : h; };
  // Site pages, as opposed to API, feed and badge paths that the browser should load itself.
  const SERVER_PATH = /^\/(v1|docs|redoc|openapi|mcp|feeds|badge|api|healthz|sitemap|robots)\b|\.[a-z0-9]+$/i;
  function nav(href) {
    if (!PATHS) { location.hash = href; return; }
    window.history.pushState(null, "", href.replace(/^#/, ""));
    route();
  }
  function fixLinks(root) {
    if (!PATHS) return;
    root.querySelectorAll('a[href^="#/"]').forEach(a => a.setAttribute("href", a.getAttribute("href").slice(1)));
  }

  function showTip(html, e) {
    tip.innerHTML = html; tip.style.opacity = 1;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.max(4, Math.min(e.clientX + 12, innerWidth - w - 4)) + "px";
    tip.style.top = Math.max(4, e.clientY - h - 12) + "px";
  }
  const hideTip = () => { tip.style.opacity = 0; };
  function hover(node, html) {
    node.addEventListener("mousemove", e => showTip(html, e));
    node.addEventListener("mouseleave", hideTip);
  }
  function linkify(node, href, label) {
    node.setAttribute("tabindex", 0); node.setAttribute("role", "link"); node.setAttribute("aria-label", label);
    node.addEventListener("click", () => nav(href));
    node.addEventListener("keydown", e => { if (e.key === "Enter") nav(href); });
  }

  // ------------------------------------------------------------------ data
  async function get(path) {
    const r = await fetch(API + path, { headers: { Accept: "application/json" } });
    if (!r.ok) { const err = new Error(`${r.status} ${path}`); err.status = r.status; throw err; }
    return r.json();
  }
  async function boot() {
    try {
      const [overview, themes] = await Promise.all([get("/v1/overview"), get("/v1/themes")]);
      state.live = true; state.overview = overview;
      state.sampleThemes = !themes.model || !themes.themes.length;
      state.themes = state.sampleThemes ? SAMPLE.themes : themes;
    } catch (e) {
      state.live = false; state.sampleThemes = true;
      state.overview = SAMPLE.overview; state.themes = SAMPLE.themes;
    }
    // A pace on a handful of repositories is noise, so it is shown only where a theme has a status.
    state.themes.themes.forEach(t => { if (t.status === "too_small") t.pace = null; });
    const banner = document.getElementById("banner");
    if (!state.live) {
      banner.innerHTML = "Sample data: the index API could not be reached, so every figure on this site is an example.";
      banner.hidden = false;
    } else if (state.sampleThemes) {
      banner.innerHTML = "Repository search and details are live. Theme views, including their repository lists, are examples until the classification run is published. <a href=\"#/method\">Why?</a>";
      banner.hidden = false;
    }
    // Partial classification coverage is normal; it is reported quietly in the footer, not a banner.
    const c = state.overview.classification, status = document.getElementById("status");
    if (status && state.live && c && c.model && !state.sampleThemes) {
      status.innerHTML = `Themes cover ${c.coverage}% of original repositories. <a href="#/about" data-scroll="themes">How themes work</a>`;
      status.hidden = false;
    }
    renderSponsor();
    fixLinks(document);
  }
  const needsLive = () => { const e = new Error("This page needs the live index API, which could not be reached."); e.status = 503; return Promise.reject(e); };
  const data = {
    theme: id => state.sampleThemes ? Promise.resolve(SAMPLE.theme(id)) : get(`/v1/themes/${enc(id)}`).catch(e => e.status === 404 ? null : Promise.reject(e)),
    repos: params => {
      const themed = params.get("domain") || params.get("tag") || params.get("kind");
      if (!state.live) return Promise.resolve(SAMPLE.repos(params));
      if (themed && state.sampleThemes) return Promise.resolve({ unavailable: true, items: [], total: null });
      return get(`/v1/repos?${params}`);
    },
    repo: async (o, n) => {
      if (!state.live) return SAMPLE.repo(o, n);
      try { return await get(`/v1/repos/${encPath(o)}/${enc(n)}`); } catch (e) { if (e.status === 404) return null; throw e; }
    },
    context: async (o, n) => {
      if (!state.live) return SAMPLE.context(o, n);
      try { return await get(`/v1/repos/${encPath(o)}/${enc(n)}/context`); } catch (e) { return null; }
    },
    countries: async () => state.countries ||= state.live ? await get("/v1/countries") : SAMPLE.countries,
    places: async () => state.places ||= state.live ? await get("/v1/places") : needsLive(),
    country: slug => state.live ? get(`/v1/countries/${enc(slug)}`).catch(e => e.status === 404 ? null : Promise.reject(e)) : needsLive(),
    account: owner => state.live ? get(`/v1/accounts/${encPath(owner)}`).catch(e => e.status === 404 ? null : Promise.reject(e)) : needsLive(),
    week: () => state.live ? get("/v1/week") : needsLive(),
    reuse: () => state.live ? get("/v1/reuse") : needsLive(),
    practice: () => state.live ? get("/v1/practice") : needsLive(),
    blocks: () => state.live ? get("/v1/building-blocks") : needsLive(),
  };

  // ------------------------------------------------------------------ theme maths
  const tags = () => state.themes.themes.filter(t => t.type === "tag" && t.repos > 0);
  const domains = () => state.themes.themes.filter(t => t.type === "domain");
  const themeById = id => state.themes.themes.find(t => t.id === id);
  function overallGrowth() {
    const tl = state.overview.timeline, now = tl[tl.length - 1], then = tl[tl.length - 13];
    return then && then.total ? (now.total - then.total) / then.total * 100 : 0;
  }
  // Momentum comes from the API. Pace is new repositories in the last 12 complete months against the
  // 12 before, divided by the same ratio for the whole index, with an 80% interval. Status:
  // emerging (rising with confidence across 5+ accounts, none over half), concentrated (rising, but
  // one team's work), cooling (confidently slower), steady, or too_small.
  const STATUS = { emerging: "Emerging", concentrated: "One team busy", cooling: "Cooling", steady: "Keeping pace", too_small: "Too few to tell" };
  const isEmerging = t => t.status === "emerging";
  // Ranked by how many more (or fewer) new repositories a theme had than keeping pace with the index
  // would give, so a large theme's shift outranks a small theme's noisy swing.
  const excess = t => t.new_window - t.new_prior * (state.themes.index_pace || 1);
  const byStatus = (s, dir = -1) => tags().filter(t => t.status === s).sort((a, b) => dir * (excess(a) - excess(b)));
  const risers = () => byStatus("emerging");
  const cooling = () => byStatus("cooling", 1);
  const oneTeam = () => byStatus("concentrated");
  function paceText(p, long) {
    if (p == null) return "–";
    const d = Math.round((p - 1) * 100);
    if (long) return d === 0 ? "same pace as the index" : `${Math.abs(d)}% ${d > 0 ? "faster" : "slower"} than the index`;
    return (d > 0 ? "+" : d < 0 ? "−" : "") + Math.abs(d) + "%";
  }
  const indexPaceText = () => { const p = state.themes.index_pace; return p ? `${p >= 1 ? "up" : "down"} ${Math.abs(Math.round((p - 1) * 100))}%` : "unchanged"; };
  function paceColour(p) {
    if (p == null) return `color-mix(in srgb,var(--faint) 22%,var(--bg))`;
    const s = Math.max(-1, Math.min(1, Math.log(p) / Math.log(2)));
    return s >= 0 ? `color-mix(in srgb,var(--hot) ${Math.round(8 + s * 82)}%,var(--bg))` : `color-mix(in srgb,var(--cool) ${Math.round(8 - s * 62)}%,var(--bg))`;
  }

  // ------------------------------------------------------------------ charts
  function spark(vals, color) {
    const w = 200, h = 30, max = Math.max(...vals), min = Math.min(...vals), span = (max - min) || 1, step = w / (vals.length - 1 || 1);
    const pts = vals.map((v, i) => `${(i * step).toFixed(1)},${(h - 2 - (v - min) / span * (h - 4)).toFixed(1)}`).join(" ");
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polygon points="0,${h} ${pts} ${w},${h}" fill="${color}" fill-opacity=".12"/><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
  }
  function squarify(items, x, y, w, h) {
    const out = [], total = items.reduce((a, b) => a + b.v, 0); let rest = items.slice();
    const scale = w * h / total;
    while (rest.length) {
      const short = Math.min(w, h); let row = [], best = Infinity;
      for (const it of rest) {
        const test = row.concat(it), s = test.reduce((a, b) => a + b.v * scale, 0);
        const mx = Math.max(...test.map(t => t.v * scale)), mn = Math.min(...test.map(t => t.v * scale));
        const worst = Math.max(short * short * mx / (s * s), s * s / (short * short * mn));
        if (worst <= best) { row = test; best = worst; } else break;
      }
      const s = row.reduce((a, b) => a + b.v * scale, 0), thick = s / short; let off = 0;
      row.forEach(it => { const len = it.v * scale / thick; out.push(w >= h ? { ...it, x, y: y + off, w: thick, h: len } : { ...it, x: x + off, y, w: len, h: thick }); off += len; });
      if (w >= h) { x += thick; w -= thick; } else { y += thick; h -= thick; }
      rest = rest.slice(row.length);
    }
    return out;
  }
  function treemap(svg) {
    const W = 720, H = 470;
    const cells = squarify(tags().sort((a, b) => b.repos - a.repos).map(t => ({ t, v: t.repos })), 0, 0, W, H);
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    cells.forEach(c => {
      const t = c.t, g = el("g", { class: "tile" });
      linkify(g, themeHref(t.id), `${t.name}: ${t.repos} repositories, ${paceText(t.pace, true)}`);
      g.append(el("rect", { x: c.x, y: c.y, width: Math.max(0, c.w), height: Math.max(0, c.h), rx: 4, style: `fill:${paceColour(t.pace)}` }));
      const strong = t.pace != null && Math.abs(Math.log(t.pace) / Math.log(2)) > .5;
      const fg = strong ? (dark ? "#111" : "#fff") : css("--ink");
      const fit = (s, px) => { const m = Math.floor((c.w - 16) / px); return s.length <= m ? s : s.slice(0, Math.max(1, m - 1)) + "…"; };
      if (c.w > 40 && c.h > 26) {
        g.append(txt({ x: c.x + 8, y: c.y + 19, fill: fg }, fit(t.name, 7.4)));
        if (c.h > 48) g.append(txt({ x: c.x + 9, y: c.y + 37, class: "s", fill: fg, "fill-opacity": .85 }, fit(`${num(t.repos)} · ${paceText(t.pace)}`, 7.2)));
      }
      hover(g, `<b>${esc(t.name)}</b>${num(t.repos)} repositories · ${t.countries} countries<br>${esc(paceText(t.pace, true))} · ${STATUS[t.status] || ""}<br>${esc(t.domain_name)}`);
      svg.append(g);
    });
    return [.5, .7, .85, 1, 1.2, 1.4, 2].map(p => `<i style="background:${paceColour(p)}"></i>`).join("");
  }
  function lineChart(svg, points, opts) {
    // points: [{label, value}]
    const W = opts.w || 440, H = opts.h || 210, M = { l: 48, r: 12, t: 10, b: 24 }, n = points.length;
    const vals = points.map(p => p.value), lo0 = Math.min(...vals), hi0 = Math.max(...vals), pad = (hi0 - lo0) * .1 || 1;
    const lo = Math.max(0, lo0 - pad), hi = hi0 + pad;
    const X = i => M.l + i * (W - M.l - M.r) / Math.max(1, n - 1), Y = v => M.t + (1 - (v - lo) / (hi - lo)) * (H - M.t - M.b);
    const ax = el("g", { class: "ax" }); svg.append(ax);
    for (let k = 0; k <= 3; k++) { const v = lo + (hi - lo) * k / 3; ax.append(el("line", { x1: M.l, x2: W - M.r, y1: Y(v), y2: Y(v) })); ax.append(txt({ x: M.l - 6, y: Y(v) + 4, "text-anchor": "end" }, num(Math.round(v)))); }
    [0, Math.floor((n - 1) / 2), n - 1].forEach((i, k) => ax.append(txt({ x: X(i), y: H - 6, "text-anchor": ["start", "middle", "end"][k] }, points[i].label)));
    const pts = points.map((p, i) => `${X(i)},${Y(p.value)}`).join(" ");
    svg.append(el("polygon", { points: `${X(0)},${Y(lo)} ${pts} ${X(n - 1)},${Y(lo)}`, fill: css("--cobalt"), "fill-opacity": .1 }));
    svg.append(el("polyline", { points: pts, fill: "none", stroke: css("--cobalt"), "stroke-width": 2 }));
    svg.append(el("circle", { cx: X(n - 1), cy: Y(vals[n - 1]), r: 4, fill: css("--cobalt") }));
    const cross = el("line", { y1: M.t, y2: H - M.b, stroke: css("--faint"), "stroke-dasharray": "3 3", opacity: 0 }), cd = el("circle", { r: 4, fill: css("--cobalt"), opacity: 0 });
    const hit = el("rect", { x: M.l, y: M.t, width: W - M.l - M.r, height: H - M.t - M.b, fill: "transparent" });
    svg.append(cross, cd, hit);
    hit.addEventListener("mousemove", e => {
      const sb = svg.getBoundingClientRect(), i = Math.max(0, Math.min(n - 1, Math.round(((e.clientX - sb.left) * W / sb.width - M.l) / (W - M.l - M.r) * (n - 1))));
      cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("opacity", 1);
      cd.setAttribute("cx", X(i)); cd.setAttribute("cy", Y(vals[i])); cd.setAttribute("opacity", 1);
      showTip(opts.tip(points[i], i), e);
    });
    hit.addEventListener("mouseleave", () => { hideTip(); cross.setAttribute("opacity", 0); cd.setAttribute("opacity", 0); });
  }
  function trendLine(series, status) {
    const w = 300, h = 30, sm = series.map((v, i) => { const a = series.slice(Math.max(0, i - 2), i + 1); return a.reduce((x, y) => x + y, 0) / a.length; });
    const mx = Math.max(...sm, 1), half = Math.floor(series.length / 2), X = i => i * w / Math.max(1, sm.length - 1), Y = v => h - 3 - v / mx * (h - 6);
    const pts = sm.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`), colour = status === "cooling" ? "var(--cool)" : "var(--hot)";
    return `<svg class="trend" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><rect x="${X(half - 1)}" y="0" width="${w - X(half - 1)}" height="${h}" fill="var(--tint)"/><polyline points="${pts.slice(0, half).join(" ")}" fill="none" stroke="var(--faint)" stroke-width="1.5" vector-effect="non-scaling-stroke"/><polyline points="${pts.slice(half - 1).join(" ")}" fill="none" stroke="${colour}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
  }
  function momentumRow(t) {
    const who = t.status === "concentrated"
      ? `<span class="who warn">${esc(t.top_account)} ${t.top_account_share}%</span>`
      : `<span class="who">${num(t.accounts_window)} accounts · ${num(t.countries_window)} countries</span>`;
    return `<li><a href="${themeHref(t.id)}">${esc(t.name)}</a><span class="${t.status === "cooling" ? "cool" : "hot"}">${paceText(t.pace)}</span>${trendLine(t.trend || [], t.status)}<small>${who}${num(t.new_window)} new in 12 months, ${num(t.new_prior)} the year before</small></li>`;
  }
  function breadth(svg) {
    const W = 1000, H = 420, M = { l: 56, r: 24, t: 24, b: 40 }, ts = tags().filter(t => t.pace != null && t.status !== "too_small");
    if (!ts.length) return;
    const xl = Math.log10(.8), xh = Math.log10(Math.max(20, ...ts.map(t => t.accounts_window)) * 1.6);
    const ps = ts.map(t => t.pace), yl = Math.log10(Math.min(.5, ...ps) * .9), yh = Math.log10(Math.max(2, ...ps) * 1.1);
    const xs = v => M.l + (Math.log10(Math.max(1, v)) - xl) / (xh - xl) * (W - M.l - M.r);
    const ys = v => M.t + (1 - (Math.log10(v) - yl) / (yh - yl)) * (H - M.t - M.b);
    svg.append(el("rect", { x: xs(5), y: M.t, width: Math.max(0, W - M.r - xs(5)), height: Math.max(0, ys(1) - M.t), fill: css("--hot"), "fill-opacity": .07 }));
    const ax = el("g", { class: "ax" }); svg.append(ax);
    [1, 2, 5, 10, 20, 50, 100, 200, 500].filter(v => Math.log10(v) >= xl && Math.log10(v) <= xh).forEach(v => {
      ax.append(el("line", { x1: xs(v), x2: xs(v), y1: M.t, y2: H - M.b })); ax.append(txt({ x: xs(v), y: H - M.b + 16, "text-anchor": "middle" }, num(v)));
    });
    [.25, .5, 1, 2, 4].filter(v => Math.log10(v) >= yl && Math.log10(v) <= yh).forEach(v => {
      ax.append(el("line", { x1: M.l, x2: W - M.r, y1: ys(v), y2: ys(v), class: v === 1 ? "zero" : "" }));
      ax.append(txt({ x: M.l - 8, y: ys(v) + 4, "text-anchor": "end" }, "×" + v));
    });
    ax.append(txt({ x: W - M.r, y: H - 4, "text-anchor": "end" }, "accounts creating new repositories in the last 12 months (log scale)"));
    ax.append(txt({ x: M.l - 8, y: M.t - 10, "text-anchor": "end" }, "pace"));
    svg.append(txt({ x: W - M.r - 10, y: M.t + 16, class: "quad hot", "text-anchor": "end" }, "Spreading"));
    svg.append(txt({ x: M.l + 10, y: M.t + 16, class: "quad" }, "One team busy"));
    svg.append(txt({ x: W - M.r - 10, y: H - M.b - 10, class: "quad", "text-anchor": "end" }, "Cooling"));
    // Label emerging, one-team and cooling themes first, then the largest, skipping any label that would collide.
    // A label goes right of its dot, else left, else nudged below; skipped if every spot collides.
    const placed = [], labelled = new Map();
    risers().concat(oneTeam(), cooling().slice(0, 4), ts.slice().sort((a, b) => b.new_window - a.new_window).slice(0, 5)).forEach(t => {
      if (!ts.includes(t) || labelled.has(t.id)) return;
      const x = xs(t.accounts_window), y = ys(t.pace), w = t.name.length * 6.6 + 16;
      const free = (box, ly) => box[0] >= M.l && box[1] <= W - M.r && !placed.some(p => Math.abs(p.y - ly) < 15 && p.box[0] < box[1] && box[0] < p.box[1]);
      const spot = [[1, 0], [-1, 0], [1, 14], [-1, 14]].find(([dir, dy]) => free(dir > 0 ? [x, x + w] : [x - w, x], y + dy));
      if (!spot) return;
      placed.push({ y: y + spot[1], box: spot[0] > 0 ? [x, x + w] : [x - w, x] }); labelled.set(t.id, spot);
    });
    ts.slice().sort((a, b) => b.new_window - a.new_window).forEach(t => {
      const x = xs(t.accounts_window), y = ys(t.pace), rad = 3 + Math.sqrt(t.new_window) * .55;
      const g = el("g", { class: "dot " + t.status });
      linkify(g, themeHref(t.id), t.name);
      g.append(el("circle", { cx: x, cy: y, r: rad }));
      if (labelled.has(t.id)) {
        const [dir, dy] = labelled.get(t.id), ly = y + dy + 4;
        g.append(txt(dir > 0 ? { x: x + rad + 4, y: ly } : { x: x - rad - 4, y: ly, "text-anchor": "end" }, t.name));
      }
      hover(g, `<b>${esc(t.name)}</b>${esc(STATUS[t.status])} · ${esc(paceText(t.pace, true))}<br>${num(t.new_window)} new in 12 months, ${num(t.new_prior)} the year before<br>${num(t.accounts_window)} accounts · busiest ${esc(t.top_account || "–")} ${t.top_account_share}%`);
      svg.append(g);
    });
  }
  function columns(svg, months) {
    const W = 600, H = 230, M = { l: 34, r: 8, t: 10, b: 24 }, n = months.length;
    const vals = months.map(m => m.new), top = Math.max(2, Math.ceil(Math.max(...vals) / 2) * 2), bw = (W - M.l - M.r) / n;
    const Y = v => M.t + (1 - v / top) * (H - M.t - M.b);
    const ax = el("g", { class: "ax" }); svg.append(ax);
    [0, top / 2, top].forEach(v => { ax.append(el("line", { x1: M.l, x2: W - M.r, y1: Y(v), y2: Y(v) })); ax.append(txt({ x: M.l - 6, y: Y(v) + 4, "text-anchor": "end" }, Math.round(v))); });
    [0, Math.floor(n / 2), n - 1].forEach((i, k) => ax.append(txt({ x: M.l + i * bw + bw / 2, y: H - 6, "text-anchor": ["start", "middle", "end"][k] }, monthLabel(months[i].month))));
    months.forEach((m, i) => {
      const last = i === n - 1;
      const r = el("rect", { x: M.l + i * bw + 1, y: Y(m.new), width: Math.max(1, bw - 2), height: Y(0) - Y(m.new), rx: 2, fill: css(last ? "--hot" : "--cobalt"), "fill-opacity": last ? 1 : .75 });
      hover(r, `<b>${num(m.new)} new repositories</b>${monthLabel(m.month)}${last ? " (so far)" : ""}`);
      svg.append(r);
    });
    const avg = vals.map((v, i) => { const w = vals.slice(Math.max(0, i - 2), i + 1); return w.reduce((a, b) => a + b, 0) / w.length; });
    svg.append(el("polyline", { points: avg.map((v, i) => `${M.l + i * bw + bw / 2},${Y(v)}`).join(" "), fill: "none", stroke: css("--ink"), "stroke-width": 2, "pointer-events": "none" }));
  }
  function adoption(svg, adopters, hot) {
    if (!adopters.length) return;
    const t0 = new Date(adopters[0].first_repo).getTime(), t1 = Date.now(), rh = 20, X = t => 170 + (t - t0) / Math.max(1, t1 - t0) * (585 - 170);
    const colour = css(hot ? "--hot" : "--cobalt");
    adopters.forEach((a, i) => {
      const yy = 18 + i * rh, x = X(new Date(a.first_repo).getTime());
      svg.append(el("line", { x1: x, x2: X(t1), y1: yy, y2: yy, stroke: colour, "stroke-opacity": .3, "stroke-width": 3 }));
      const c = el("circle", { cx: x, cy: yy, r: 5, fill: colour }); hover(c, `<b>${esc(a.owner)}</b>${esc(a.country)} · first repository ${date(a.first_repo)}`); svg.append(c);
      svg.append(txt({ x: 160, y: yy + 4, "text-anchor": "end", "font-size": 12.5, fill: css("--ink") }, a.owner.length > 22 ? a.owner.slice(0, 21) + "…" : a.owner));
    });
    const y = 30 + adopters.length * rh;
    svg.append(txt({ x: 170, y, "font-size": 11.5, fill: css("--muted") }, date(adopters[0].first_repo)));
    svg.append(txt({ x: 585, y, "text-anchor": "end", "font-size": 11.5, fill: css("--muted") }, "Now"));
    svg.setAttribute("viewBox", `0 0 600 ${y + 8}`);
  }
  function history(svg, hist, created) {
    if (hist.length < 2) { svg.outerHTML = `<p class="empty">Not enough weekly snapshots yet. History builds up from each scrape${created ? `; the repository was created ${date(created)}` : ""}.</p>`; return; }
    const n = hist.length, W = 600;
    const panel = (key, y0, label, color) => {
      const vals = hist.map(h => h[key]), h = 80, mx = Math.max(...vals) || 1, X = w => 40 + w * (W - 50) / (n - 1), Y = v => y0 + h - v / mx * h;
      svg.append(el("line", { x1: 40, x2: W - 10, y1: y0 + h, y2: y0 + h, stroke: css("--line") }));
      svg.append(txt({ x: 0, y: y0 + 12, "font-size": 12, "font-weight": 700, fill: css("--ink") }, label));
      svg.append(txt({ x: W - 10, y: y0 + 12, "text-anchor": "end", "font-size": 12, fill: css("--muted") }, num(vals[n - 1])));
      const pts = vals.map((v, w) => `${X(w)},${Y(v)}`).join(" ");
      svg.append(el("polygon", { points: `${X(0)},${y0 + h} ${pts} ${X(n - 1)},${y0 + h}`, fill: color, "fill-opacity": .12 }), el("polyline", { points: pts, fill: "none", stroke: color, "stroke-width": 2 }));
    };
    panel("stars", 8, "Stars", css("--cobalt")); panel("forks", 118, "Forks", css("--hot"));
    svg.append(txt({ x: 40, y: 216, "font-size": 11.5, fill: css("--muted") }, date(hist[0].date)));
    svg.append(txt({ x: 590, y: 216, "text-anchor": "end", "font-size": 11.5, fill: css("--muted") }, date(hist[n - 1].date)));
  }

  // ------------------------------------------------------------------ shared fragments
  const licensed = r => r.license && !["NOASSERTION", "other", "Other"].includes(r.license);
  const openness = r => `<div class="badges">${licensed(r) ? `<span class="b ok">${esc(r.license)}</span>` : '<span class="b no">No licence</span>'}${r.archived ? '<span class="b">Archived</span>' : ""}${r.fork ? '<span class="b">Fork</span>' : ""}</div>`;
  function repoTable(items, emptyText) {
    if (!items.length) return `<p class="empty">${esc(emptyText)}</p>`;
    return `<div class="card tbl"><table class="list"><thead><tr><th>Repository</th><th>Government</th><th class="r">Stars</th><th class="r">Last push</th><th>Openness</th></tr></thead><tbody>${items.map(r => {
      const dom = r.domain && themeById(r.domain);
      return `<tr><td><a href="${repoHref(r)}">${esc(r.full_name)}</a><small>${esc(r.description || "No description")}</small></td><td><a class="plain" href="${accountHref(ownerOf(r))}">${esc(ownerOf(r))}</a><small>${esc(r.country)}</small>${dom ? `<small><a class="plain" href="${themeHref(dom.id)}">${esc(dom.name)}</a></small>` : ""}</td><td class="r">${num(r.stars)}</td><td class="r">${ago(r.pushed_at)}</td><td>${openness(r)}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  }
  // A collapsible table version of a chart, for keyboard and screen-reader users.
  function chartTable(caption, head, rows) {
    return `<details class="astable"><summary>Show as a table</summary><div class="tbl"><table class="list"><caption class="vh">${esc(caption)}</caption><thead><tr>${head.map((h, i) => `<th${i ? ' class="r"' : ""}>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${r.map((c, i) => `<td${i ? ' class="r"' : ""}>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details>`;
  }
  function csvDownload(filename, rows) {
    const cell = v => { const t = Array.isArray(v) ? v.join(" ") : String(v ?? ""); return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t; };
    const cols = ["full_name", "html_url", "country", "description", "language", "stars", "forks", "license", "topics", "archived", "fork", "created_at", "pushed_at", "domain", "kind"];
    const text = [cols.join(","), ...rows.map(r => cols.map(c => cell(r[c])).join(","))].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    a.download = filename; document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  // Theme shares for a country or account, with the whole index as a marker.
  function mixRows(mix, max = 10) {
    if (!mix || !mix.labelled) return '<p class="empty">No classified repositories yet.</p>';
    const items = mix.tags.slice(0, max), top = Math.max(...items.map(t => Math.max(t.share, t.index_share)), 1);
    return `<div class="legend"><span><i></i>Here</span><span><em></em>Whole index</span></div>${items.map(t => `<div class="mixrow"><a class="lbl" href="${themeHref(t.id)}" title="${esc(t.name)}">${esc(t.name)}</a><div class="bars"><i style="width:${t.share / top * 100}%"></i><em style="left:${t.index_share / top * 100}%"></em></div><span class="${(t.ratio || 0) >= 1.5 ? "up" : ""}">${t.share.toFixed(1)}%</span></div>`).join("")}
      ${chartTable("Theme shares", ["Theme", "Share here", "Whole index"], items.map(t => [esc(t.name), t.share.toFixed(1) + "%", t.index_share.toFixed(1) + "%"]))}`;
  }
  const countRows = (items, href, label, value, small) => items.length ? `<ul class="rows">${items.map(x => { const v = value(x); return `<li>${href ? `<a href="${href(x)}">${esc(label(x))}</a>` : `<span class="lbl">${esc(label(x))}</span>`}<span>${num(v)}</span>${small ? `<small>${small(x)}</small>` : ""}<div class="bar"><i style="width:${v / Math.max(1, value(items[0])) * 100}%"></i></div></li>`; }).join("")}</ul>` : "";
  const placeHref = label => { const p = (state.places && state.places.items.find(c => c.label === label)); return countryHref(p ? p.slug : label.toLowerCase().replace(/\./g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")); };
  function copyButton(id, label) {
    return `<button class="btn ghost" type="button" data-copy="${id}">${label}</button>`;
  }
  document.addEventListener("click", e => {
    const b = e.target.closest("[data-copy]");
    if (!b) return;
    const box = document.getElementById(b.dataset.copy), text = box.value ?? box.textContent, label = b.textContent;
    const done = () => { b.textContent = "Copied"; setTimeout(() => { b.textContent = label; }, 1500); };
    const pick = () => { if (box.select) box.select(); };
    try { navigator.clipboard.writeText(text).then(done, pick); } catch (err) { pick(); }
  });

  // ------------------------------------------------------------------ sponsor slot
  const LOGO_FALLBACK = name => `<span class="spmark" aria-hidden="true">${esc(name.slice(0, 1))}</span>`;
  function sponsorMark(sp) {
    return `<a class="lg" href="${esc(sp.url)}" rel="noopener sponsored">${sp.logo ? `<img src="${esc(sp.logo)}" alt="" width="26" height="26">` : LOGO_FALLBACK(sp.name)}<span>${esc(sp.name)}</span></a>`;
  }
  const mailto = subject => `mailto:${CONTACT}?subject=${enc(subject)}`;
  function renderSponsor() {
    const strip = document.getElementById("sponsor");
    if (!strip) return;
    const sp = CONFIG.sponsor;
    strip.innerHTML = `<div class="in">${sp ? `<div class="spon">The Open GovTech Index is proudly supported by ${sponsorMark(sp)}</div><div class="spon"><a href="#/about" data-scroll="supporters">Why we have sponsors</a></div>` : `<div class="spon">The Open GovTech Index is free and independent. Interested in sponsoring it? <a href="${mailto("Sponsoring the Open GovTech Index")}">Get in touch</a> at <span class="mono">${esc(CONTACT)}</span></div>`}</div>`;
    strip.hidden = false;
  }

  const watched = new Set(); try { JSON.parse(localStorage.getItem("ogi-watch") || "[]").forEach(k => watched.add(k)); } catch (e) { /* storage blocked */ }
  const saveWatched = () => { try { localStorage.setItem("ogi-watch", JSON.stringify([...watched])); } catch (e) { /* storage blocked */ } };

  // ------------------------------------------------------------------ pages
  function pageOverview() {
    const o = state.overview, t = o.totals, ts = tags();
    const em = risers(), lead = em[0], watch = em.concat(oneTeam()).slice(0, 6);
    const sp = CONFIG.sponsor;
    const tl = o.timeline, yearAgo = tl[tl.length - 13] || tl[0], growth = overallGrowth();
    const asOf = o.as_of ? date(o.as_of) : "unknown";
    const tracked = [...watched].map(themeById).filter(Boolean);
    app.innerHTML = `
    <section class="hero pad"><div class="in">
      <div class="top">
        <div><h1>The state of government open source, every week</h1>
        <p>Every public repository from ${num(t.accounts)} government accounts, sorted into themes so you can see what is being built, what is growing, and who to learn from.</p></div>
        <div class="asof">Scraped ${esc(asOf)}<br>${num(t.repos)} repositories · ${num(t.countries)} countries and labels</div>
      </div>
      <div class="kpis">
        <div class="kpi"><div class="k">Maintained repositories</div><div class="v">${num(t.maintained)}</div><div class="d up">+${num(t.new_7d)} new this week</div>${spark(tl.slice(-24).map(m => m.total), css("--cobalt"))}</div>
        <div class="kpi"><div class="k">Government accounts</div><div class="v">${num(t.accounts)}</div><div class="d up">${num(t.countries)} countries and labels</div></div>
        <div class="kpi"><div class="k">Leading emerging theme</div><div class="v kname">${lead ? `<a href="${themeHref(lead.id)}">${esc(lead.name)}</a>` : "None right now"}</div><div class="d hot">${lead ? `${esc(paceText(lead.pace, true))} · ${em.length} emerging` : "No theme is clearly picking up"}</div></div>
        <div class="kpi"><div class="k">Licensed for reuse</div><div class="v">${t.licensed_share == null ? "–" : Math.round(t.licensed_share) + "%"}</div><div class="d">of maintained repositories</div></div>
      </div>
    </div></section>
    ${sp ? `<div class="pad"><div class="in herocredit"><div class="spon">Supported by ${sponsorMark(sp)}</div></div></div>` : ""}

    <section class="sec pad"><div class="in">
      <h2>Everything in the index</h2>
      <p class="sub">Each block is a theme, sized by original repositories and coloured by pace: how fast new repositories are arriving compared with the index as a whole. Select one to open its page.</p>
      <div class="ov">
        <div class="card"><svg class="fill" id="tm" viewBox="0 0 720 470" role="img" aria-label="Treemap of themes"></svg>
          <div class="dlegend"><span>Slowing</span><span class="bar" id="dbar"></span><span>Picking up</span><span style="margin-left:auto">Area = original repositories</span></div>
          ${chartTable("Themes by size and pace", ["Theme", "Repositories", "Pace against the index", "Countries"], ts.slice().sort((a, b) => b.repos - a.repos).map(t => [`<a href="${themeHref(t.id)}">${esc(t.name)}</a>`, num(t.repos), paceText(t.pace), num(t.countries)]))}</div>
        <div class="stack">
          <div class="card"><h3>Original repositories over time</h3><p class="cap">By creation date, last three years. ${pct(growth, 1)} in 12 months.</p><svg class="fill" id="tot" viewBox="0 0 440 210" role="img" aria-label="Line chart of original repositories over three years"></svg></div>
          <div class="card"><h3>Where things stand</h3><p class="cap">Original repositories by status.</p><ul class="rows" id="flow"></ul></div>
        </div>
      </div>
    </div></section>

    <section class="sec pad"><div class="in">
      <h2>Momentum</h2>
      <p class="sub">Which themes are picking up and which are slowing. Pace compares new repositories in the last 12 complete months with the 12 before, against every classified repository (${indexPaceText()}). Trend lines show new repositories a month over two years, with the last 12 months shaded.</p>
      <div class="dig mom">
        <div class="card"><h3>Emerging</h3><p class="cap">At least 20% faster than the index with confidence, spread across five or more accounts.</p><ul class="rows">${em.slice(0, 6).map(momentumRow).join("") || "<li><small>No theme is clearly picking up right now.</small></li>"}</ul></div>
        <div class="card"><h3>Cooling</h3><p class="cap">Clearly slower than the index, with confidence.</p><ul class="rows">${cooling().slice(0, 6).map(momentumRow).join("") || "<li><small>No theme is clearly slowing.</small></li>"}</ul></div>
        <div class="card"><h3>One team busy</h3><p class="cap">Rising, but most of the new work comes from one account. Worth a look, not yet a trend.</p><ul class="rows">${oneTeam().slice(0, 6).map(momentumRow).join("") || "<li><small>No single-team spikes right now.</small></li>"}</ul></div>
      </div>
      <div class="card" style="margin-top:16px"><h3>Spreading, or one team?</h3><p class="cap">Across is how many accounts created new repositories in the theme in the last 12 months; up is pace. Circle size is new repositories. The shaded area is where emerging themes sit.</p>
        <div class="wide"><svg class="fill" id="mm" viewBox="0 0 1000 420" role="img" aria-label="Scatter chart of themes by breadth and pace"></svg></div>
        ${chartTable("Theme momentum", ["Theme", "Status", "Pace against the index", "80% interval", "New, last 12 months", "New, 12 months before", "Accounts", "Busiest account"], tags().filter(t => t.pace != null).sort((a, b) => b.pace - a.pace).map(t => [`<a href="${themeHref(t.id)}">${esc(t.name)}</a>`, STATUS[t.status] || "", paceText(t.pace), `×${t.pace_low.toFixed(2)} to ×${t.pace_high.toFixed(2)}`, num(t.new_window), num(t.new_prior), num(t.accounts_window), t.top_account ? `${esc(t.top_account)} ${t.top_account_share}%` : "–"]))}</div>
    </div></section>

    <section class="sec pad"><div class="in">
      <h2>Dig in</h2>
      <p class="sub">Starting points for a closer look.</p>
      <div class="dig">
        <div class="card"><h3>${tracked.length ? "Themes you track" : "Picking up"}</h3><p class="cap">${tracked.length ? "Saved in this browser." : "Emerging themes, then single-team rises."}</p><ul class="rows">${(tracked.length ? tracked : watch).map(x => `<li><a href="${themeHref(x.id)}">${esc(x.name)}</a><span>${paceText(x.pace)}</span><small>${num(x.repos)} repositories · ${x.countries} countries${x.status === "emerging" || x.status === "cooling" ? ` · <span class="sig">${STATUS[x.status]}</span>` : ""}</small></li>`).join("") || "<li><small>No theme is clearly picking up right now.</small></li>"}</ul><a class="more" href="#/themes">All themes →</a></div>
        <div class="card"><h3>Countries</h3><p class="cap">Maintained repositories, with share licensed.</p><ul class="rows">${o.countries.map(c => `<li><a href="${placeHref(c.country)}">${esc(c.country)}</a><span>${num(c.maintained)} · ${Math.round(c.licensed_share)}%</span><div class="bar"><i style="width:${c.maintained / Math.max(1, o.countries[0].maintained) * 100}%"></i></div></li>`).join("")}</ul><a class="more" href="#/countries">All countries →</a></div>
        <div class="card"><h3>Newest</h3><p class="cap">Recently created, with a description and real content.</p><ul class="rows">${o.newest.map(r => `<li><a href="${repoHref(r)}"><code>${esc(r.full_name)}</code></a><span>${ago(r.created_at)}</span><small>${esc(r.country)}${r.domain && themeById(r.domain) ? " · " + esc(themeById(r.domain).name) : ""}</small></li>`).join("")}</ul><a class="more" href="#/week">This week →</a></div>
      </div>
      <nav class="ways" aria-label="More ways into the index">
        <a href="#/week"><b>This week</b><span>New repositories and themes on the move</span></a>
        <a href="#/reuse"><b>Reuse</b><span>Who forks whose code</span></a>
        <a href="#/building-blocks"><b>Building blocks</b><span>Components worth adopting</span></a>
        <a href="#/compare"><b>Compare</b><span>Two countries or themes side by side</span></a>
        <a href="#/practice"><b>Open-source practice</b><span>Licences, descriptions and topics</span></a>
      </nav>
    </div></section>`;
    document.getElementById("dbar").innerHTML = treemap(document.getElementById("tm"));
    lineChart(document.getElementById("tot"), tl.map(m => ({ label: monthLabel(m.month), value: m.total, n: m.new })), { tip: p => `<b>${num(p.value)} repositories</b>${p.label} · ${num(p.n)} created` });
    const s = o.status, F = [["Created in the last 12 months", s.new_12m, "var(--ok)"], ["Active (pushed in the last 12 months)", s.active, "var(--cobalt)"], ["Dormant (no push in 12 months)", s.dormant, "var(--cool)"], ["Archived", s.archived, "var(--faint)"]];
    const fmax = Math.max(...F.map(f => f[1]), 1);
    document.getElementById("flow").innerHTML = F.map(f => `<li><span class="lbl">${f[0]}</span><span>${num(f[1])}</span><div class="bar"><i style="width:${f[1] / fmax * 100}%;background:${f[2]}"></i></div></li>`).join("") +
      `<li><small>${num(tl[tl.length - 1].total - yearAgo.total)} more original repositories than a year ago.</small></li>`;
    breadth(document.getElementById("mm"));
  }

  function pageThemes() {
    const ds = domains(), all = tags();
    app.innerHTML = `
    <section class="phead pad"><div class="in"><div class="crumb"><a href="#/">Overview</a> / Themes</div>
      <h1>Themes</h1><p>The ten UN COFOG functions of government, plus cross-government capabilities, each split into tags. A repository has one domain and any number of tags.</p></div></section>
    <section class="sec pad"><div class="in"><div class="domgrid">${ds.map(d => {
      const kids = all.filter(t => t.domain === d.id).sort((a, b) => b.repos - a.repos), mx = Math.max(1, ...kids.map(k => k.repos));
      return `<div class="card"><h3><a href="${themeHref(d.id)}">${esc(d.name)}</a></h3><p class="cap">${num(d.repos)} repositories · ${esc(paceText(d.pace, true))}</p><ul class="rows">${kids.map(k => `<li><a href="${themeHref(k.id)}">${esc(k.name)}</a><span>${num(k.repos)} · ${paceText(k.pace)}</span>${k.status === "emerging" || k.status === "cooling" ? `<small><span class="sig">${STATUS[k.status]}</span></small>` : ""}<div class="bar"><i style="width:${k.repos / mx * 100}%"></i></div></li>`).join("") || '<li><small>No tagged repositories yet.</small></li>'}</ul></div>`;
    }).join("")}</div></div></section>`;
  }

  async function pageTheme(id) {
    const t = await data.theme(id);
    if (!t) { app.innerHTML = notFound("That theme is not in the taxonomy."); return; }
    const m = themeById(id) || t, em = t.type === "tag" && isEmerging(m);
    const filter = `${t.type === "domain" ? "domain" : "tag"}=${enc(id)}`;
    const kids = t.type === "domain" ? tags().filter(x => x.domain === id).sort((a, b) => b.repos - a.repos) : [];
    app.innerHTML = `
    <section class="phead pad"><div class="in">
      <div class="crumb"><a href="#/">Overview</a> / <a href="#/themes">Themes</a>${t.type === "tag" ? ` / <a href="${themeHref(t.domain)}">${esc(t.domain_name)}</a>` : ""}</div>
      <div class="row">
        <div><div class="eyebrow">${em ? "Emerging theme" : t.type === "domain" ? "Function of government" : "Theme"}</div><h1>${esc(t.name)}</h1><p>${esc(t.definition)}</p></div>
        <div class="acts"><button class="btn" id="track" type="button"></button><a class="btn ghost" href="#/repos?${filter}">Browse all</a><a class="btn ghost" href="${esc(API)}/v1/themes/${enc(id)}">JSON</a></div>
      </div>
      <div class="stats">
        <div class="stat"><b>${num(t.repos)}</b><small>original repositories</small></div>
        <div class="stat"><b>${paceText(m.pace)}</b><small>pace against the index</small></div>
        <div class="stat"><b>${num(t.countries)}</b><small>countries and labels</small></div>
        ${m.status ? `<div class="stat"><b>${STATUS[m.status]}</b><small>${m.status === "concentrated" ? `${esc(m.top_account)} made ${m.top_account_share}% of new work` : `${num(m.accounts_window)} accounts made new work this year`}</small></div>` : ""}
        <div class="stat"><b>${t.licensed_share == null ? "–" : Math.round(t.licensed_share) + "%"}</b><small>licensed</small></div>
      </div>
    </div></section>
    ${t.repos ? `
    <section class="sec pad"><div class="in"><div class="two">
      <div class="card"><h3>New repositories per month</h3><p class="cap">Last 24 months, by creation date. Line shows the 3-month average.</p><svg class="fill" id="cols" viewBox="0 0 600 230" role="img" aria-label="New repositories per month"></svg>
        ${chartTable("New repositories per month", ["Month", "New"], t.monthly_new.map(m => [monthLabel(m.month), num(m.new)]))}</div>
      <div class="card"><h3>Where it is happening</h3><p class="cap">Original repositories by country or registry label.</p><ul class="rows">${t.by_country.map(c => `<li><a href="#/repos?${filter}&country=${enc(c.country)}">${esc(c.country)}</a><span>${num(c.repos)}</span><div class="bar"><i style="width:${c.repos / Math.max(1, t.by_country[0].repos) * 100}%"></i></div></li>`).join("")}</ul></div>
    </div></div></section>
    <section class="sec pad"><div class="in"><div class="two">
      <div class="card"><h3>Who picked it up, and when</h3><p class="cap">First repository on this theme from the most recent ${t.adopters.length} accounts to adopt it.</p><svg class="fill" id="adopt" viewBox="0 0 600 300" role="img" aria-label="Adoption timeline"></svg>
        ${chartTable("Adopters", ["Account", "Country", "First repository"], t.adopters.map(a => [`<a href="${accountHref(a.owner)}">${esc(a.owner)}</a>`, esc(a.country), date(a.first_repo)]))}</div>
      <div class="card"><h3>How repositories get this theme</h3><p class="cap">A decision model reads each repository's name, description, topics and language, and scores it against this definition. No text is generated.</p>
        <ul class="why"><li><b>Definition the model reads</b>${esc(t.definition)}</li><li><b>Rule</b>${t.type === "domain" ? "The most likely of the 11 domains." : "Probability of at least 0.5."} Model <code>${esc(t.model || state.themes.model || "not run yet")}</code>, taxonomy v${state.themes.taxonomy_version}.</li></ul>
        ${kids.length ? `<h3 style="margin-top:18px">Tags in this domain</h3><p class="cap">Repositories per tag.</p><div class="chips">${kids.map(k => `<a href="${themeHref(k.id)}">${esc(k.name)}<span>${num(k.repos)}</span></a>`).join("")}</div>` : ""}
        ${t.related && t.related.length ? `<h3 style="margin-top:18px">Often appears with</h3><p class="cap">Share of this theme's repositories that also carry the tag.</p><div class="chips">${t.related.map(r => `<a href="${themeHref(r.id)}">${esc(r.name)}<span>${Math.round(r.share)}%</span></a>`).join("")}</div>` : ""}
      </div>
    </div></div></section>
    ${t.teams && t.teams.length ? `<section class="sec pad"><div class="in"><div class="two">
      <div class="card"><h3>Teams to talk to</h3><p class="cap">Accounts with the most maintained repositories on this ${t.type === "domain" ? "domain" : "theme"}.</p>${countRows(t.teams, x => accountHref(x.owner), x => x.owner, x => x.maintained, x => `${esc(x.country)} · ${num(x.repos)} repositories in total`)}</div>
      <div class="card"><h3>Follow this ${t.type === "domain" ? "domain" : "theme"}</h3><p class="cap">New repositories with a description and real content, as an RSS feed you can read anywhere.</p>
        <div class="feedbox"><input id="feedurl" readonly value="${esc((API || location.origin) + "/feeds/theme/" + enc(id) + ".xml")}" aria-label="Feed address">${copyButton("feedurl", "Copy feed")}</div>
        <p class="cap" style="margin-top:12px">Or <a href="#/compare?a=theme:${enc(id)}">compare it with another theme</a>.</p></div>
    </div></div></section>` : ""}
    <section class="sec pad"><div class="in">
      ${state.sampleThemes ? '<p class="sub"><strong>Example repositories</strong>: these theme lists are sample data. Repository links open live details when the API is available.</p>' : ""}
      <h2>Most starred</h2><p class="sub">Original repositories with this ${t.type === "domain" ? "domain" : "tag"}. <a href="#/repos?${filter}">Browse all ${num(t.repos)}</a>.</p>
      ${repoTable(t.top_repos, "No repositories yet.")}
      <h2 style="margin-top:32px">Newest</h2><p class="sub">The most recently created.</p>
      ${repoTable(t.newest_repos, "No repositories yet.")}
    </div></section>` : `<section class="sec pad"><div class="in"><p class="empty">No repositories carry this ${t.type} yet.</p></div></section>`}`;
    const btn = document.getElementById("track");
    const upd = () => { btn.textContent = watched.has(id) ? "Tracking" : "Track this theme"; btn.setAttribute("aria-pressed", watched.has(id)); };
    btn.onclick = () => { watched.has(id) ? watched.delete(id) : watched.add(id); saveWatched(); upd(); }; upd();
    if (t.repos) { columns(document.getElementById("cols"), t.monthly_new); adoption(document.getElementById("adopt"), t.adopters, em); }
  }

  async function pageRepos(params) {
    const p = new URLSearchParams(params);
    if (!p.has("include_forks")) p.set("include_forks", "false");
    if (!p.has("include_archived")) p.set("include_archived", "false");
    if (!p.has("maintained")) p.set("maintained", "true");
    p.set("limit", "25");
    const [res, countries] = await Promise.all([data.repos(p), data.countries().catch(() => [])]);
    const off = +p.get("offset") || 0, total = res.total;
    const classificationDisabled = state.live && state.sampleThemes ? " disabled" : "";
    const opt = (v, label, cur) => `<option value="${esc(v)}"${v === (cur || "") ? " selected" : ""}>${esc(label)}</option>`;
    app.innerHTML = `
    <section class="phead pad"><div class="in"><div class="crumb"><a href="#/">Overview</a> / Repositories</div>
      <h1>Repositories</h1><p>${res.unavailable ? "Classification filters are unavailable until a classification run is published. Clear these filters to search live repositories." : `${num(total)} match. ${p.get("maintained") === "true" ? "Maintained" : "Unarchived"} originals unless you include the rest.`}</p></div></section>
    <section class="sec pad"><div class="in">
      <form class="filters" id="ff">
        <label>Search<input name="q" value="${esc(p.get("q") || "")}" placeholder="Name, description or topic"></label>
        <label>Country<select name="country">${opt("", "Any")}${countries.map(c => opt(c.country, c.country, p.get("country"))).join("")}</select></label>
        <label>Domain<select name="domain"${classificationDisabled}>${opt("", "Any")}${domains().map(d => opt(d.id, d.name, p.get("domain"))).join("")}</select></label>
        <label>Tag<select name="tag"${classificationDisabled}>${opt("", "Any")}${tags().slice().sort((a, b) => a.name.localeCompare(b.name)).map(t => opt(t.id, t.name, p.get("tag"))).join("")}</select></label>
        <label>Language<input name="language" value="${esc(p.get("language") || "")}" placeholder="Any" size="10"></label>
        <label>Sort<select name="sort">${[["stars", "Most stars"], ["recently_pushed", "Recently pushed"], ["newest", "Newest"], ["name", "Name"]].map(([v, l]) => opt(v, l, p.get("sort") || "stars")).join("")}</select></label>
        <label class="chk"><input type="checkbox" name="maintained" value="true"${p.get("maintained") === "true" ? " checked" : ""}> Maintained only</label>
        <label class="chk"><input type="checkbox" name="include_forks" value="true"${p.get("include_forks") === "true" ? " checked" : ""}> Forks</label>
        <label class="chk"><input type="checkbox" name="include_archived" value="true"${p.get("include_archived") === "true" ? " checked" : ""}> Archived</label>
        <button class="btn" type="submit">Apply</button>
      </form>
      ${res.unavailable ? '<p class="empty">Apply the available filters above to search live repositories.</p>' : repoTable(res.items, "Nothing matches these filters.")}
      <div class="pager">${off > 0 ? `<a class="btn ghost" href="#/repos?${pageParams(p, off - 25)}">Previous</a>` : ""}<span>${total ? `${num(off + 1)}–${num(Math.min(total, off + 25))} of ${num(total)}` : ""}</span>${off + 25 < total ? `<a class="btn ghost" href="#/repos?${pageParams(p, off + 25)}">Next</a>` : ""}
        ${total && state.live ? `<button class="btn ghost" type="button" id="csv" style="margin-left:auto">Download CSV${total > CSV_MAX ? ` (first ${num(CSV_MAX)})` : ""}</button>` : ""}</div>
    </div></section>`;
    document.getElementById("ff").addEventListener("submit", e => {
      e.preventDefault();
      const q = new URLSearchParams();
      for (const [k, v] of new FormData(e.target)) if (v) q.set(k, v);
      if (!q.has("maintained")) q.set("maintained", "false");
      nav("#/repos?" + q);
    });
    const csv = document.getElementById("csv");
    if (csv) csv.addEventListener("click", async () => {
      csv.disabled = true; csv.textContent = "Preparing…";
      const rows = [];
      try {
        for (let o = 0; o < Math.min(total, CSV_MAX); o += 100) {
          const q = new URLSearchParams(p); q.set("limit", "100"); q.set("offset", o);
          rows.push(...(await data.repos(q)).items);
        }
        csvDownload("govtech-index-repositories.csv", rows);
        csv.textContent = "Downloaded";
      } catch (err) { csv.textContent = "Download failed, try again"; }
      csv.disabled = false;
    });
  }
  const CSV_MAX = 1000;
  function pageParams(p, offset) { const q = new URLSearchParams(p); q.delete("limit"); q.set("offset", Math.max(0, offset)); return q; }

  async function pageRepo(owner, name) {
    const [r, ctx] = await Promise.all([data.repo(owner, name), data.context(owner, name)]);
    if (!r) { app.innerHTML = notFound("That repository is not in the index."); return; }
    const cls = r.classification, dom = cls && themeById(cls.domain);
    const domainLink = dom ? `<a href="${themeHref(dom.id)}">${esc(dom.name)}</a>` : "Not classified yet";
    app.innerHTML = `
    <section class="phead pad"><div class="in">
      <div class="crumb"><a href="#/">Overview</a> / ${dom ? `<a href="${themeHref(dom.id)}">${esc(dom.name)}</a>` : `<a href="#/repos">Repositories</a>`} / Repository</div>
      <div class="row">
        <div><h1 style="font-family:var(--mono);font-weight:600;font-size:clamp(22px,3.4vw,36px);letter-spacing:-.01em;overflow-wrap:anywhere">${esc(r.full_name)}</h1><p>${esc((r.description || "No description").trim().replace(/([^.!?])$/, "$1."))} Published by <a href="${accountHref(ownerOf(r))}">${esc(ownerOf(r))}</a>, <a href="${placeHref(r.country)}">${esc(r.country)}</a>.</p></div>
        <div class="acts"><a class="btn" href="${esc(r.html_url)}" rel="noopener">View on ${esc(hostName(r.html_url))}</a></div>
      </div>
      <div class="badges" style="margin-top:14px">${dom ? `<a class="b hot" href="${themeHref(dom.id)}">${esc(dom.name)}</a>` : ""}${(cls ? cls.tags : []).map(t => `<a class="b co" href="${themeHref(t.id)}">${esc((themeById(t.id) || { name: t.id }).name)}</a>`).join("")}${licensed(r) ? `<span class="b ok">${esc(r.license)}</span>` : '<span class="b no">No licence</span>'}${r.fork ? `<span class="b">Fork${r.fork_source ? " of " + esc(r.fork_source) : ""}</span>` : '<span class="b">Original, not a fork</span>'}${r.archived ? '<span class="b no">Archived</span>' : ""}${cls && cls.kind ? `<span class="b">${esc(cls.kind)}</span>` : ""}</div>
      <div class="stats">${[[num(r.stars), "stars"], [num(r.forks), "forks"], [ctx ? ctx.reused_by.length : "Unavailable", "government forks"], [ago(r.pushed_at), "last push"], [date(r.created_at), "created"], [r.language || "–", "main language"]].map(s => `<div class="stat"><b style="${String(s[0]).length > 9 ? "font-size:18px" : ""}">${esc(s[0])}</b><small>${s[1]}</small></div>`).join("")}</div>
    </div></section>
    <section class="sec pad"><div class="in"><div class="two">
      <div class="card"><h3>Stars and forks over time</h3><p class="cap">From weekly snapshots since the repository first appeared in the index.</p>${ctx ? '<svg class="fill" id="hist" viewBox="0 0 600 220" role="img" aria-label="Stars and forks over time"></svg>' : '<p class="empty">History unavailable. Try again later.</p>'}</div>
      <div class="card"><h3>Why it is in these themes</h3><p class="cap">${cls && cls.confidence_mode === "evidence" ? "Evidence-backed labels with literal source quotes; probabilities are not calibrated." : "The decision model's probabilities, so you can check the classification."}</p>
      ${cls ? (cls.confidence_mode === "evidence" ? `<p><b>Domain:</b> ${domainLink}${cls.kind ? ` · <b>Kind:</b> ${esc(cls.kind)}` : ""}</p>
        ${(cls.tags || []).length ? `<div class="evidence">${cls.tags.map(t => `<p><a href="${themeHref(t.id)}">${esc((themeById(t.id) || { name: t.id }).name)}</a><br><q>${esc(t.quote)}</q><br><span class="cap">${esc(t.rationale)} (${esc(t.source_field)})</span></p>`).join("")}</div>` : `<p class="empty">No taxonomy tags were assigned.</p>`}
        <p class="cap">Missing tags are unknown, not verified negatives. A labelled record has at least one domain, kind or tag, not necessarily a complete classification.</p>
        ${cls.abstentions ? `<p class="cap"><b>Field abstentions:</b> ${esc(JSON.stringify(cls.abstentions))}</p>` : ""}
        <p class="cap" style="margin-top:12px">Model <code>${esc(cls.model)}</code>. AI-assisted labels are not human-certified; literal quotes do not prove semantic accuracy.</p>` : `<div class="prob"><span style="text-align:left;color:var(--ink);font-family:var(--body)"><b>Domain:</b> ${domainLink}</span><span>${cls.domain_probability == null ? "" : cls.domain_probability.toFixed(2)}</span><div class="bar"><i style="width:${(cls.domain_probability || 0) * 100}%"></i></div>
        ${cls.tags.map(t => `<span style="text-align:left;color:var(--ink);font-family:var(--body)"><a href="${themeHref(t.id)}">${esc((themeById(t.id) || { name: t.id }).name)}</a></span><span>${t.probability == null ? "" : t.probability.toFixed(2)}</span><div class="bar"><i style="width:${(t.probability || 0) * 100}%"></i></div>`).join("")}</div>
        <p class="cap" style="margin-top:12px">Model <code>${esc(cls.model)}</code>. Tags apply at 0.5 or above.</p>`) : (hostOf(r.html_url) === "github.com" ? `<p class="empty">This repository has not been through the classification run yet.</p>` : `<p class="empty">Repositories from code platforms other than GitHub are counted and searchable, but not sorted into themes yet.</p>`)}
      ${r.topics && r.topics.length ? `<h3 style="margin-top:18px">Topics</h3><div class="chips">${r.topics.map(t => `<a href="#/repos?q=${enc(t)}">${esc(t)}</a>`).join("")}</div>` : ""}
      </div>
    </div></div></section>
    <section class="sec pad"><div class="in"><div class="dig">
      <div class="card"><h3>Reused by</h3><p class="cap">Government accounts in the index that forked it.</p><ul class="rows">${ctx && ctx.reused_by.length ? ctx.reused_by.map(f => `<li><a href="${repoHref(f)}">${esc(f.owner)}</a><span>${ago(f.created_at)}</span><small>${esc(f.country)}</small></li>`).join("") : ctx ? "<li><small>No government forks yet.</small></li>" : "<li><small>Government forks unavailable. Try again later.</small></li>"}</ul></div>
      <div class="card"><h3>Similar repositories</h3><p class="cap">Same theme, other accounts.</p><ul class="rows">${ctx && ctx.similar.length ? ctx.similar.map(x => `<li><a href="${repoHref(x)}"><code>${esc(x.full_name)}</code></a><span>${num(x.stars)} ★</span><small>${esc(x.country)}</small></li>`).join("") : ctx ? "<li><small>None yet.</small></li>" : "<li><small>Similar repositories unavailable. Try again later.</small></li>"}</ul></div>
      <div class="card"><h3>Details</h3><p class="cap">From the ${esc(hostName(r.html_url))} API, refreshed weekly.</p><ul class="rows"><li><span class="lbl">Open issues</span><span>${num(r.open_issues)}</span></li><li><span class="lbl">Size</span><span>${num(Math.round((r.size_kb || 0) / 1024))} MB</span></li><li><span class="lbl">Default branch</span><span>${esc(r.default_branch || "–")}</span></li><li><span class="lbl">Data</span><span><a href="${esc(API)}/v1/repos/${encPath(r.full_name)}">JSON</a></span></li></ul></div>
    </div></div></section>
    <section class="sec pad"><div class="in"><div class="two">
      <div class="card"><h3>From the README</h3>${r.readme_excerpt ? `<p class="readme">${esc(r.readme_excerpt)}</p><a class="more" href="${esc(r.html_url)}#readme" rel="noopener">Read it on ${esc(hostName(r.html_url))} →</a>` : '<p class="empty">No README text in this publication.</p>'}</div>
      <div class="card"><h3>Show it is listed</h3><p class="cap">Add this badge to the repository's README. It links back to this page.</p>
        <p><img src="${esc(API)}/badge/${encPath(r.full_name)}.svg" alt="Open GovTech Index badge" height="20"></p>
        <textarea id="badgemd" class="snippet" readonly rows="3">[![Open GovTech Index](${esc((API || location.origin) + "/badge/" + r.full_name + ".svg")})](${esc((API || location.origin) + "/repo/" + r.full_name)})</textarea>
        ${copyButton("badgemd", "Copy Markdown")}</div>
    </div></div></section>`;
    if (ctx) history(document.getElementById("hist"), ctx.history, r.created_at);
  }

  // ------------------------------------------------------------------ about (was "method")
  function pipelineFigure() {
    const steps = [
      ["Find sources", "Start from lists of government accounts and servers, plus reviewed submissions.", "government.github.com YAML · forge_hosts.yaml · submissions/pending"],
      ["Fetch", "Ask each platform for its public repositories, and skip copies of ones already found. Code itself is not copied.", "GitHub REST API · GitLab v4 and Forgejo v1 APIs, 2 requests a second per server"],
      ["Store", "Keep one row per repository in one database, with a snapshot each week.", "SQLite · repositories, repository_snapshots, cache (ETag)"],
      ["Count", "Work out originals, maintained and licensed repositories for the numbers.", "fork, archived and pushed_at filters"],
      ["Classify", "An AI model picks themes from a fixed list and must quote its evidence. Only new or changed repositories are read again.", "decision model · taxonomy v1 · quote validation · content-hash cache"],
      ["Publish", "Rebuild the site, the API and the open dataset from the same database.", "static site · FastAPI and MCP · Parquet and CSV export"],
    ];
    return `<ol class="flow" aria-label="The six steps from sources to published data">${steps.map(([name, plain, tech], i) => `<li><span class="n">${i + 1}</span><b>${name}</b><p>${plain}</p><code>${tech}</code></li>`).join("")}</ol>`;
  }

  function pageAbout() {
    const o = state.overview, t = o.totals, c = o.classification || {}, sp = CONFIG.sponsor;
    const nt = state.themes.themes, nd = nt.filter(x => x.type === "domain").length, ntag = nt.filter(x => x.type === "tag").length;
    const covered = c.current_total_repos || t.original || 0, w = n => covered ? (n || 0) / covered * 100 : 0;
    const askBox = `<div class="sponsorbox"><b>Sponsor the index</b>
      <p>Sponsorship keeps the site, API and dataset free for everyone. In return your organisation gets a small credit, with its logo, on every page and on this page. Sponsors have no say in the method, the taxonomy or what is published.</p>
      <p><a class="btn" href="${mailto("Sponsoring the Open GovTech Index")}">Get in touch</a> <span class="mono">${esc(CONTACT)}</span></p></div>`;
    app.innerHTML = `
    <section class="phead pad"><div class="in"><div class="crumb"><a href="#/">Overview</a> / About</div><h1>About the index</h1>
      <p>Why the Open GovTech Index exists, who runs it, and exactly how every number on the site is made.</p></div></section>
    <div class="pad"><div class="in about">
      <nav class="toc" aria-label="On this page">
        <span class="grp">The project</span>
        ${[["why", "Why it exists"], ["who", "Who runs it"], ["supporters", "Supporters"]].map(([id, l]) => `<a href="#/about" data-scroll="${id}">${l}</a>`).join("")}
        <span class="grp">Method</span>
        ${[["coverage", "Coverage"], ["pipeline", "How the data is made"], ["counting", "Which repositories count"], ["themes", "Themes"], ["growth", "Pace and emerging"], ["limits", "Known limits"]].map(([id, l]) => `<a href="#/about" data-scroll="${id}">${l}</a>`).join("")}
        <span class="grp">Get involved</span>
        <a href="#/about" data-scroll="corrections">Corrections and submissions</a>
      </nav>
      <article class="prose">
        <h2 id="why">Why it exists</h2>
        <p class="lede">Governments publish a remarkable amount of open code, but almost nobody can see it as a whole.</p>
        <p>There are ${num(t.repos)} public repositories across the ${num(t.accounts)} government accounts the index tracks. They are spread across hundreds of GitHub organisations and government code servers, written in dozens of languages and named in ways that only make sense inside the team that built them. A digital team in Oslo building a benefits calculator has no easy way to learn that teams in Ottawa and Leeds have already built one.</p>
        <p class="pull">The result is that the same problems get solved again and again, in parallel, with public money.</p>
        <p>The index exists to make that work findable. It sorts every repository into the functions of government it serves, shows which themes are growing and where, and points to the teams worth learning from. It is meant for people who build and buy public services: developers, architects, service owners, and the people who fund them.</p>
        <p>It is deliberately cheap to run. Nothing on the page you are reading calls an AI model, the API is free, and the full dataset is open for anyone to reuse.</p>

        <h2 id="who">Who runs it</h2>
        <p>The Open GovTech Index is an independent project. It is not affiliated with GitHub or any government.</p>
        <div class="author"><div class="avatar" aria-hidden="true">AV</div><div>
          <b>Andreas Varotsis</b>
          <p>Data scientist and public safety researcher working on how government uses data and evidence. Previously police analytics, including at the Metropolitan Police. Teaches public sector innovation at Newspeak House.</p>
          <div class="links"><a href="https://andreasthinks.me">andreasthinks.me</a><a href="https://andreasthinks.me/about.html">About Andreas</a><a href="https://andreasthinks.me/index.html">Blog</a><a href="https://github.com/AndreasThinks">GitHub</a></div>
        </div></div>
        <p>Writing about the index, including what it has found, is published on <a href="https://andreasthinks.me">andreasthinks.me</a>. The code is open source on <a href="https://github.com/AndreasThinks/open-govtech-report">GitHub</a>. To get in touch, email <a href="${mailto("Open GovTech Index")}">${esc(CONTACT)}</a>.</p>

        <h2 id="supporters">Supporters</h2>
        ${sp ? `<p>Running the index costs money: hosting, the weekly scrape and the classification of new repositories. Sponsors cover those costs so the site, API and dataset stay free.</p>
          <div class="sponsorbox"><div class="who">${sp.logo ? `<img src="${esc(sp.logo)}" alt="" width="44" height="44">` : LOGO_FALLBACK(sp.name)}<div><b>${esc(sp.name)}</b>${sp.since ? `<small>Supporting the index since ${esc(sp.since)}</small>` : ""}</div></div>
          ${sp.blurb ? `<p>${esc(sp.blurb)} <a href="${esc(sp.url)}" rel="noopener sponsored">${esc(sp.url.replace(/^https?:\/\//, "").replace(/\/$/, ""))}</a></p>` : ""}</div>
          <p class="cap">Sponsors get a credit on the site. They do not get any say in the method, the taxonomy, what is published or how repositories are ranked.</p>
          <p>Want to support the index too? <a href="${mailto("Sponsoring the Open GovTech Index")}">Get in touch</a>.</p>`
        : `<p>Running the index costs money: hosting, the weekly scrape and the classification of new repositories. It is currently funded by its author.</p>${askBox}`}

        <h2 id="coverage">Coverage</h2>
        <p>Government code does not all live on GitHub, so the index reads it from three sources.</p>
        <ul>
          <li><b>GitHub accounts</b> in the <a href="https://github.com/github/government.github.com">government.github.com registry</a>.</li>
          <li><b>Government code servers</b>, read in full: OpenCoDE (Germany), code.overheid.nl (Netherlands), code.europa.eu (European Union), ADULLACT (France), Platform One (US Department of Defense), USGS and Oak Ridge National Laboratory (US). A maintainer adds these by hand.</li>
          <li><b>Community submissions</b> of single accounts, reviewed by a maintainer. An account can be on GitHub, on a GitLab server such as gitlab.com, or on a Forgejo or Gitea server such as Codeberg. <a href="#/submit">Add an account</a>.</li>
        </ul>
        <p>Those three platforms are the ones whose public APIs list an account's repositories without a token, which keeps the weekly job free. Submissions are of whole accounts rather than single repositories, so one review covers everything the account publishes, now and later. On servers shared with the public, such as gitlab.com, only submitted accounts are read; personal accounts on government servers are left out.</p>
        <p>Some bodies publish the same code in two places. A repository that is a mirror or a migrated copy of one already in the index is skipped, and so is a repository with the same name as one in the same body's GitHub account, so nothing is counted twice. Repositories from other platforms are counted and searchable, but they are <a href="#/about" data-scroll="themes">not sorted into themes yet</a>.</p>
        <p>It is not a census of all government code: an account missing from these sources is missing here. A weekly job reads every source through its public API and keeps a snapshot of each repository.</p>
        <div class="facts">
          <div class="stat"><b>${num(t.repos)}</b><small>repositories</small></div>
          <div class="stat"><b>${num(t.accounts)}</b><small>accounts</small></div>
          <div class="stat"><b>${num(t.countries)}</b><small>registry labels</small></div>
          <div class="stat"><b>Weekly</b><small>last scrape ${esc(o.as_of ? date(o.as_of) : "unknown")}</small></div>
        </div>
        <p>Registry labels mix countries with parts of a government and international bodies. The <a href="#/countries">countries page</a> groups each label by country and level of government: national, regional, local, tribal or international.</p>

        <h2 id="pipeline">How the data is made</h2>
        <p>Each week the index runs the same six steps. The plain-English version is in each card; the technical names are underneath.</p>
        ${pipelineFigure()}

        <h2 id="counting">Which repositories count</h2>
        <dl class="def">
          <dt>Original</dt><dd>Not a fork. ${num(t.original)} repositories. Themes and growth use original repositories only, so a government forking a popular project does not inflate a theme.</dd>
          <dt>Maintained</dt><dd>Original, not archived, and pushed to in the 12 months before the latest scrape. ${num(t.maintained)} repositories.</dd>
          <dt>Licensed</dt><dd>Has an open licence its platform can detect. ${t.licensed_share == null ? "–" : t.licensed_share + "%"} of maintained repositories.</dd>
          <dt>Notable</dt><dd>Has a description, some content, and a push after the day it was created. Lists of new repositories show only these, which hides empty placeholders.</dd>
        </dl>

        <h2 id="themes">Themes</h2>
        <p>The taxonomy is fixed and reviewed. Its top level is the ten divisions of the UN Classification of the Functions of Government (COFOG), plus a domain for cross-government capabilities such as identity, payments and design systems. Each domain has tags, each with a one-line definition: ${nd} domains and ${ntag} tags in taxonomy v${esc(state.themes.taxonomy_version ?? c.taxonomy_version ?? 1)}. The full list is at <a href="${esc(API)}/v1/taxonomy">/v1/taxonomy</a>.</p>
        <p>Evidence-backed runs read bounded repository metadata and README text, then store only fixed taxonomy IDs supported by literal quotes from the source. A repository is classified once and again only when it changes, so the weekly cost stays small.</p>
        <p>For now only GitHub repositories are classified, because README text is read through GitHub's API. Repositories from other platforms are left out of the coverage figures below rather than counted as waiting.</p>
        ${c.model ? `<p>Current run: <code>${esc(c.model)}</code>, ${num(c.labelled_repos ?? c.classified_original_repos)} labelled of ${num(covered)} original repositories (${c.coverage}% coverage), taxonomy v${esc(c.taxonomy_version)}.</p>
        <div class="cov" role="img" aria-label="Classification coverage of original repositories">
          <div class="track"><i style="width:${w(c.labelled_repos)}%;background:var(--cobalt)"></i><i style="width:${w(c.abstained_repos)}%;background:var(--faint)"></i><i style="width:${w(c.pending_repos)}%;background:var(--line)"></i><i style="width:${w(c.failed_repos)}%;background:var(--hot)"></i></div>
          <div class="key"><span style="--c:var(--cobalt)">Labelled ${num(c.labelled_repos)}</span><span style="--c:var(--faint)">Abstained ${num(c.abstained_repos)}</span><span style="--c:var(--line)">Waiting ${num(c.pending_repos)}</span><span style="--c:var(--hot)">Failed ${num(c.failed_repos)}, retried weekly</span></div>
        </div>` : "<p>No classification run is published yet, so theme figures on this site are examples.</p>"}

        <h2 id="growth">Pace and emerging themes</h2>
        <p><b>Pace</b> is how fast new repositories are arriving in a theme compared with the index as a whole. It counts new original repositories in the last 12 complete months and in the 12 before, and divides that ratio by the same ratio for every classified repository (currently ${indexPaceText()}). Comparing with classified repositories only means a classification backlog, or sources that are not classified yet, slow every theme alike instead of making them all look like they are cooling. A pace of +40% means the theme sped up 40% more than government open source overall. Each pace comes with an 80% interval from the counts, so a theme that went from 3 repositories to 6 is not mistaken for a trend.</p>
        <p>A theme is <b>emerging</b> when that interval is above the index's pace, the pace is at least 20% faster, and at least 5 accounts created the new work, none of them more than half of it. A rise that fails that test is <b>one team busy</b>: worth a look, but not yet spreading. A theme is <b>cooling</b> when the interval is below the index's pace and the pace is at least a sixth slower. The size test stops large themes, whose intervals are narrow, from flagging small drifts. Themes with fewer than 20 new repositories across the two years get no status.</p>
        <p>Pace uses creation dates, so it cannot see repositories deleted or made private, and repositories still waiting for classification are missing from every theme alike. The theme API still reports <code>growth_12m</code>, repositories created in the last 12 months as a share of those before, but it mostly shows how young a theme is, so the site no longer uses it.</p>

        <h2 id="limits">Known limits</h2>
        <ul>
          <li>Labels are AI-assisted, not human-certified. Quote validation proves the evidence exists, not that the label is right.</li>
          <li>Labelled means at least one domain, kind or tag was assigned, not a complete tag set. A missing tag is unknown, not a verified negative.</li>
          <li>Registry labels mix countries with international bodies and sub-national governments, such as "U.S. City" and "U.K. Councils".</li>
          <li>GitLab and Forgejo report less than GitHub: few of their projects have a description or topics, Forgejo reports no licence, and neither has a push date, so the last activity date stands in for it. Their licensed and maintained figures are less complete.</li>
          <li>On GitLab, copies of GitHub repositories are only recognised by name, through the same body's GitHub account when the submission names it.</li>
          <li>Stars are a weak signal of value in government: a few very large projects hold most of them. Government forks are a better one, and the <a href="#/reuse">reuse page</a> counts them.</li>
        </ul>

        <h2 id="corrections">Corrections and submissions</h2>
        <p>If your organisation is missing, on GitHub or another code platform, <a href="#/submit">suggest it</a>. To suggest a whole government code server, use the same page with the server's address. If a repository is in the wrong theme or something else is wrong, open an issue on <a href="https://github.com/AndreasThinks/open-govtech-report/issues">GitHub</a> or email <a href="${mailto("Open GovTech Index correction")}">${esc(CONTACT)}</a>.</p>
      </article>
    </div></div>`;
  }

  // ------------------------------------------------------------------ countries
  const LEVEL_NAMES = { national: "National", regional: "Regional and state", local: "Local", tribal: "Tribal nations", international: "International bodies" };
  async function pageCountries(params) {
    const p = new URLSearchParams(params), level = p.get("level") || "";
    const places = await data.places();
    const items = places.items.filter(c => !level || c.level === level);
    app.innerHTML = `
    <section class="phead pad"><div class="in"><div class="crumb"><a href="#/">Overview</a> / Countries</div><h1>Countries</h1>
      <p>Each registry label, with the country and level of government it belongs to. Select one for its profile.</p>
      <div class="chips" style="margin-top:16px"><a href="#/countries"${!level ? ' aria-current="page"' : ""}>All<span>${places.items.length}</span></a>${places.levels.map(l => `<a href="#/countries?level=${l}"${l === level ? ' aria-current="page"' : ""}>${LEVEL_NAMES[l]}<span>${places.items.filter(c => c.level === l).length}</span></a>`).join("")}</div>
    </div></section>
    <section class="sec pad"><div class="in"><div class="card tbl"><table class="list"><thead><tr><th>Label</th><th>Country and level</th><th class="r">Maintained</th><th class="r">New in 12 months</th><th class="r">Accounts</th><th class="r">Licensed</th></tr></thead><tbody>
      ${items.map(c => `<tr><td><a class="plain" href="${countryHref(c.slug)}">${esc(c.label)}</a></td><td>${esc(c.country)}<small>${LEVEL_NAMES[c.level]}</small></td><td class="r">${num(c.maintained)}</td><td class="r">${num(c.new_12m)}</td><td class="r">${num(c.accounts)}</td><td class="r">${c.licensed_share == null ? "–" : Math.round(c.licensed_share) + "%"}</td></tr>`).join("")}
    </tbody></table></div></div></section>`;
  }

  async function pageCountry(slug) {
    const [c] = await Promise.all([data.country(slug), data.places().catch(() => null)]);
    if (!c) { app.innerHTML = notFound("There is no registry label with that name."); return; }
    const index = state.overview.totals, mix = c.theme_mix;
    app.innerHTML = `
    <section class="phead pad"><div class="in">
      <div class="crumb"><a href="#/">Overview</a> / <a href="#/countries">Countries</a> / ${esc(c.label)}</div>
      <div class="row">
        <div><div class="eyebrow">${esc(LEVEL_NAMES[c.level])}${c.country !== c.label ? ` · ${esc(c.country)}` : ""}</div><h1>${esc(c.label)}</h1>
          <p>Public repositories from ${num(c.accounts)} accounts under this registry label.</p></div>
        <div class="acts"><a class="btn ghost" href="#/compare?a=country:${enc(c.slug)}">Compare with…</a><a class="btn ghost" href="#/repos?country=${enc(c.label)}">Browse all</a><a class="btn ghost" href="${esc(API)}/v1/countries/${enc(c.slug)}">JSON</a></div>
      </div>
      <div class="stats">
        <div class="stat"><b>${num(c.maintained)}</b><small>maintained originals</small></div>
        <div class="stat"><b>${num(c.new_12m)}</b><small>new in 12 months</small></div>
        <div class="stat"><b>${num(c.accounts)}</b><small>accounts</small></div>
        <div class="stat"><b>${c.licensed_share == null ? "–" : c.licensed_share + "%"}</b><small>openly licensed (index ${index.licensed_share}%)</small></div>
        <div class="stat"><b>${num(c.stars)}</b><small>stars</small></div>
      </div>
    </div></section>
    <section class="sec pad"><div class="in"><div class="two">
      <div class="card"><h3>What it builds, compared with everyone else</h3><p class="cap">Share of this label's classified repositories in each theme. The black mark is the share across the whole index, so bars past the mark are specialisms.</p>${mixRows(mix)}</div>
      <div class="stack">
        <div class="card"><h3>Most active accounts</h3><p class="cap">Maintained original repositories.</p>${countRows(c.accounts_top.slice(0, 8), x => accountHref(x.owner), x => x.owner, x => x.maintained, x => `${num(x.repos)} originals · ${num(x.stars)} stars`) || '<p class="empty">None yet.</p>'}</div>
        <div class="card"><h3>Main languages</h3><p class="cap">Maintained original repositories.</p><div class="chips">${c.languages.map(l => `<a href="#/repos?country=${enc(c.label)}&language=${enc(l.language)}">${esc(l.language)}<span>${num(l.repos)}</span></a>`).join("") || '<span class="empty">None yet.</span>'}</div></div>
      </div>
    </div></div></section>
    <section class="sec pad"><div class="in"><div class="dig">
      <div class="card"><h3>Reused by other governments</h3><p class="cap">Government forks of this label's code, by the forking label.</p>${countRows(c.reused_by, x => placeHref(x.label), x => x.label, x => x.forks) || '<p class="empty">No government forks yet.</p>'}
        ${c.most_reused.length ? `<p class="cap" style="margin-top:12px">Most reused: <a href="${repoHref(c.most_reused[0])}"><code>${esc(c.most_reused[0].full_name)}</code></a>, by ${num(c.most_reused[0].reused_by_accounts)} accounts.</p>` : ""}</div>
      <div class="card"><h3>Reuses from</h3><p class="cap">Other labels whose code this label has forked.</p>${countRows(c.reuses_from, x => placeHref(x.label), x => x.label, x => x.forks) || '<p class="empty">No forks of other governments’ code.</p>'}</div>
      <div class="card"><h3>New repositories per month</h3><p class="cap">Original repositories, last 24 months.</p><svg class="fill" id="cols" viewBox="0 0 600 230" role="img" aria-label="New repositories per month"></svg>
        ${chartTable("New repositories per month", ["Month", "New"], c.monthly_new.map(m => [monthLabel(m.month), num(m.new)]))}</div>
    </div></div></section>
    <section class="sec pad"><div class="in">
      <h2>Most starred maintained repositories</h2><p class="sub"><a href="#/repos?country=${enc(c.label)}">Browse all</a>.</p>${repoTable(c.top_repos, "None yet.")}
      <h2 style="margin-top:32px">New and worth a look</h2><p class="sub">Recently created, with a description and real content. <a href="${esc((API || location.origin) + "/feeds/country/" + enc(c.slug) + ".xml")}">RSS feed</a>.</p>${repoTable(c.newest_repos, "Nothing new yet.")}
    </div></section>`;
    columns(document.getElementById("cols"), c.monthly_new);
  }

  // ------------------------------------------------------------------ accounts
  async function pageAccount(owner) {
    const a = await data.account(owner);
    if (!a) { app.innerHTML = notFound("That account is not in the index."); return; }
    app.innerHTML = `
    <section class="phead pad"><div class="in">
      <div class="crumb"><a href="#/">Overview</a> / <a href="${placeHref(a.label)}">${esc(a.label)}</a> / Account</div>
      <div class="row">
        <div><div class="eyebrow">${a.account_type === "user" ? "User account" : "Organisation"} · ${esc(LEVEL_NAMES[a.level] || "")}</div><h1 class="mono-h">${esc(a.owner)}</h1>
          <p>${num(a.repos)} public repositories under <a href="${placeHref(a.label)}">${esc(a.label)}</a>. First repository ${date(a.first_repo)}, last push ${ago(a.last_push)}.</p></div>
        <div class="acts"><a class="btn" href="${esc(a.url || a.github_url)}" rel="noopener">View on ${esc(hostName(a.url || a.github_url))}</a><a class="btn ghost" href="#/repos?q=${enc(a.owner)}&maintained=false">Browse all</a></div>
      </div>
      <div class="stats">
        <div class="stat"><b>${num(a.maintained)}</b><small>maintained originals</small></div>
        <div class="stat"><b>${num(a.original)}</b><small>original repositories</small></div>
        <div class="stat"><b>${a.licensed_share == null ? "–" : Math.round(a.licensed_share) + "%"}</b><small>originals licensed</small></div>
        <div class="stat"><b>${num(a.reused_by.reduce((n, x) => n + x.forks, 0))}</b><small>forks by other governments</small></div>
        <div class="stat"><b>${num(a.stars)}</b><small>stars</small></div>
      </div>
    </div></section>
    <section class="sec pad"><div class="in"><div class="two">
      <div class="card"><h3>What it works on</h3><p class="cap">Share of this account's classified repositories in each theme, against the whole index.</p>${mixRows(a.theme_mix, 8)}</div>
      <div class="stack">
        <div class="card"><h3>New repositories per month</h3><p class="cap">Original repositories, last 24 months.</p><svg class="fill" id="cols" viewBox="0 0 600 230" role="img" aria-label="New repositories per month"></svg></div>
        <div class="card"><h3>Languages</h3><div class="chips">${a.languages.map(l => `<span class="chip">${esc(l.language)}<span>${num(l.repos)}</span></span>`).join("") || '<span class="empty">None recorded.</span>'}</div></div>
      </div>
    </div></div></section>
    <section class="sec pad"><div class="in"><div class="two">
      <div class="card"><h3>Who reuses its code</h3><p class="cap">Other government accounts that forked its repositories.</p>${countRows(a.reused_by, x => accountHref(x.owner), x => x.owner, x => x.forks, x => esc(x.country)) || '<p class="empty">No government forks yet.</p>'}</div>
      <div class="card"><h3>Whose code it reuses</h3><p class="cap">Government accounts it has forked from.</p>${countRows(a.reuses_from, x => accountHref(x.owner), x => x.owner, x => x.forks, x => esc(x.country)) || '<p class="empty">No forks of other governments’ code.</p>'}</div>
    </div></div></section>
    <section class="sec pad"><div class="in">
      <h2>Most starred</h2><p class="sub">Original repositories.</p>${repoTable(a.top_repos, "None yet.")}
      <h2 style="margin-top:32px">Recently pushed</h2><p class="sub">Unarchived originals.</p>${repoTable(a.recent_repos, "None yet.")}
    </div></section>`;
    columns(document.getElementById("cols"), a.monthly_new);
  }

  // ------------------------------------------------------------------ this week
  async function pageWeek() {
    const w = await data.week(), end = date(w.as_of), hidden = w.new - w.new_notable;
    app.innerHTML = `
    <section class="phead pad"><div class="in">
      <div class="crumb"><a href="#/">Overview</a> / This week</div>
      <div class="eyebrow">Week to ${esc(end)}</div><h1 style="margin-top:6px">This week in government code</h1>
      <p>New repositories worth a look, themes on the move and projects that changed, rebuilt from each weekly scrape.</p>
      <div class="stats">
        <div class="stat"><b>${num(w.new)}</b><small>new original repositories in 7 days</small></div>
        <div class="stat"><b>${num(w.new_notable)}</b><small>with a description and real content</small></div>
        <div class="stat"><b>${num(w.archived.count)}</b><small>archived this week</small></div>
        <div class="stat"><b>${num(state.overview.totals.new_30d)}</b><small>new in 30 days</small></div>
      </div>
    </div></section>
    <section class="sec pad"><div class="in"><div class="two">
      <div class="card"><h3>New and worth a look</h3><p class="cap">${esc(w.definitions.notable)} Empty placeholders are hidden.</p>
        ${w.notable.length ? w.notable.map(r => { const dom = r.domain && themeById(r.domain); return `<div class="newrow"><a href="${repoHref(r)}">${esc(r.full_name)}</a><span class="b">${esc(r.country)}</span><p>${esc(r.description)}</p><div class="meta">${dom ? `<a class="b co" href="${themeHref(dom.id)}">${esc(dom.name)}</a>` : ""}${r.language ? `<span class="b">${esc(r.language)}</span>` : ""}${licensed(r) ? `<span class="b ok">${esc(r.license)}</span>` : ""}<span class="b">${ago(r.created_at)}</span></div></div>`; }).join("") : '<p class="empty">Nothing notable was created this week.</p>'}
        ${hidden > 0 ? `<p class="cap" style="margin-top:10px">${num(hidden)} more new repositories had no description or code yet. <a href="#/repos?sort=newest&maintained=false">Show every new repository</a></p>` : ""}</div>
      <div class="stack">
        <div class="card"><h3>Themes picking up</h3><p class="cap">Emerging and single-team rises, by pace against the index.</p>
          <ul class="rows">${w.fastest_themes.map(t => `<li><a href="${themeHref(t.id)}">${esc(t.name)}</a><span>${paceText(t.pace)}</span><small>${esc(STATUS[t.status] || "")}</small><div class="bar"><i class="h" style="width:${Math.max(0, t.pace - 1) / Math.max(.01, w.fastest_themes[0].pace - 1) * 100}%"></i></div></li>`).join("") || "<li><small>No theme is clearly picking up right now.</small></li>"}</ul></div>
        <div class="card"><h3>Star movers</h3><p class="cap">${w.star_movers.since ? `Stars gained since the snapshot of ${date(w.star_movers.since)}.` : "Needs two weekly snapshots."}</p>
          <ul class="rows">${w.star_movers.items.map(r => `<li><a href="${repoHref(r)}"><code>${esc(r.full_name)}</code></a><span>+${num(r.gained)}</span><small>${esc(r.country)} · ${num(r.stars)} stars</small></li>`).join("") || "<li><small>No movers this week.</small></li>"}</ul></div>
        <div class="card"><h3>Archived this week</h3><p class="cap">${esc(w.definitions.archived)}</p>
          <ul class="rows">${w.archived.items.map(r => `<li><a href="${repoHref(r)}"><code>${esc(r.full_name)}</code></a><span>${num(r.stars)} ★</span><small>${esc(r.country)}</small></li>`).join("") || "<li><small>None this week.</small></li>"}</ul></div>
        <div class="card"><h3>Follow it</h3><p class="cap">One feed for the whole index; every theme and country page has its own.</p>
          <div class="feedbox"><input id="feedurl" readonly value="${esc((API || location.origin) + "/feeds/week.xml")}" aria-label="Weekly feed address">${copyButton("feedurl", "Copy feed")}</div></div>
      </div>
    </div></div></section>`;
  }

  // ------------------------------------------------------------------ reuse
  async function pageReuse() {
    const [r] = await Promise.all([data.reuse(), data.places().catch(() => null)]);
    const fmax = Math.max(1, ...r.flows.map(f => f.forks));
    app.innerHTML = `
    <section class="phead pad"><div class="in"><div class="crumb"><a href="#/">Overview</a> / Reuse</div><h1>Reuse between governments</h1>
      <p>${esc(r.definition)} Reuse is the strongest public sign that code is useful beyond the team that wrote it.</p>
      <div class="stats">
        <div class="stat"><b>${num(r.forks)}</b><small>government forks of government code</small></div>
        <div class="stat"><b>${num(r.cross_border_forks)}</b><small>across registry labels</small></div>
        <div class="stat"><b>${num(r.accounts_reusing)}</b><small>accounts reusing others' code</small></div>
      </div>
    </div></section>
    <section class="sec pad"><div class="in"><div class="two">
      <div class="card"><h3>Biggest flows</h3><p class="cap">From the label that wrote the code to the label that forked it.</p>
        <ul class="rows">${r.flows.slice(0, 15).map(f => `<li><span class="lbl"><a href="${placeHref(f.from)}">${esc(f.from)}</a> → <a href="${placeHref(f.to)}">${esc(f.to)}</a></span><span>${num(f.forks)}</span><div class="bar"><i style="width:${f.forks / fmax * 100}%"></i></div></li>`).join("") || "<li><small>No flows yet.</small></li>"}</ul></div>
      <div class="card"><h3>Most reused across borders</h3><p class="cap">Repositories forked by accounts under other labels.</p>
        <ul class="rows">${r.most_reused_across_borders.map(x => `<li><a href="${repoHref(x)}"><code>${esc(x.full_name)}</code></a><span>${num(x.reused_by_accounts)}</span><small>${esc(x.country)} · reused in ${num(x.reused_in_labels)} labels</small></li>`).join("") || "<li><small>None yet.</small></li>"}</ul></div>
    </div></div></section>
    <section class="sec pad"><div class="in"><div class="two">
      <div class="card"><h3>Most reused overall</h3><p class="cap">By the number of other government accounts that forked it.</p>
        <ul class="rows">${r.most_reused.map(x => `<li><a href="${repoHref(x)}"><code>${esc(x.full_name)}</code></a><span>${num(x.reused_by_accounts)}</span><small>${esc(x.country)}</small></li>`).join("")}</ul></div>
      <div class="card"><h3>Biggest reusers</h3><p class="cap">Accounts that forked the most government code.</p>${countRows(r.top_reusers, x => accountHref(x.owner), x => x.owner, x => x.forks, x => esc(x.country)) || '<p class="empty">None yet.</p>'}</div>
    </div></div></section>`;
  }

  // ------------------------------------------------------------------ compare
  async function pageCompare(params) {
    const p = new URLSearchParams(params), places = await data.places().catch(() => null);
    const options = (cur) => `<option value="">Choose…</option>${places ? `<optgroup label="Countries and labels">${places.items.map(c => `<option value="country:${esc(c.slug)}"${cur === "country:" + c.slug ? " selected" : ""}>${esc(c.label)}</option>`).join("")}</optgroup>` : ""}<optgroup label="Themes">${state.themes.themes.slice().sort((x, y) => x.name.localeCompare(y.name)).map(t => `<option value="theme:${esc(t.id)}"${cur === "theme:" + t.id ? " selected" : ""}>${esc(t.name)}</option>`).join("")}</optgroup>`;
    const a = p.get("a") || "", b = p.get("b") || "";
    app.innerHTML = `
    <section class="phead pad"><div class="in"><div class="crumb"><a href="#/">Overview</a> / Compare</div><h1>Compare</h1>
      <p>Two countries or two themes, side by side.</p>
      <form class="filters" id="cf" style="margin-top:18px"><label>First<select name="a">${options(a)}</select></label><label>Second<select name="b">${options(b)}</select></label><button class="btn" type="submit">Compare</button></form>
    </div></section><section class="sec pad"><div class="in" id="cmp"></div></section>`;
    document.getElementById("cf").addEventListener("submit", e => {
      e.preventDefault();
      const q = new URLSearchParams();
      for (const [k, v] of new FormData(e.target)) if (v) q.set(k, v);
      nav("#/compare?" + q);
    });
    const out = document.getElementById("cmp");
    if (!a || !b) { out.innerHTML = '<p class="empty">Choose two countries or two themes.</p>'; return; }
    const [ta, ida] = a.split(":"), [tb, idb] = b.split(":");
    if (ta !== tb) { out.innerHTML = '<p class="empty">Pick two countries, or two themes. A country and a theme measure different things.</p>'; return; }
    if (ta === "country") {
      const [x, y] = await Promise.all([data.country(ida), data.country(idb)]);
      if (!x || !y) { out.innerHTML = '<p class="empty">One of those labels is not in the index.</p>'; return; }
      const row = (l, f) => `<tr><th scope="row">${l}</th><td class="r">${f(x)}</td><td class="r">${f(y)}</td></tr>`;
      const spec = c => (c.theme_mix && c.theme_mix.specialisms || []).slice(0, 4).map(t => `<a href="${themeHref(t.id)}">${esc(t.name)}</a>`).join(", ") || "–";
      out.innerHTML = `<div class="card tbl"><table class="list cmp"><thead><tr><th></th><th class="r"><a href="${countryHref(x.slug)}">${esc(x.label)}</a></th><th class="r"><a href="${countryHref(y.slug)}">${esc(y.label)}</a></th></tr></thead><tbody>
        ${row("Level", c => esc(LEVEL_NAMES[c.level]))}${row("Maintained originals", c => num(c.maintained))}${row("New in 12 months", c => num(c.new_12m))}${row("Accounts", c => num(c.accounts))}
        ${row("Openly licensed", c => c.licensed_share == null ? "–" : c.licensed_share + "%")}${row("Stars", c => num(c.stars))}
        ${row("Forked by other labels", c => num(c.reused_by.reduce((n, r) => n + r.forks, 0)))}${row("Specialisms", spec)}${row("Main languages", c => c.languages.slice(0, 3).map(l => esc(l.language)).join(", ") || "–")}
      </tbody></table></div>
      <div class="two" style="margin-top:16px"><div class="card"><h3>${esc(x.label)}</h3>${mixRows(x.theme_mix, 8)}</div><div class="card"><h3>${esc(y.label)}</h3>${mixRows(y.theme_mix, 8)}</div></div>`;
    } else {
      const x = themeById(ida), y = themeById(idb);
      if (!x || !y) { out.innerHTML = '<p class="empty">One of those themes is not in the taxonomy.</p>'; return; }
      const row = (l, f) => `<tr><th scope="row">${l}</th><td class="r">${f(x)}</td><td class="r">${f(y)}</td></tr>`;
      out.innerHTML = `<div class="card tbl"><table class="list cmp"><thead><tr><th></th><th class="r"><a href="${themeHref(x.id)}">${esc(x.name)}</a></th><th class="r"><a href="${themeHref(y.id)}">${esc(y.name)}</a></th></tr></thead><tbody>
        ${row("Domain", t => esc(t.domain_name))}${row("Original repositories", t => num(t.repos))}${row("Maintained", t => num(t.maintained))}${row("New in 12 months", t => num(t.new_12m))}
        ${row("Pace against the index", t => paceText(t.pace))}${row("Countries and labels", t => num(t.countries))}${row("Accounts", t => num(t.accounts))}${row("Licensed", t => t.licensed_share == null ? "–" : Math.round(t.licensed_share) + "%")}
        ${row("Status", t => STATUS[t.status] || "–")}
      </tbody></table></div>`;
    }
    fixLinks(out);
  }

  // ------------------------------------------------------------------ building blocks
  async function pageBlocks() {
    const b = await data.blocks();
    app.innerHTML = `
    <section class="phead pad"><div class="in"><div class="crumb"><a href="#/">Overview</a> / Building blocks</div><h1>Building blocks</h1>
      <p>Components that government teams have built and others can adopt. The picks are reviewed by hand${b.reviewed ? ` (last on ${esc(date(b.reviewed))})` : ""}; the figures are live from the index. <a href="https://github.com/AndreasThinks/open-govtech-report/blob/main/src/govtech_scraper/api/building_blocks.yaml">Suggest a pick</a>.</p>
      <div class="chips" style="margin-top:16px">${b.groups.map(g => `<a href="#/building-blocks" data-scroll="bb-${esc(g.id)}">${esc(g.name)}<span>${g.picks.length}</span></a>`).join("")}</div>
    </div></section>
    ${b.groups.map(g => `<section class="sec pad" id="bb-${esc(g.id)}"><div class="in">
      <h2>${esc(g.name)}</h2><p class="sub">${esc(g.summary)}${g.tag && themeById(g.tag) ? ` <a href="${themeHref(g.tag)}">See the ${esc(themeById(g.tag).name.toLowerCase())} theme</a>.` : ""}</p>
      <div class="blocks">${g.picks.map(r => `<div class="card block"><a class="nm" href="${repoHref(r)}">${esc(r.full_name)}</a><p>${esc(r.note || r.description || "")}</p>
        <div class="badges"><span class="b">${esc(r.country)}</span>${r.language ? `<span class="b">${esc(r.language)}</span>` : ""}${licensed(r) ? `<span class="b ok">${esc(r.license)}</span>` : '<span class="b no">No licence</span>'}${r.archived ? '<span class="b no">Archived</span>' : ""}</div>
        <p class="cap">${num(r.stars)} stars · ${num(r.government_forks)} government forks · pushed ${ago(r.pushed_at)}</p></div>`).join("") || '<p class="empty">No picks are in the current publication.</p>'}</div>
      ${g.also_popular.length ? `<p class="cap" style="margin-top:12px">Also popular in this theme: ${g.also_popular.map(r => `<a href="${repoHref(r)}">${esc(r.full_name)}</a>`).join(", ")}.</p>` : ""}
    </div></section>`).join("")}`;
  }

  // ------------------------------------------------------------------ practice
  async function pagePractice() {
    const pr = await data.practice(), ix = pr.index;
    const pc = v => v == null ? "–" : Math.round(v) + "%";
    const bar = (v, ref) => `<span class="pbar"><i style="width:${v || 0}%"></i><em style="left:${ref || 0}%"></em></span>${pc(v)}`;
    app.innerHTML = `
    <section class="phead pad"><div class="in"><div class="crumb"><a href="#/">Overview</a> / Open-source practice</div><h1>Open-source practice</h1>
      <p>Simple signs that code is ready for others to reuse: a licence, a description and topics that make it findable. Labels with at least ${num(pr.minimum_maintained)} maintained repositories, ranked by the average of the three. Stars play no part.</p>
      <div class="stats">
        <div class="stat"><b>${pc(ix.licensed_share)}</b><small>licensed, whole index</small></div>
        <div class="stat"><b>${pc(ix.described_share)}</b><small>described</small></div>
        <div class="stat"><b>${pc(ix.topics_share)}</b><small>with topics</small></div>
        <div class="stat"><b>${pc(ix.archived_share)}</b><small>of originals archived</small></div>
      </div>
    </div></section>
    <section class="sec pad"><div class="in">
      <div class="card tbl"><table class="list"><thead><tr><th>Label</th><th class="r">Maintained</th><th>Licensed</th><th>Described</th><th>Topics</th><th class="r">Archived</th><th class="r">Score</th></tr></thead><tbody>
        ${pr.items.map(c => `<tr><td><a class="plain" href="${countryHref(c.slug)}">${esc(c.label)}</a><small>${esc(LEVEL_NAMES[c.level])}</small></td><td class="r">${num(c.maintained)}</td><td>${bar(c.licensed_share, ix.licensed_share)}</td><td>${bar(c.described_share, ix.described_share)}</td><td>${bar(c.topics_share, ix.topics_share)}</td><td class="r">${pc(c.archived_share)}</td><td class="r"><b>${pc(c.score)}</b></td></tr>`).join("")}
      </tbody></table></div>
      <p class="cap" style="margin-top:12px">The black mark on each bar is the whole index. ${esc(pr.definitions.archived_share)} Coming next: README, contributing guide and security policy, from GitHub's community profile.</p>
    </div></section>`;
  }

  // ------------------------------------------------------------------ submit an account
  // Government servers read in full (keep in step with forge_hosts.yaml; a test checks).
  const FORGE_SERVERS = { "gitlab.opencode.de": "gitlab", "code.europa.eu": "gitlab", "code.overheid.nl": "forgejo", "gitlab.adullact.net": "gitlab", "repo1.dso.mil": "gitlab", "code.usgs.gov": "gitlab", "code.ornl.gov": "gitlab" };
  // Shared servers whose platform we know; any other host asks.
  const SHARED_HOSTS = { "gitlab.com": "gitlab", "framagit.org": "gitlab", "codeberg.org": "forgejo", "gitea.com": "forgejo" };
  const PLATFORM_NAMES = { github: "GitHub", gitlab: "GitLab", forgejo: "Forgejo or Gitea" };
  const REPO_URL = "https://github.com/AndreasThinks/open-govtech-report";

  function parseCodeUrl(raw) {
    let u;
    try { u = new URL(/^https?:\/\//i.test(raw.trim()) ? raw.trim() : "https://" + raw.trim()); } catch (e) { return null; }
    const host = u.host.toLowerCase().replace(/^www\./, "");
    let path = u.pathname.replace(/\/+$/, "").replace(/^\/+/, "");
    path = path.replace(/^groups\//, "").split("/-/")[0];  // GitLab group pages
    if (!/^[a-z0-9.-]+\.[a-z]{2,}$/.test(host)) return null;
    return { host, path };
  }

  // Ask the platform directly where it allows browsers to (gitlab.com and Codeberg do).
  async function checkAccount(host, platform, owner, type) {
    const p = enc(owner);
    const urls = platform === "github" ? [`https://api.github.com/users/${p}`]
      : platform === "forgejo" ? [`https://${host}/api/v1/users/${p}`]
      : type === "user" ? [`https://${host}/api/v4/users?username=${p}`] : [`https://${host}/api/v4/groups/${p}?with_projects=false`];
    try {
      const r = await fetch(urls[0], { headers: { Accept: "application/json" } });
      if (r.status === 404) return "missing";
      if (!r.ok) return "unknown";
      const body = await r.json();
      return Array.isArray(body) && !body.length ? "missing" : "found";
    } catch (e) { return "unknown"; }
  }

  function pageSubmit() {
    app.innerHTML = `
    <section class="phead pad"><div class="in"><div class="crumb"><a href="#/">Overview</a> / Add an account</div><h1>Add an account</h1>
      <p>Know a government account whose code is missing? It can be on GitHub, on a GitLab server (including gitlab.com) or on a Forgejo or Gitea server such as Codeberg. Fill this in and we will open a prefilled submission on GitHub for you to propose. A maintainer reviews every submission.</p></div></section>
    <section class="sec pad"><div class="in two">
      <form class="card subform" id="sub" novalidate>
        <label>Where is the code?<input name="url" id="suburl" required autocomplete="off" spellcheck="false" placeholder="https://gitlab.com/your-group"><small>The address of the organisation, group or user page, not a single repository.</small></label>
        <div class="detect" id="subdetect" aria-live="polite"><span class="cap">Paste an address to see what the index can read there.</span></div>
        <label id="subplat" hidden>Which platform is this server?<select name="platform"><option value="gitlab">GitLab</option><option value="forgejo">Forgejo or Gitea</option><option value="other">Something else</option></select></label>
        <label>Account type<select name="account_type"><option value="org">Organisation or group</option><option value="user">Person</option></select></label>
        <label>Country<input name="country" required list="subcountries" placeholder="NL, Netherlands or U.K. Councils"><small>A two-letter code, a country name, or a label from the countries page</small><datalist id="subcountries"></datalist></label>
        <label>What it is<input name="description" required maxlength="200" placeholder="One plain sentence"></label>
        <label id="subtwin" hidden>Same body on GitHub<input name="same_as_github" pattern="[A-Za-z0-9][A-Za-z0-9-]{0,38}" placeholder="Optional, for example betagouv"><small>If this body also has a GitHub account, name it so copies of its GitHub repositories are skipped.</small></label>
        <label>Evidence it is government<textarea name="evidence" required rows="3" placeholder="One link per line: an official website or register that links to this account"></textarea><small>Best is an official site that links to the account, since anyone can create a group on gitlab.com or Codeberg.</small></label>
        <label>Notes for the reviewer<textarea name="notes" rows="2" placeholder="Optional"></textarea></label>
        <button class="btn" type="submit">Prepare submission</button>
      </form>
      <div class="card"><h3>Your submission</h3><p class="cap" id="subdest">This file goes in <code>submissions/pending/</code>. GitHub opens it ready to propose; you need a GitHub account.</p>
        <textarea id="subyaml" class="snippet" readonly rows="14">Fill in the form to see the file.</textarea>
        <div class="acts" style="margin-top:10px"><a class="btn" id="subgo" href="${REPO_URL}/tree/main/submissions" rel="noopener" aria-disabled="true">Open on GitHub</a>${copyButton("subyaml", "Copy file")}</div>
        <p class="cap" style="margin-top:12px">No GitHub account? Email the details to <a href="${mailto("Account for the Open GovTech Index")}">${esc(CONTACT)}</a>.</p></div>
    </div></section>`;
    const form = document.getElementById("sub"), out = document.getElementById("subyaml"), go = document.getElementById("subgo");
    const detect = document.getElementById("subdetect"), dest = document.getElementById("subdest");
    const platBox = document.getElementById("subplat"), twinBox = document.getElementById("subtwin");
    data.countries().then(c => {
      const list = document.getElementById("subcountries");
      if (list && c && c.items) list.innerHTML = c.items.map(x => `<option value="${esc(x.label)}">`).join("");
    }).catch(() => {});
    const q = v => JSON.stringify(v);
    let target = null, seq = 0;

    function describe() {
      const f = Object.fromEntries(new FormData(form));
      const parsed = parseCodeUrl(f.url || "");
      target = null;
      platBox.hidden = twinBox.hidden = true;
      if (!f.url) { detect.className = "detect"; detect.innerHTML = '<span class="cap">Paste an address to see what the index can read there.</span>'; return; }
      if (!parsed) { detect.className = "detect no"; detect.innerHTML = "That does not look like a web address."; return; }
      const { host, path } = parsed;
      if (host === "github.com") {
        const owner = path.split("/")[0];
        if (!owner) { detect.className = "detect no"; detect.innerHTML = "Add the account name, as in github.com/alphagov."; return; }
        target = { kind: "account", host, platform: "github", owner };
      } else if (FORGE_SERVERS[host]) {
        detect.className = "detect ok";
        detect.innerHTML = `<b>${esc(host)}</b> is a government server the index already reads in full, so every public group on it is included.`;
        target = { kind: "covered", host }; return;
      } else {
        const known = SHARED_HOSTS[host];
        if (!known) platBox.hidden = false;
        const platform = known || f.platform;
        if (platform === "other") {
          detect.className = "detect no";
          detect.innerHTML = `The index can read GitHub, GitLab, Forgejo and Gitea. For other platforms, email <a href="${mailto("Code platform for the Open GovTech Index: " + host)}">${esc(CONTACT)}</a> and we will note it.`;
          return;
        }
        twinBox.hidden = false;
        if (!path) {
          target = { kind: "server", host, platform };
          detect.className = "detect warn";
          detect.innerHTML = `This is a whole server. If it is run by a government, suggest it and a maintainer will add it; the button opens an issue instead of a file. To add one account on it, include the group, as in <code>${esc(host)}/your-group</code>.`;
          return;
        }
        target = { kind: "account", host, platform, owner: platform === "forgejo" ? path.split("/")[0] : path };
      }
      const t = target, mine = ++seq;
      detect.className = "detect";
      detect.innerHTML = `<span class="b co">${PLATFORM_NAMES[t.platform]}</span> <b>${esc(t.host)}/${esc(t.owner)}</b> <span class="cap">Checking…</span>`;
      const indexed = t.platform === "github" ? t.owner : `${t.host}/${t.owner}`;
      Promise.all([
        state.live ? data.account(indexed).catch(() => null) : Promise.resolve(null),
        checkAccount(t.host, t.platform, t.owner, f.account_type),
      ]).then(([inIndex, check]) => {
        if (mine !== seq) return;
        const head = `<span class="b co">${PLATFORM_NAMES[t.platform]}</span> <b>${esc(t.host)}/${esc(t.owner)}</b>`;
        if (inIndex) { detect.className = "detect ok"; detect.innerHTML = `${head}<br>This account is already in the index, with ${num(inIndex.repos)} repositories. <a href="${accountHref(inIndex.owner)}">See it</a>.`; target.kind = "indexed"; fixLinks(detect); return; }
        const note = { found: "Found. The index can read its public repositories.", missing: "Not found. Check the address, and whether it is a group or a person.", unknown: "Could not check from your browser; the review will check it." }[check];
        detect.className = "detect " + (check === "missing" ? "no" : check === "found" ? "ok" : "");
        detect.innerHTML = `${head}<br>${note}`;
      });
    }
    const urlBox = document.getElementById("suburl");
    let timer;
    urlBox.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(describe, 400); });
    form.platform.addEventListener("change", describe);
    form.account_type.addEventListener("change", describe);

    form.addEventListener("submit", e => {
      e.preventDefault();
      describe();
      const f = Object.fromEntries(new FormData(form));
      const evidence = f.evidence.split(/\s+/).filter(x => /^https:\/\//.test(x));
      const fail = msg => { out.value = msg; go.setAttribute("aria-disabled", "true"); go.href = `${REPO_URL}/tree/main/submissions`; };
      if (!target) return fail("Add the address of the account first.");
      if (target.kind === "covered") return fail(`Nothing to submit: ${target.host} is already read in full.`);
      if (target.kind === "indexed") return fail("Nothing to submit: this account is already in the index.");
      if (!f.country.trim() || !f.description.trim()) return fail("Fill in the country and what it is.");
      if (!evidence.length) return fail("Add at least one evidence link starting with https://");
      const country = /^[A-Za-z]{2}$/.test(f.country.trim()) ? f.country.trim().toUpperCase() : f.country.trim();
      const ev = evidence.map(x => `  - ${x}`).join("\n");
      if (target.kind === "server") {
        const body = `Please add this government code server.\n\nServer: https://${target.host}\nPlatform: ${PLATFORM_NAMES[target.platform]}\nCountry: ${country}\nWho runs it: ${f.description.trim()}\nEvidence:\n${ev}\n\nNotes: ${f.notes.trim() || "none"}`;
        out.value = body;
        dest.innerHTML = "Whole servers are added by a maintainer, so this opens a GitHub issue rather than a file.";
        go.textContent = "Open issue on GitHub";
        go.href = `${REPO_URL}/issues/new?title=${enc("Add government code server: " + target.host)}&body=${enc(body)}`;
        go.removeAttribute("aria-disabled");
        return;
      }
      const where = target.platform === "github" ? `org: ${target.owner}\n`
        : `host: ${target.host}\nplatform: ${target.platform}\nowner: ${q(target.owner)}\n${f.same_as_github ? `same_as_github: ${f.same_as_github.trim()}\n` : ""}`;
      const yaml = `${where}country: ${q(country)}\naccount_type: ${f.account_type}\ndescription: ${q(f.description.trim())}\nevidence:\n${ev}\nnotes: ${q(f.notes.trim())}\n`;
      const stem = [country, target.platform === "github" ? "" : target.host.split(".")[0], target.owner].filter(Boolean).join("-");
      const file = stem.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") + ".yaml";
      out.value = yaml;
      dest.innerHTML = `This file goes in <code>submissions/pending/${esc(file)}</code>. GitHub opens it ready to propose; you need a GitHub account.`;
      go.textContent = "Open on GitHub";
      go.href = `${REPO_URL}/new/main/submissions/pending?filename=${enc(file)}&value=${enc(yaml)}`;
      go.removeAttribute("aria-disabled");
    });
  }

  function pageData() {
    const base = API || location.origin;
    app.innerHTML = `
    <section class="phead pad"><div class="in"><div class="crumb"><a href="#/">Overview</a> / Data and API</div><h1>Data and API</h1>
      <p>Everything on this site comes from a free, open, read-only API. Use it directly, or connect an AI assistant to it.</p></div></section>
    <section class="sec pad"><div class="in prose">
      <h2>REST</h2>
      <p>Interactive documentation is at <a href="${esc(API)}/docs">/docs</a>. CORS is open.</p>
      <ul>
        <li><code>/v1/overview</code>: headline numbers, timeline, countries and newest repositories</li>
        <li><code>/v1/themes</code> and <code>/v1/themes/{id}</code>: domains and tags with size, growth and adopters</li>
        <li><code>/v1/repos</code>: search and filter by text, country, language, topic, domain, tag or kind</li>
        <li><code>/v1/repos/{owner}/{name}</code> and <code>/context</code>: one repository, its classification, history and reuse</li>
        <li><code>/v1/countries</code> and <code>/v1/places</code>: registry labels with country, level of government and activity; <code>/v1/countries/{id}</code> for one</li>
        <li><code>/v1/accounts/{owner}</code>: one government account; accounts on other forges are <code>{host}/{group}</code></li>
        <li><code>/v1/week</code>, <code>/v1/reuse</code>, <code>/v1/practice</code>, <code>/v1/building-blocks</code>: the data behind those pages</li>
        <li><code>/v1/taxonomy</code>, <code>/v1/stats</code></li>
      </ul>
      <h2>Feeds and badges</h2>
      <p>RSS feeds of new repositories worth a look: <a href="${esc(API)}/feeds/week.xml">/feeds/week.xml</a>, <code>/feeds/theme/{id}.xml</code> and <code>/feeds/country/{id}.xml</code>. Every repository has a README badge at <code>/badge/{owner}/{name}.svg</code>; copy it from the repository's page.</p>
      <h2>MCP</h2>
      <p>AI assistants that support the Model Context Protocol can query the index with the same answers as this site.</p>
      <pre>{ "mcpServers": { "govtech-index": { "url": "${esc(base)}/mcp/" } } }</pre>
      <h2>Downloads</h2>
      <p>The full SQLite database, Parquet and CSV exports are published weekly to the <a href="https://huggingface.co/datasets/AndreasThinks/government-github-repos">government-github-repos dataset</a> on Hugging Face.</p>
    </div></section>`;
  }

  const notFound = msg => `<section class="phead pad"><div class="in"><div class="crumb"><a href="#/">Overview</a></div><h1>Not found</h1><p>${esc(msg)}</p></div></section>`;

  // ------------------------------------------------------------------ router
  async function route() {
    hideTip();
    const raw = PATHS ? (location.pathname + location.search).replace(/^\//, "") : location.hash.replace(/^#\/?/, "");
    const [path, query = ""] = raw.split("?");
    const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
    const page = parts[0] || "overview";
    const section = { theme: "themes", repo: "repos", country: "countries", account: "countries", method: "about" }[page] || page;
    document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("on", a.dataset.nav === section));
    try {
      if (page === "overview") pageOverview();
      else if (page === "themes") pageThemes();
      else if (page === "theme") await pageTheme(parts[1]);
      else if (page === "repos") await pageRepos(query);
      else if (page === "repo") await pageRepo(parts.slice(1, -1).join("/"), parts[parts.length - 1]);
      else if (page === "about" || page === "method") pageAbout();
      else if (page === "countries") await pageCountries(query);
      else if (page === "country") await pageCountry(parts[1]);
      else if (page === "account") await pageAccount(parts.slice(1).join("/"));
      else if (page === "week") await pageWeek();
      else if (page === "reuse") await pageReuse();
      else if (page === "compare") await pageCompare(query);
      else if (page === "building-blocks") await pageBlocks();
      else if (page === "practice") await pagePractice();
      else if (page === "submit") pageSubmit();
      else if (page === "data") pageData();
      else app.innerHTML = notFound("There is no page at this address.");
    } catch (e) {
      console.error(e);
      app.innerHTML = notFound(state.live === false && e.status === 503 ? e.message : "Something went wrong loading this page. The API may be waking up; try again in a minute.");
    }
    fixLinks(app);
    const h1 = app.querySelector("h1");
    document.title = h1 && page !== "overview" ? `${h1.textContent} · Open GovTech Index` : "Open GovTech Index";
    window.scrollTo(0, 0);
  }
  // In-page sections (About, Building blocks) use data-scroll so they never fight the router.
  document.addEventListener("click", e => {
    const s = e.target.closest("[data-scroll]");
    if (s) {
      const target = document.getElementById(s.dataset.scroll), here = PATHS ? location.pathname.replace(/^\//, "") : location.hash.replace(/^#\/?/, "");
      const want = s.getAttribute("href").replace(/^#?\/?/, "");
      if (target && here.split("?")[0] === want.split("?")[0]) { e.preventDefault(); target.scrollIntoView(); return; }
      if (!target) { e.preventDefault(); nav("#/" + want); setTimeout(() => { const t = document.getElementById(s.dataset.scroll); if (t) t.scrollIntoView(); }, 150); return; }
    }
    if (!PATHS) return;
    const a = e.target.closest("a[href]");
    if (!a || a.target || a.hasAttribute("download") || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const href = a.getAttribute("href");
    if (href.startsWith("#/")) { e.preventDefault(); nav(href); return; }
    if (href.startsWith("/") && !href.startsWith("//") && !SERVER_PATH.test(href.split("?")[0])) { e.preventDefault(); nav(href); }
  });

  document.getElementById("sf").addEventListener("submit", e => {
    e.preventDefault();
    const q = document.getElementById("sq").value.trim();
    if (!q) return;
    const lq = q.toLowerCase(), t = state.themes.themes.find(x => x.name.toLowerCase().includes(lq));
    nav(t ? themeHref(t.id) : "#/repos?q=" + enc(q) + "&maintained=false");
  });
  if (PATHS) {
    // Old #/ links keep working: move them to the matching path.
    const fromHash = () => { if (location.hash.startsWith("#/")) { window.history.replaceState(null, "", location.hash.slice(1)); return true; } };
    fromHash();
    addEventListener("hashchange", () => { if (fromHash()) route(); });
    addEventListener("popstate", route);
  } else addEventListener("hashchange", route);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", route);
  boot().then(route);
})();
