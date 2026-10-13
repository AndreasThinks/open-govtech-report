// Example figures, shaped like the API's responses. The site falls back to them when the API is
// unreachable, or for themes when no decision-model run has been stored yet. Never shown as real:
// the page carries a banner whenever any of this is on screen.
(function () {
  const DOMAINS = {
    "00": "Cross-government capabilities", "01": "General public services", "03": "Public order and safety",
    "04": "Economic affairs", "05": "Environmental protection", "07": "Health", "09": "Education", "10": "Social protection",
  };
  // [id, name, domain, repos, growth %, countries, definition]
  const T = [
    ["ai-and-machine-learning", "AI and machine learning", "00", 212, 186, 14, "Machine learning models, AI assistants, natural language processing or computer vision."],
    ["digital-identity", "Digital identity", "00", 146, 122, 11, "Login, authentication, single sign-on, electronic ID or identity verification for citizens or staff."],
    ["legislation-and-parliament", "Legislation and parliament", "01", 94, 97, 8, "Bills, laws, regulations, parliamentary proceedings or legal drafting."],
    ["security-tooling", "Security tooling", "00", 131, 88, 9, "Cybersecurity tools, vulnerability scanning, secrets management or security guidance."],
    ["climate", "Climate", "05", 236, 73, 12, "Climate change, emissions, carbon accounting or adaptation."],
    ["api-platform", "API platform", "00", 388, 64, 21, "API gateways, developer portals or standards for government APIs."],
    ["accessibility", "Accessibility", "00", 274, 41, 16, "Testing or improving accessibility of digital services for disabled users."],
    ["health-data-standards", "Health data standards", "07", 260, 35, 11, "FHIR, HL7, clinical terminologies or health data exchange."],
    ["procurement", "Procurement", "01", 121, 31, 10, "Public contracts, tenders, open contracting or supplier data."],
    ["online-forms", "Online forms", "00", 342, 28, 13, "Building, publishing or processing online forms and applications."],
    ["schools", "Schools", "09", 150, 20, 8, "School administration, pupil data or school information services."],
    ["benefits-and-welfare", "Benefits and welfare", "10", 410, 15, 9, "Social security benefits, eligibility checking or welfare case management."],
    ["design-system", "Design system", "00", 1180, 12, 24, "Reusable UI components, styles or front-end patterns for government websites and services."],
    ["notifications", "Notifications", "00", 198, 9, 10, "Sending emails, SMS, letters or push notifications to the public."],
    ["geospatial", "Geospatial", "00", 1620, 6, 28, "Maps, GIS, geocoding, addresses or other location data."],
    ["open-data-publishing", "Open data publishing", "00", 2140, -4, 31, "Publishing government data openly, including data portals, catalogues and open data APIs."],
    ["payments", "Payments", "00", 167, -8, 7, "Collecting fees, fines or taxes online, or payment gateway integration."],
    ["content-management", "Content management", "00", 600, -12, 15, "Content management systems or publishing tools for government websites."],
  ];
  const COUNTRIES = ["United States", "United Kingdom", "France", "Canada", "Australia", "Brazil", "Germany", "Netherlands"];
  function rng(seed) { let a = seed >>> 0; return () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ t >>> 15, t | 1); t ^= t + Math.imul(t ^ t >>> 7, t | 61); return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  const iso = d => d.toISOString().slice(0, 19) + "Z";
  const NOW = new Date(Date.UTC(2026, 9, 11, 6));
  const monthsBack = n => { const out = []; for (let i = n - 1; i >= 0; i--) { const d = new Date(Date.UTC(NOW.getUTCFullYear(), NOW.getUTCMonth() - i, 1)); out.push(d.toISOString().slice(0, 7)); } return out; };

  const REPOS = [
    ["i-dot-ai", "consult", "United Kingdom", "Analyses public consultation responses with a language model and human review.", "ai-and-machine-learning", "MIT", 88, 14, 21, "Python"],
    ["etalab-ia", "albert", "France", "Open generative AI assistant for French public servants.", "ai-and-machine-learning", "MIT", 312, 41, 35, "Python"],
    ["18F", "llm-guidance-sandbox", "United States", "Sandbox and evaluation harness for federal LLM use cases.", "ai-and-machine-learning", "CC0-1.0", 64, 9, 60, "TypeScript"],
    ["eu-digital-identity-wallet", "eudi-app-android-wallet-ui", "European Union", "Reference wallet app for the EU digital identity wallet.", "digital-identity", "EUPL-1.2", 206, 58, 120, "Kotlin"],
    ["openfisca", "openfisca-france", "France", "French tax and benefit rules as executable code.", "legislation-and-parliament", "AGPL-3.0", 401, 120, 3300, "Python"],
    ["cisagov", "sbom-tools", "United States", "Generate and validate software bills of materials.", "security-tooling", "CC0-1.0", 233, 37, 210, "Go"],
    ["defra", "emissions-inventory", "United Kingdom", "Pipelines for the national atmospheric emissions inventory.", "climate", "OGL-3.0", 27, 4, 150, "R"],
    ["alphagov", "govuk-frontend", "United Kingdom", "GOV.UK Frontend contains the code you need to start building a user interface for government services.", "design-system", "MIT", 1300, 340, 3100, "JavaScript"],
    ["alphagov", "notifications-api", "United Kingdom", "GOV.UK Notify API.", "notifications", "MIT", 120, 40, 3000, "Python"],
    ["ckan", "ckan", "Open Knowledge", "CKAN is an open-source DMS for powering data hubs and data portals.", "open-data-publishing", "AGPL-3.0", 4500, 2000, 5000, "Python"],
  ].map(([owner, name, country, description, tag, license, stars, forks, ageDays, language]) => {
    const t = T.find(x => x[0] === tag);
    return {
      full_name: `${owner}/${name}`, html_url: `https://github.com/${owner}/${name}`, country, description, language,
      stars, forks, license, topics: [tag], fork: false, archived: false,
      created_at: iso(new Date(NOW - ageDays * 864e5)), pushed_at: iso(new Date(NOW - (ageDays % 9) * 864e5)),
      domain: t[2], kind: "web-application", _tag: tag,
    };
  });

  function summary(t) {
    const [id, name, domain, repos, g, countries, definition] = t;
    const before = Math.round(repos / (1 + g / 100));
    return {
      id, type: "tag", name, definition, domain, domain_name: DOMAINS[domain], repos,
      maintained: Math.round(repos * .62), new_12m: repos - before, growth_12m: g, countries,
      accounts: countries * 3, licensed_share: 55 + countries, stars: repos * 12,
    };
  }
  const themes = T.map(summary);
  const domainThemes = Object.entries(DOMAINS).map(([id, name]) => {
    const kids = themes.filter(t => t.domain === id);
    const repos = kids.reduce((a, b) => a + b.repos, 0), newer = kids.reduce((a, b) => a + b.new_12m, 0);
    return { id, type: "domain", name, definition: "", domain: id, domain_name: name, repos, maintained: Math.round(repos * .6),
      new_12m: newer, growth_12m: Math.round(newer / (repos - newer) * 1000) / 10, countries: Math.max(...kids.map(k => k.countries)),
      accounts: 0, licensed_share: 69, stars: 0 };
  });

  const total = (() => { const r = rng(41), months = monthsBack(36), out = []; let v = 7900; months.forEach((m, i) => { const n = Math.round(48 + r() * 30 + i); v += n; out.push({ month: m, new: n, total: v }); }); return out; })();

  function theme(id) {
    const t = themes.find(x => x.id === id) || domainThemes.find(x => x.id === id);
    if (!t) return null;
    const r = rng(t.repos * 7 + t.countries), base = t.new_12m / 12;
    const months = monthsBack(24).map((m, i) => ({ month: m, new: Math.max(0, Math.round(base * Math.pow(1 + t.growth_12m / 100, (i - 23) / 12) * (0.7 + r() * .6))) }));
    const byCountry = COUNTRIES.slice(0, 6).map((c, i) => ({ country: c, repos: Math.round(t.repos * [.3, .2, .12, .08, .06, .04][i]) }));
    const adopters = COUNTRIES.slice(0, 8).map((c, i) => ({ owner: c.toLowerCase().replace(/ /g, "-") + "-gov", country: c, first_repo: iso(new Date(NOW - (700 - i * 80) * 864e5)) }));
    const related = themes.filter(x => x.id !== t.id && x.type === "tag").slice(0, 5).map((x, i) => ({ ...x, shared: 20 - i * 3, share: 18 - i * 3 }));
    const mine = REPOS.filter(x => t.type === "tag" ? x._tag === t.id : x.domain === t.id);
    return { ...t, model: null, as_of: iso(NOW), monthly_new: months, by_country: byCountry, adopters, related, top_repos: mine, newest_repos: mine };
  }

  function repo(owner, name) {
    const r = REPOS.find(x => x.full_name.toLowerCase() === `${owner}/${name}`.toLowerCase());
    if (!r) return null;
    const t = themes.find(x => x.id === r._tag);
    return { ...r, open_issues: 12, size_kb: 4000, default_branch: "main", fork_source: null, llm_tags: [],
      classification: { model: "sample", domain: r.domain, domain_name: DOMAINS[r.domain], domain_probability: .86, kind: r.kind, tags: [{ id: t.id, probability: .93 }] } };
  }

  function context(owner, name) {
    const r = repo(owner, name);
    if (!r) return null;
    const rr = rng(r.stars), history = [];
    let s = r.stars * .6, f = r.forks * .6;
    for (let w = 25; w >= 0; w--) { s += (r.stars - s) * (.08 + rr() * .06); f += (r.forks - f) * (.08 + rr() * .06); history.push({ date: iso(new Date(NOW - w * 7 * 864e5)).slice(0, 10), stars: Math.round(s), forks: Math.round(f) }); }
    return { full_name: r.full_name, history, reused_by: [{ full_name: "example-gov/" + name, owner: "example-gov", country: "Canada", created_at: iso(new Date(NOW - 20 * 864e5)) }],
      similar: REPOS.filter(x => x.full_name !== r.full_name && x.domain === r.domain).slice(0, 4) };
  }

  function repos(p) {
    let items = REPOS.slice();
    const q = (p.get("q") || "").toLowerCase();
    if (q) items = items.filter(r => (r.full_name + " " + r.description + " " + r.topics.join(" ")).toLowerCase().includes(q));
    if (p.get("country")) items = items.filter(r => r.country.toLowerCase() === p.get("country").toLowerCase());
    if (p.get("domain")) items = items.filter(r => r.domain === p.get("domain"));
    if (p.get("tag")) items = items.filter(r => r._tag === p.get("tag"));
    const sort = p.get("sort") || "stars";
    items.sort(sort === "newest" ? (a, b) => b.created_at.localeCompare(a.created_at) : sort === "recently_pushed" ? (a, b) => b.pushed_at.localeCompare(a.pushed_at) : sort === "name" ? (a, b) => a.full_name.localeCompare(b.full_name) : (a, b) => b.stars - a.stars);
    const limit = +p.get("limit") || 20, offset = +p.get("offset") || 0;
    return { total: items.length, limit, offset, items: items.slice(offset, offset + limit) };
  }

  window.OGI_SAMPLE = {
    overview: {
      as_of: iso(NOW),
      totals: { repos: 21480, original: 17320, maintained: 9612, accounts: 1083, countries: 52, new_7d: 148, new_30d: 590, licensed_share: 69 },
      status: { new_12m: 2214, active: 9612, dormant: 6120, archived: 1588 },
      timeline: total,
      countries: [["United States", 3352, 63], ["United Kingdom", 1699, 68], ["France", 720, 91], ["Canada", 574, 72], ["Australia", 380, 70], ["Brazil", 336, 52]]
        .map(([country, maintained, licensed_share]) => ({ country, maintained, licensed_share, accounts: 0 })),
      newest: REPOS.slice().sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 6),
      classification: { model: null, taxonomy_version: 1, classified_original_repos: 0, coverage: 0 },
    },
    themes: { model: "sample", taxonomy_version: 1, growth_definition: "", themes: domainThemes.concat(themes) },
    theme, repo, context, repos,
    countries: COUNTRIES.map(country => ({ country })),
  };
})();
