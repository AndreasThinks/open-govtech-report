/* Dependency-free behavioral harness. Browser/layout checks: tests/site_browser.py. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const site = path.join(__dirname, '../src/govtech_scraper/site');

async function setup(mode = 'classified') {
  const nodes = new Map();
  const node = () => ({innerHTML: '', hidden: true, style: {}, children: [],
    append(...n) {this.children.push(...n);}, setAttribute() {}, addEventListener() {},
    querySelector() {return null;}, querySelectorAll() {return [];}, classList: {toggle() {}}});
  const sandbox = {URLSearchParams, console, Set, Date, Math, location: {hash: ''},
    document: {getElementById(id) {if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id);},
      createElementNS: node, querySelectorAll: () => [], documentElement: {}, addEventListener() {}},
    getComputedStyle: () => ({getPropertyValue: () => '#000'}),
    addEventListener() {}, matchMedia: () => ({addEventListener() {}})};
  sandbox.window = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(site, 'sample.js'), 'utf8'), ctx);
  const sample = sandbox.OGI_SAMPLE;
  const liveRepo = {...sample.repo('i-dot-ai', 'consult'), stars: 321, forks: 17};
  const response = {missing: false, contextError: false, empty: false};
  sandbox.fetch = async url => {
    if (mode === 'offline') throw Error('offline');
    let body, status = 200;
    if (url === '/v1/overview') body = sample.overview;
    else if (url === '/v1/themes') body = mode === 'unclassified' ? {model: null, themes: []} : sample.themes;
    else if (url === '/v1/countries') body = sample.countries;
    else if (url.startsWith('/v1/repos?')) body = {total: 1, items: [liveRepo]};
    else if (url.endsWith('/context')) {
      status = response.contextError ? 503 : 200;
      body = response.empty ? {history: [], reused_by: [], similar: []} : sample.context('i-dot-ai', 'consult');
    } else {status = response.missing ? 404 : 200; body = liveRepo;}
    return {ok: status === 200, status, json: async () => body};
  };
  const source = fs.readFileSync(path.join(site, 'app.js'), 'utf8');
  assert.ok(source.includes('boot().then(route);'));
  vm.runInContext(source.replace('boot().then(route);',
    'window.harness = {boot, data, pageRepo, pageRepos, pageTheme, pageAbout, pageOverview, isEmerging, risers, cooling, oneTeam, rising, paceText, pageSubmit};'), ctx);
  await sandbox.harness.boot();
  return {...sandbox.harness, response, nodes, html: () => nodes.get('app').innerHTML};
}

test('offline boot labels sample details', async () => {
  const h = await setup('offline'); await h.pageRepo('i-dot-ai', 'consult');
  assert.match(h.nodes.get('banner').innerHTML, /every figure on this site is an example/);
  assert.match(h.html(), /i-dot-ai\/consult/);
});
test('live 404 does not invent sample repository', async () => {
  const h = await setup(); h.response.missing = true; await h.pageRepo('i-dot-ai', 'consult');
  assert.match(h.html(), /Not found/); assert.doesNotMatch(h.html(), /example-gov|88<\/b>/);
});
test('context failure is unavailable and navigation recovers', async () => {
  const h = await setup(); h.response.contextError = true; await h.pageRepo('i-dot-ai', 'consult');
  for (const text of ['History unavailable', 'Government forks unavailable', 'Similar repositories unavailable']) assert.ok(h.html().includes(text), text);
  assert.match(h.html(), />Unavailable<\/b><small>government forks/);
  assert.doesNotMatch(h.html(), /example-gov|No government forks yet/);
  h.response.contextError = false; await h.pageRepos(''); await h.pageRepo('i-dot-ai', 'consult');
  assert.doesNotMatch(h.html(), /unavailable/); assert.match(h.html(), /321/);
});
for (const filter of ['domain=00', 'tag=ai-and-machine-learning', 'kind=tool']) {
  test(`unclassified filtered URL ${filter} cannot show sample results`, async () => {
    const h = await setup('unclassified'); await h.pageRepos(filter);
    assert.match(h.html(), /Classification filters are unavailable/); assert.doesNotMatch(h.html(), /<table/);
    assert.match(h.html(), /select name="domain" disabled/); assert.match(h.html(), /select name="tag" disabled/);
    await h.pageRepos(''); assert.match(h.html(), /321/); assert.doesNotMatch(h.html(), /Classification filters are unavailable/);
  });
}
test('sample theme snippets are explicitly examples and missing linked repos stay absent', async () => {
  const h = await setup('unclassified'); await h.pageTheme('00');
  assert.match(h.html(), /Example repositories/);
  h.response.missing = true; await h.pageRepo('i-dot-ai', 'consult'); assert.match(h.html(), /Not found/);
});
test('healthy classified details and genuinely empty context', async () => {
  const h = await setup(); await h.pageRepo('i-dot-ai', 'consult');
  assert.match(h.html(), /321/); assert.match(h.html(), /class="prob"/);
  assert.equal(h.nodes.get('banner').hidden, true);
  h.response.empty = true; await h.pageRepo('i-dot-ai', 'consult');
  assert.match(h.html(), /No government forks yet/); assert.doesNotMatch(h.html(), /unavailable/);
});
test('about replaces method, with contact and sponsor ask', async () => {
  const h = await setup(); h.pageAbout();
  assert.match(h.html(), /About the index/);
  assert.match(h.html(), /andreasthinks\.me/);
  assert.match(h.html(), /contact@andreasthinks\.me/);
  assert.match(h.html(), /Sponsor the index/);
  assert.match(h.nodes.get('sponsor').innerHTML, /Interested in sponsoring it\?/);
});
test('momentum lists follow the API status, not size', async () => {
  const h = await setup();
  assert.ok(h.risers().length && h.risers().every(t => t.status === 'emerging'));
  assert.ok(h.risers().some(t => t.repos > 200));
  assert.ok(h.cooling().every(t => t.status === 'cooling' && t.pace < 1));
  assert.ok(h.oneTeam().every(t => t.status === 'concentrated'));
  assert.deepEqual(new Set(h.rising().map(t => t.status)), new Set(h.oneTeam().length ? ['emerging', 'concentrated'] : ['emerging']));
  assert.equal(h.paceText(1.55), '+55%'); assert.equal(h.paceText(0.7), '−30%');
  assert.equal(h.paceText(1.2, true), '20% faster than the index');
  h.pageOverview();
  assert.match(h.html(), /Leading emerging theme/);
  assert.match(h.html(), /<h3>Growing<\/h3>/);
  assert.doesNotMatch(h.html(), /One team busy/);
  if (h.oneTeam().length) assert.match(h.html(), /Mostly [^:<]+: \d+ of \d+/);
  assert.match(h.html(), /Spreading, or one team\?/);
  assert.match(h.html(), /Show as a table/);
  assert.doesNotMatch(h.html(), /growth_12m|Fastest-growing/);
});
