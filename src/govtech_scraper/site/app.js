/* Open GovTech Index: a static, hash-routed site over the /v1 API.
 *
 * Data comes from the API configured in config.js (same origin by default). When the API is
 * unreachable, everything falls back to sample.js; when the API is up but no decision-model run
 * is stored, only the theme views fall back. A banner says which.
 */
(function () {
  "use strict";
  const API = ((window.OGI_CONFIG || {}).api || "").replace(/\/$/, "");
  const SAMPLE = window.OGI_SAMPLE;
  const app = document.getElementById("app");
  const tip = document.getElementById("tip");
  const state = { live: null, sampleThemes: false, overview: null, themes: null, countries: null, taxonomy: null };

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
  const repoHref = r => `#/repo/${r.full_name.split("/").map(enc).join("/")}`;
  const themeHref = id => `#/theme/${enc(id)}`;

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
    node.addEventListener("click", () => { location.hash = href; });
    node.addEventListener("keydown", e => { if (e.key === "Enter") location.hash = href; });
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
    const banner = document.getElementById("banner");
    if (!state.live) {
      banner.innerHTML = "Sample data: the index API could not be reached, so every figure on this site is an example.";
      banner.hidden = false;
    } else if (state.sampleThemes) {
      banner.innerHTML = "Repository figures are live. Themes show example figures until the classification run is published. <a href=\"#/method\">Why?</a>";
      banner.hidden = false;
    }
  }
  const data = {
    theme: id => state.sampleThemes ? Promise.resolve(SAMPLE.theme(id)) : get(`/v1/themes/${enc(id)}`).catch(e => e.status === 404 ? null : Promise.reject(e)),
    repos: params => {
      const themed = params.get("domain") || params.get("tag") || params.get("kind");
      if (!state.live || (themed && state.sampleThemes)) return Promise.resolve(SAMPLE.repos(params));
      return get(`/v1/repos?${params}`);
    },
    repo: async (o, n) => {
      if (!state.live) return SAMPLE.repo(o, n);
      try { return await get(`/v1/repos/${enc(o)}/${enc(n)}`); } catch (e) { if (e.status === 404) return SAMPLE.repo(o, n); throw e; }
    },
    context: async (o, n) => {
      if (!state.live) return SAMPLE.context(o, n);
      try { return await get(`/v1/repos/${enc(o)}/${enc(n)}/context`); } catch (e) { return SAMPLE.context(o, n); }
    },
    countries: async () => state.countries ||= state.live ? await get("/v1/countries") : SAMPLE.countries,
  };

  // ------------------------------------------------------------------ theme maths
  const tags = () => state.themes.themes.filter(t => t.type === "tag" && t.repos > 0);
  const domains = () => state.themes.themes.filter(t => t.type === "domain");
  const themeById = id => state.themes.themes.find(t => t.id === id);
  function overallGrowth() {
    const tl = state.overview.timeline, now = tl[tl.length - 1], then = tl[tl.length - 13];
    return then && then.total ? (now.total - then.total) / then.total * 100 : 0;
  }
  function quantile(vals, q) { const s = vals.slice().sort((a, b) => a - b); return s.length ? s[Math.min(s.length - 1, Math.floor(q * s.length))] : 0; }
  // Emerging: smaller than most themes, growing at least twice as fast as the index overall.
  function emergingRule() {
    const ts = tags();
    return { maxRepos: quantile(ts.map(t => t.repos), .75), minGrowth: Math.max(20, overallGrowth() * 2) };
  }
  function isEmerging(t, rule = emergingRule()) { return t.repos >= 5 && t.repos <= rule.maxRepos && (t.growth_12m ?? 0) >= rule.minGrowth; }
  function growthColour(g, gmax) {
    if (g == null) return `color-mix(in srgb,var(--hot) 60%,var(--bg))`;
    if (g < 0) return `color-mix(in srgb,var(--cool) ${Math.round(20 + Math.min(1, -g / 25) * 45)}%,var(--bg))`;
    const s = Math.sqrt(Math.min(1, g / Math.max(gmax, 1)));
    return `color-mix(in srgb,var(--hot) ${Math.round(8 + s * 82)}%,var(--bg))`;
  }
  const growthMax = () => quantile(tags().map(t => t.growth_12m ?? 0), .95);

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
    const W = 720, H = 470, gmax = growthMax();
    const cells = squarify(tags().sort((a, b) => b.repos - a.repos).map(t => ({ t, v: t.repos })), 0, 0, W, H);
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    cells.forEach(c => {
      const t = c.t, g = el("g", { class: "tile" });
      linkify(g, themeHref(t.id), `${t.name}: ${t.repos} repositories, ${pct(t.growth_12m)} growth`);
      g.append(el("rect", { x: c.x, y: c.y, width: Math.max(0, c.w), height: Math.max(0, c.h), rx: 4, style: `fill:${growthColour(t.growth_12m, gmax)}` }));
      const strong = (t.growth_12m ?? gmax) > gmax * .5;
      const fg = strong ? (dark ? "#111" : "#fff") : css("--ink");
      const fit = (s, px) => { const m = Math.floor((c.w - 16) / px); return s.length <= m ? s : s.slice(0, Math.max(1, m - 1)) + "…"; };
      if (c.w > 40 && c.h > 26) {
        g.append(txt({ x: c.x + 8, y: c.y + 19, fill: fg }, fit(t.name, 7.4)));
        if (c.h > 48) g.append(txt({ x: c.x + 9, y: c.y + 37, class: "s", fill: fg, "fill-opacity": .85 }, fit(`${num(t.repos)} · ${pct(t.growth_12m)}`, 7.2)));
      }
      hover(g, `<b>${esc(t.name)}</b>${num(t.repos)} repositories · ${pct(t.growth_12m)} · ${t.countries} countries<br>${esc(t.domain_name)}`);
      svg.append(g);
    });
    return [-25, -8, 0, gmax * .2, gmax * .5, gmax].map(g => `<i style="background:${growthColour(g, gmax)}"></i>`).join("");
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
  function momentum(svg) {
    const W = 1000, H = 420, M = { l: 70, r: 24, t: 18, b: 40 }, ts = tags(), rule = emergingRule();
    if (!ts.length) return;
    const rs = ts.map(t => t.repos), gs = ts.map(t => t.growth_12m ?? 0);
    const xlo = Math.log10(Math.max(1, Math.min(...rs)) * .8), xhi = Math.log10(Math.max(...rs) * 1.25);
    const glo = Math.min(-10, Math.min(...gs)) - 5, ghi = Math.max(20, quantile(gs, .98)) * 1.1;
    const xs = v => M.l + (Math.log10(Math.max(1, v)) - xlo) / (xhi - xlo) * (W - M.l - M.r);
    const ys = v => M.t + (1 - (Math.min(v, ghi) - glo) / (ghi - glo)) * (H - M.t - M.b);
    const ax = el("g", { class: "ax" }); svg.append(ax);
    [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000].filter(v => Math.log10(v) > xlo && Math.log10(v) < xhi).forEach(v => {
      ax.append(el("line", { x1: xs(v), x2: xs(v), y1: M.t, y2: H - M.b })); ax.append(txt({ x: xs(v), y: H - M.b + 16, "text-anchor": "middle" }, num(v)));
    });
    ax.append(txt({ x: W - M.r, y: H - 4, "text-anchor": "end" }, "repositories (log scale)"));
    const step = ghi - glo > 200 ? 50 : ghi - glo > 80 ? 25 : 10;
    for (let v = Math.ceil(glo / step) * step; v <= ghi; v += step) {
      ax.append(el("line", { x1: M.l, x2: W - M.r, y1: ys(v), y2: ys(v), class: v === 0 ? "zero" : "" }));
      ax.append(txt({ x: M.l - 8, y: ys(v) + 4, "text-anchor": "end" }, pct(v)));
    }
    const ex = Math.min(xs(rule.maxRepos), W - M.r), ey = Math.max(M.t, ys(rule.minGrowth));
    svg.append(el("rect", { x: M.l, y: M.t, width: Math.max(0, ex - M.l), height: Math.max(0, ey - M.t), fill: css("--hot"), "fill-opacity": .07 }));
    svg.append(txt({ x: M.l + 10, y: M.t + 16, class: "quad hot" }, "Emerging"));
    svg.append(txt({ x: W - M.r - 10, y: ys(0) - 8, class: "quad", "text-anchor": "end" }, "Mature"));
    // Label emerging themes first, then the largest, skipping any label that would collide.
    const placed = [], labelled = new Set();
    ts.filter(t => isEmerging(t, rule)).sort((a, b) => b.growth_12m - a.growth_12m).concat(ts.slice().sort((a, b) => b.repos - a.repos).slice(0, 8)).forEach(t => {
      const x = xs(t.repos), y = ys(t.growth_12m ?? ghi), w = t.name.length * 6.6 + 16;
      const box = x < W - 200 ? [x, x + w] : [x - w, x];
      if (labelled.has(t.id) || placed.some(p => Math.abs(p.y - y) < 16 && p.box[0] < box[1] && box[0] < p.box[1])) return;
      placed.push({ y, box }); labelled.add(t.id);
    });
    ts.slice().sort((a, b) => b.countries - a.countries).forEach(t => {
      const x = xs(t.repos), y = ys(t.growth_12m ?? ghi), rad = 4 + Math.sqrt(t.countries) * 2.2;
      const g = el("g", { class: "dot" + (isEmerging(t, rule) ? " em" : "") });
      linkify(g, themeHref(t.id), t.name);
      g.append(el("circle", { cx: x, cy: y, r: rad }));
      if (labelled.has(t.id)) {
        const right = x < W - 200;
        g.append(txt(right ? { x: x + rad + 4, y: y + 4 } : { x: x - rad - 4, y: y + 4, "text-anchor": "end" }, t.name));
      }
      hover(g, `<b>${esc(t.name)}</b>${num(t.repos)} repositories · ${pct(t.growth_12m)} · ${t.countries} countries`);
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
      return `<tr><td><a href="${repoHref(r)}">${esc(r.full_name)}</a><small>${esc(r.description || "No description")}</small></td><td>${esc(r.country)}${dom ? `<small><a class="plain" href="${themeHref(dom.id)}">${esc(dom.name)}</a></small>` : ""}</td><td class="r">${num(r.stars)}</td><td class="r">${ago(r.pushed_at)}</td><td>${openness(r)}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  }
  const watched = new Set(); try { JSON.parse(localStorage.getItem("ogi-watch") || "[]").forEach(k => watched.add(k)); } catch (e) { /* storage blocked */ }
  const saveWatched = () => { try { localStorage.setItem("ogi-watch", JSON.stringify([...watched])); } catch (e) { /* storage blocked */ } };

  // ------------------------------------------------------------------ pages
  function pageOverview() {
    const o = state.overview, t = o.totals, ts = tags(), rule = emergingRule();
    const em = ts.filter(x => isEmerging(x, rule)).sort((a, b) => b.growth_12m - a.growth_12m);
    const fastest = ts.filter(x => x.repos >= 5 && x.growth_12m != null).sort((a, b) => b.growth_12m - a.growth_12m).slice(0, 6);
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
        <div class="kpi"><div class="k">Emerging themes</div><div class="v">${em.length} <small>of ${ts.length}</small></div><div class="d hot">${em[0] ? `${esc(em[0].name)} fastest, ${pct(em[0].growth_12m)}` : "None above the threshold"}</div></div>
        <div class="kpi"><div class="k">Licensed for reuse</div><div class="v">${t.licensed_share == null ? "–" : Math.round(t.licensed_share) + "%"}</div><div class="d">of maintained repositories</div></div>
      </div>
    </div></section>

    <section class="sec pad"><div class="in">
      <h2>Everything in the index</h2>
      <p class="sub">Each block is a theme, sized by original repositories and coloured by 12-month growth. Select one to open its page.</p>
      <div class="ov">
        <div class="card"><svg class="fill" id="tm" viewBox="0 0 720 470" role="img" aria-label="Treemap of themes"></svg>
          <div class="dlegend"><span>Shrinking</span><span class="bar" id="dbar"></span><span>Growing fast</span><span style="margin-left:auto">Area = original repositories</span></div></div>
        <div class="stack">
          <div class="card"><h3>Original repositories over time</h3><p class="cap">By creation date, last three years. ${pct(growth, 1)} in 12 months.</p><svg class="fill" id="tot" viewBox="0 0 440 210" role="img" aria-label="Line chart of original repositories over three years"></svg></div>
          <div class="card"><h3>Where things stand</h3><p class="cap">Original repositories by status.</p><ul class="rows" id="flow"></ul></div>
        </div>
      </div>
    </div></section>

    <section class="sec pad"><div class="in">
      <h2>Momentum</h2>
      <p class="sub">Which themes are growing, and how big they already are. Further right is more repositories; higher is faster growth. Circle size is the number of countries. The shaded corner holds emerging themes: smaller than three quarters of themes, growing at least ${pct(rule.minGrowth)} a year, twice the index overall.</p>
      <div class="card"><svg class="fill" id="mm" viewBox="0 0 1000 420" role="img" aria-label="Scatter chart of themes by size and growth"></svg></div>
    </div></section>

    <section class="sec pad"><div class="in">
      <h2>Dig in</h2>
      <p class="sub">Starting points for a closer look.</p>
      <div class="dig">
        <div class="card"><h3>${tracked.length ? "Themes you track" : "Fastest growing"}</h3><p class="cap">${tracked.length ? "Saved in this browser." : "Themes with at least five repositories."}</p><ul class="rows">${(tracked.length ? tracked : fastest).map(x => `<li><a href="${themeHref(x.id)}">${esc(x.name)}</a><span>${pct(x.growth_12m)}</span><small>${num(x.repos)} repositories · ${x.countries} countries${isEmerging(x, rule) ? ' · <span class="sig">Emerging</span>' : ""}</small></li>`).join("")}</ul><a class="more" href="#/themes">All themes →</a></div>
        <div class="card"><h3>Countries</h3><p class="cap">Maintained repositories, with share licensed.</p><ul class="rows">${o.countries.map(c => `<li><a href="#/repos?country=${enc(c.country)}">${esc(c.country)}</a><span>${num(c.maintained)} · ${Math.round(c.licensed_share)}%</span><div class="bar"><i style="width:${c.maintained / Math.max(1, o.countries[0].maintained) * 100}%"></i></div></li>`).join("")}</ul><a class="more" href="#/repos">Browse by country →</a></div>
        <div class="card"><h3>Newest</h3><p class="cap">Most recently created original repositories.</p><ul class="rows">${o.newest.map(r => `<li><a href="${repoHref(r)}"><code>${esc(r.full_name)}</code></a><span>${ago(r.created_at)}</span><small>${esc(r.country)}${r.domain && themeById(r.domain) ? " · " + esc(themeById(r.domain).name) : ""}</small></li>`).join("")}</ul><a class="more" href="#/repos?sort=newest">All new repositories →</a></div>
      </div>
    </div></section>`;
    document.getElementById("dbar").innerHTML = treemap(document.getElementById("tm"));
    lineChart(document.getElementById("tot"), tl.map(m => ({ label: monthLabel(m.month), value: m.total, n: m.new })), { tip: p => `<b>${num(p.value)} repositories</b>${p.label} · ${num(p.n)} created` });
    const s = o.status, F = [["Created in the last 12 months", s.new_12m, "var(--ok)"], ["Active (pushed in the last 12 months)", s.active, "var(--cobalt)"], ["Dormant (no push in 12 months)", s.dormant, "var(--cool)"], ["Archived", s.archived, "var(--faint)"]];
    const fmax = Math.max(...F.map(f => f[1]), 1);
    document.getElementById("flow").innerHTML = F.map(f => `<li><span class="lbl">${f[0]}</span><span>${num(f[1])}</span><div class="bar"><i style="width:${f[1] / fmax * 100}%;background:${f[2]}"></i></div></li>`).join("") +
      `<li><small>${num(tl[tl.length - 1].total - yearAgo.total)} more original repositories than a year ago.</small></li>`;
    momentum(document.getElementById("mm"));
  }

  function pageThemes() {
    const rule = emergingRule(), ds = domains(), all = tags();
    app.innerHTML = `
    <section class="phead pad"><div class="in"><div class="crumb"><a href="#/">Overview</a> / Themes</div>
      <h1>Themes</h1><p>The ten UN COFOG functions of government, plus cross-government capabilities, each split into tags. A repository has one domain and any number of tags.</p></div></section>
    <section class="sec pad"><div class="in"><div class="domgrid">${ds.map(d => {
      const kids = all.filter(t => t.domain === d.id).sort((a, b) => b.repos - a.repos), mx = Math.max(1, ...kids.map(k => k.repos));
      return `<div class="card"><h3><a href="${themeHref(d.id)}">${esc(d.name)}</a></h3><p class="cap">${num(d.repos)} repositories · ${pct(d.growth_12m)} in 12 months</p><ul class="rows">${kids.map(k => `<li><a href="${themeHref(k.id)}">${esc(k.name)}</a><span>${num(k.repos)} · ${pct(k.growth_12m)}</span>${isEmerging(k, rule) ? '<small><span class="sig">Emerging</span></small>' : ""}<div class="bar"><i style="width:${k.repos / mx * 100}%"></i></div></li>`).join("") || '<li><small>No tagged repositories yet.</small></li>'}</ul></div>`;
    }).join("")}</div></div></section>`;
  }

  async function pageTheme(id) {
    const t = await data.theme(id);
    if (!t) { app.innerHTML = notFound("That theme is not in the taxonomy."); return; }
    const summary = themeById(id) || t, rule = emergingRule(), em = t.type === "tag" && isEmerging(summary, rule);
    const ranked = tags().slice().sort((a, b) => (b.growth_12m ?? 1e9) - (a.growth_12m ?? 1e9));
    const rank = ranked.findIndex(x => x.id === id) + 1;
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
        <div class="stat"><b>${pct(t.growth_12m)}</b><small>growth in 12 months</small></div>
        <div class="stat"><b>${num(t.countries)}</b><small>countries and labels</small></div>
        ${t.type === "tag" && rank ? `<div class="stat"><b>#${rank} of ${ranked.length}</b><small>for growth</small></div>` : ""}
        <div class="stat"><b>${t.licensed_share == null ? "–" : Math.round(t.licensed_share) + "%"}</b><small>licensed</small></div>
      </div>
    </div></section>
    ${t.repos ? `
    <section class="sec pad"><div class="in"><div class="two">
      <div class="card"><h3>New repositories per month</h3><p class="cap">Last 24 months, by creation date. Line shows the 3-month average.</p><svg class="fill" id="cols" viewBox="0 0 600 230" role="img" aria-label="New repositories per month"></svg></div>
      <div class="card"><h3>Where it is happening</h3><p class="cap">Original repositories by country or registry label.</p><ul class="rows">${t.by_country.map(c => `<li><a href="#/repos?${filter}&country=${enc(c.country)}">${esc(c.country)}</a><span>${num(c.repos)}</span><div class="bar"><i style="width:${c.repos / Math.max(1, t.by_country[0].repos) * 100}%"></i></div></li>`).join("")}</ul></div>
    </div></div></section>
    <section class="sec pad"><div class="in"><div class="two">
      <div class="card"><h3>Who picked it up, and when</h3><p class="cap">First repository on this theme from the most recent ${t.adopters.length} accounts to adopt it.</p><svg class="fill" id="adopt" viewBox="0 0 600 300" role="img" aria-label="Adoption timeline"></svg></div>
      <div class="card"><h3>How repositories get this theme</h3><p class="cap">A decision model reads each repository's name, description, topics and language, and scores it against this definition. No text is generated.</p>
        <ul class="why"><li><b>Definition the model reads</b>${esc(t.definition)}</li><li><b>Rule</b>${t.type === "domain" ? "The most likely of the 11 domains." : "Probability of at least 0.5."} Model <code>${esc(t.model || state.themes.model || "not run yet")}</code>, taxonomy v${state.themes.taxonomy_version}.</li></ul>
        ${kids.length ? `<h3 style="margin-top:18px">Tags in this domain</h3><p class="cap">Repositories per tag.</p><div class="chips">${kids.map(k => `<a href="${themeHref(k.id)}">${esc(k.name)}<span>${num(k.repos)}</span></a>`).join("")}</div>` : ""}
        ${t.related && t.related.length ? `<h3 style="margin-top:18px">Often appears with</h3><p class="cap">Share of this theme's repositories that also carry the tag.</p><div class="chips">${t.related.map(r => `<a href="${themeHref(r.id)}">${esc(r.name)}<span>${Math.round(r.share)}%</span></a>`).join("")}</div>` : ""}
      </div>
    </div></div></section>
    <section class="sec pad"><div class="in">
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
    p.set("limit", "25");
    const [res, countries] = await Promise.all([data.repos(p), data.countries().catch(() => [])]);
    const off = +p.get("offset") || 0, total = res.total;
    const opt = (v, label, cur) => `<option value="${esc(v)}"${v === (cur || "") ? " selected" : ""}>${esc(label)}</option>`;
    app.innerHTML = `
    <section class="phead pad"><div class="in"><div class="crumb"><a href="#/">Overview</a> / Repositories</div>
      <h1>Repositories</h1><p>${num(total)} match. Original, unarchived repositories unless you include the rest.</p></div></section>
    <section class="sec pad"><div class="in">
      <form class="filters" id="ff">
        <label>Search<input name="q" value="${esc(p.get("q") || "")}" placeholder="Name, description or topic"></label>
        <label>Country<select name="country">${opt("", "Any")}${countries.map(c => opt(c.country, c.country, p.get("country"))).join("")}</select></label>
        <label>Domain<select name="domain">${opt("", "Any")}${domains().map(d => opt(d.id, d.name, p.get("domain"))).join("")}</select></label>
        <label>Tag<select name="tag">${opt("", "Any")}${tags().slice().sort((a, b) => a.name.localeCompare(b.name)).map(t => opt(t.id, t.name, p.get("tag"))).join("")}</select></label>
        <label>Sort<select name="sort">${[["stars", "Most stars"], ["recently_pushed", "Recently pushed"], ["newest", "Newest"], ["name", "Name"]].map(([v, l]) => opt(v, l, p.get("sort") || "stars")).join("")}</select></label>
        <label class="chk"><input type="checkbox" name="include_forks" value="true"${p.get("include_forks") === "true" ? " checked" : ""}> Forks</label>
        <label class="chk"><input type="checkbox" name="include_archived" value="true"${p.get("include_archived") === "true" ? " checked" : ""}> Archived</label>
        <button class="btn" type="submit">Apply</button>
      </form>
      ${repoTable(res.items, "Nothing matches these filters.")}
      <div class="pager">${off > 0 ? `<a class="btn ghost" href="#/repos?${pageParams(p, off - 25)}">Previous</a>` : ""}<span>${total ? `${num(off + 1)}–${num(Math.min(total, off + 25))} of ${num(total)}` : ""}</span>${off + 25 < total ? `<a class="btn ghost" href="#/repos?${pageParams(p, off + 25)}">Next</a>` : ""}</div>
    </div></section>`;
    document.getElementById("ff").addEventListener("submit", e => {
      e.preventDefault();
      const q = new URLSearchParams();
      for (const [k, v] of new FormData(e.target)) if (v) q.set(k, v);
      location.hash = "#/repos?" + q;
    });
  }
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
        <div><h1 style="font-family:var(--mono);font-weight:600;font-size:clamp(22px,3.4vw,36px);letter-spacing:-.01em;overflow-wrap:anywhere">${esc(r.full_name)}</h1><p>${esc((r.description || "No description").trim().replace(/([^.!?])$/, "$1."))} Published by ${esc(r.full_name.split("/")[0])}, ${esc(r.country)}.</p></div>
        <div class="acts"><a class="btn" href="${esc(r.html_url)}" rel="noopener">View on GitHub</a></div>
      </div>
      <div class="badges" style="margin-top:14px">${dom ? `<a class="b hot" href="${themeHref(dom.id)}">${esc(dom.name)}</a>` : ""}${(cls ? cls.tags : []).map(t => `<a class="b co" href="${themeHref(t.id)}">${esc((themeById(t.id) || { name: t.id }).name)}</a>`).join("")}${licensed(r) ? `<span class="b ok">${esc(r.license)}</span>` : '<span class="b no">No licence</span>'}${r.fork ? `<span class="b">Fork${r.fork_source ? " of " + esc(r.fork_source) : ""}</span>` : '<span class="b">Original, not a fork</span>'}${r.archived ? '<span class="b no">Archived</span>' : ""}${cls && cls.kind ? `<span class="b">${esc(cls.kind)}</span>` : ""}</div>
      <div class="stats">${[[num(r.stars), "stars"], [num(r.forks), "forks"], [ctx ? ctx.reused_by.length : 0, "government forks"], [ago(r.pushed_at), "last push"], [date(r.created_at), "created"], [r.language || "–", "main language"]].map(s => `<div class="stat"><b style="${String(s[0]).length > 9 ? "font-size:18px" : ""}">${esc(s[0])}</b><small>${s[1]}</small></div>`).join("")}</div>
    </div></section>
    <section class="sec pad"><div class="in"><div class="two">
      <div class="card"><h3>Stars and forks over time</h3><p class="cap">From weekly snapshots since the repository first appeared in the index.</p><svg class="fill" id="hist" viewBox="0 0 600 220" role="img" aria-label="Stars and forks over time"></svg></div>
      <div class="card"><h3>Why it is in these themes</h3><p class="cap">The decision model's probabilities, so you can check the classification.</p>
      ${cls ? `<div class="prob"><span style="text-align:left;color:var(--ink);font-family:var(--body)"><b>Domain:</b> ${domainLink}</span><span>${cls.domain_probability == null ? "" : cls.domain_probability.toFixed(2)}</span><div class="bar"><i style="width:${(cls.domain_probability || 0) * 100}%"></i></div>
        ${cls.tags.map(t => `<span style="text-align:left;color:var(--ink);font-family:var(--body)"><a href="${themeHref(t.id)}">${esc((themeById(t.id) || { name: t.id }).name)}</a></span><span>${t.probability.toFixed(2)}</span><div class="bar"><i style="width:${t.probability * 100}%"></i></div>`).join("")}</div>
        <p class="cap" style="margin-top:12px">Model <code>${esc(cls.model)}</code>. Tags apply at 0.5 or above.</p>` : `<p class="empty">This repository has not been through the classification run yet.</p>`}
      ${r.topics && r.topics.length ? `<h3 style="margin-top:18px">GitHub topics</h3><div class="chips">${r.topics.map(t => `<a href="#/repos?q=${enc(t)}">${esc(t)}</a>`).join("")}</div>` : ""}
      </div>
    </div></div></section>
    <section class="sec pad"><div class="in"><div class="dig">
      <div class="card"><h3>Reused by</h3><p class="cap">Government accounts in the index that forked it.</p><ul class="rows">${ctx && ctx.reused_by.length ? ctx.reused_by.map(f => `<li><a href="${repoHref(f)}">${esc(f.owner)}</a><span>${ago(f.created_at)}</span><small>${esc(f.country)}</small></li>`).join("") : "<li><small>No government forks yet.</small></li>"}</ul></div>
      <div class="card"><h3>Similar repositories</h3><p class="cap">Same theme, other accounts.</p><ul class="rows">${ctx && ctx.similar.length ? ctx.similar.map(x => `<li><a href="${repoHref(x)}"><code>${esc(x.full_name)}</code></a><span>${num(x.stars)} ★</span><small>${esc(x.country)}</small></li>`).join("") : "<li><small>None yet.</small></li>"}</ul></div>
      <div class="card"><h3>Details</h3><p class="cap">From the GitHub API, refreshed weekly.</p><ul class="rows"><li><span class="lbl">Open issues</span><span>${num(r.open_issues)}</span></li><li><span class="lbl">Size</span><span>${num(Math.round((r.size_kb || 0) / 1024))} MB</span></li><li><span class="lbl">Default branch</span><span>${esc(r.default_branch || "–")}</span></li><li><span class="lbl">Data</span><span><a href="${esc(API)}/v1/repos/${r.full_name.split("/").map(enc).join("/")}">JSON</a></span></li></ul></div>
    </div></div></section>`;
    history(document.getElementById("hist"), ctx ? ctx.history : [], r.created_at);
  }

  function pageMethod() {
    const c = state.overview.classification, rule = emergingRule();
    app.innerHTML = `
    <section class="phead pad"><div class="in"><div class="crumb"><a href="#/">Overview</a> / Method</div><h1>Method</h1>
      <p>What is counted, how themes are assigned, and what the numbers can and cannot tell you.</p></div></section>
    <section class="sec pad"><div class="in prose">
      <h2>Coverage</h2>
      <p>The index covers public repositories owned by accounts listed in the <a href="https://github.com/github/government.github.com">government.github.com registry</a>, plus community submissions that have been reviewed. It is not a census of all government code: an account missing from the registry is missing here. A weekly job scrapes every account through the GitHub API and keeps a snapshot of each repository.</p>
      <h2>Which repositories count</h2>
      <p><b>Original</b> repositories are those that are not forks. <b>Maintained</b> means original, not archived, and pushed to in the 12 months before the latest scrape. Themes and growth use original repositories only, so a government forking a popular project does not inflate a theme.</p>
      <h2>Themes</h2>
      <p>The taxonomy is fixed and reviewed. Its top level is the ten divisions of the UN Classification of the Functions of Government (COFOG), plus a domain for cross-government capabilities such as identity, payments and design systems. Each domain has tags, each with a one-line definition. The full list is at <a href="${esc(API)}/v1/taxonomy">/v1/taxonomy</a>.</p>
      <p>A small decision model reads each repository's name, description, topics and language and returns a probability for each option. It never writes text, so no new tags appear and nothing needs merging. A repository gets the most likely domain and every tag scoring 0.5 or more. Every probability is stored, so the threshold can change later without rescoring.</p>
      <p>Current run: ${c.model ? `<code>${esc(c.model)}</code>, ${num(c.classified_original_repos)} original repositories classified (${c.coverage}% coverage), taxonomy v${c.taxonomy_version}.` : "not published yet, so theme figures on this site are examples."}</p>
      <h2>Growth and emerging themes</h2>
      <p>Growth is the number of repositories created in the last 12 months as a share of those created before then. It uses creation dates, so it works from the first scrape, but it cannot see repositories deleted or made private during the year. As weekly snapshots build up, growth will move to snapshot-to-snapshot change.</p>
      <p>A theme is <b>emerging</b> when it has at least five repositories, is smaller than three quarters of themes (currently ${num(rule.maxRepos)} repositories or fewer), and grew at least twice as fast as the index overall (currently ${pct(rule.minGrowth)}).</p>
      <h2>Known limits</h2>
      <p>Classification reads metadata only. Repositories with no description or topics are hard to place, and accuracy has to be checked against a hand-labelled sample before the themes are relied on. Registry labels mix countries with international bodies and sub-national governments.</p>
    </div></section>`;
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
        <li><code>/v1/countries</code>, <code>/v1/taxonomy</code>, <code>/v1/stats</code></li>
      </ul>
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
    const raw = location.hash.replace(/^#\/?/, ""), [path, query = ""] = raw.split("?");
    const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
    const page = parts[0] || "overview";
    document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("on", a.dataset.nav === (page === "theme" ? "themes" : page === "repo" ? "repos" : page)));
    try {
      if (page === "overview") pageOverview();
      else if (page === "themes") pageThemes();
      else if (page === "theme") await pageTheme(parts[1]);
      else if (page === "repos") await pageRepos(query);
      else if (page === "repo") await pageRepo(parts[1], parts[2]);
      else if (page === "method") pageMethod();
      else if (page === "data") pageData();
      else app.innerHTML = notFound("There is no page at this address.");
    } catch (e) {
      console.error(e);
      app.innerHTML = notFound("Something went wrong loading this page. The API may be waking up; try again in a minute.");
    }
    const h1 = app.querySelector("h1");
    document.title = h1 && page !== "overview" ? `${h1.textContent} · Open GovTech Index` : "Open GovTech Index";
    window.scrollTo(0, 0);
  }

  document.getElementById("sf").addEventListener("submit", e => {
    e.preventDefault();
    const q = document.getElementById("sq").value.trim();
    if (!q) return;
    const lq = q.toLowerCase(), t = state.themes.themes.find(x => x.name.toLowerCase().includes(lq));
    location.hash = t ? themeHref(t.id) : "#/repos?q=" + enc(q);
  });
  addEventListener("hashchange", route);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", route);
  boot().then(route);
})();
