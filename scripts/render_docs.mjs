#!/usr/bin/env node
// Render every page of a built Antora site at desktop (1280px) and mobile
// (390px) widths and record per-page layout facts to report.json. This is
// the toolchain-installed successor to the ad hoc render script used for the
// 2026-09-17 rendered-site review; see docs/MAINTAINING.md.
//
// Usage:
//   node scripts/render_docs.mjs [siteDir] [outDir] [--screenshots]
//   node scripts/render_docs.mjs --site=docs/build/site/agent-memory --out=docs/build/render-report
//
// siteDir defaults to docs/build/site/agent-memory (a fresh `make docs`
// build). outDir defaults to docs/build/render-report. The site is served
// from disk with a tiny built-in http server (no external dependency) rather
// than opened over file://, because the shared UI's relative asset and
// fragment links assume an http origin. Screenshots are skipped unless
// --screenshots is passed, since report.json is what the pytest check reads.
import { chromium } from '../docs/diagrams/node_modules/playwright/index.mjs';
import { readdirSync, readFileSync, statSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, relative, extname } from 'node:path';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function parseArgs(argv) {
  const positional = [];
  const flags = { screenshots: false };
  for (const arg of argv) {
    if (arg === '--screenshots') flags.screenshots = true;
    else if (arg.startsWith('--site=')) flags.site = arg.slice('--site='.length);
    else if (arg.startsWith('--out=')) flags.out = arg.slice('--out='.length);
    else positional.push(arg);
  }
  return { positional, flags };
}

const { positional, flags } = parseArgs(process.argv.slice(2));
const SITE = path.resolve(flags.site || positional[0] || path.join(root, 'docs/build/site/agent-memory'));
const OUT = path.resolve(flags.out || positional[1] || path.join(root, 'docs/build/render-report'));
const SCREENSHOTS = flags.screenshots;

if (!statSync(SITE, { throwIfNoEntry: false })?.isDirectory()) {
  throw new Error(`Site directory not found: ${SITE} (run \`make docs\` first, or pass a site directory)`);
}

mkdirSync(OUT, { recursive: true });
if (SCREENSHOTS) {
  mkdirSync(join(OUT, 'shots', 'desktop'), { recursive: true });
  mkdirSync(join(OUT, 'shots', 'mobile'), { recursive: true });
}

function walk(dir, acc = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const s = statSync(p);
    if (s.isDirectory()) {
      if (!['_attachments', '_images', '_'].includes(name)) walk(p, acc);
    } else if (name.endsWith('.html') && name !== '404.html') {
      acc.push(p);
    }
  }
  return acc;
}
const pages = walk(SITE).map(p => relative(SITE, p)).sort();
if (pages.length === 0) throw new Error(`No .html pages found under ${SITE}`);
const slug = p => p.replace(/\.html$/, '').replace(/[/\\]/g, '__');

// Antora mounts a component's pages under a URL segment (here /agent-memory/)
// while the shared UI's assets (css/js/fonts, the "_" directory) live one
// level up, as a sibling of that component directory. Pages reference those
// assets with relative paths computed against that mount point, so serving
// SITE in isolation 404s every shared asset. Serve the parent directory
// instead, with the component name as a URL prefix, whenever that sibling
// "_" directory is present; otherwise SITE is self-contained and is served
// at the root.
const parent = path.dirname(SITE);
const hasSharedUi = statSync(join(parent, '_'), { throwIfNoEntry: false })?.isDirectory();
const SERVE_ROOT = hasSharedUi ? parent : SITE;
const MOUNT = hasSharedUi ? `/${path.basename(SITE)}/` : '/';

// Minimal static file server for the built site directory. Free port (0),
// no external dependency, so `make docs-render-check` never fights another
// process for a fixed port.
const MIME = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript',
  '.mjs': 'text/javascript', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
  '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf',
  '.json': 'application/json', '.ico': 'image/x-icon',
};
const server = http.createServer((request, response) => {
  try {
    const url = new URL(request.url, 'http://localhost');
    let file = path.resolve(SERVE_ROOT, '.' + decodeURIComponent(url.pathname));
    if (file !== SERVE_ROOT && !file.startsWith(SERVE_ROOT + path.sep)) {
      response.writeHead(403).end();
      return;
    }
    let stat;
    try {
      stat = statSync(file);
    } catch {
      response.writeHead(404).end();
      return;
    }
    if (stat.isDirectory()) file = join(file, 'index.html');
    response.setHeader('Content-Type', MIME[extname(file)] || 'application/octet-stream');
    response.end(readFileSync(file));
  } catch (error) {
    response.writeHead(500).end(String(error));
  }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;
const BASE = `http://127.0.0.1:${port}${MOUNT}`;

// Evaluated in-page. Mirrors the per-page facts the 2026-09-17 review script
// captured, plus an explicit inline-TOC flag: the rendered defect classes
// this check exists for (inline TOC duplication, undersized images, mobile
// overflow, Title Case headings, leftover scaffolding headings) all read off
// these facts.
const PAGE_FACTS = `(() => {
  const art = document.querySelector('article') || document.querySelector('.doc') || document.body;
  const rect = art.getBoundingClientRect();
  const imgs = [...art.querySelectorAll('img')].map(i => {
    const r = i.getBoundingClientRect();
    return {
      src: i.getAttribute('src'), alt: i.getAttribute('alt'), widthAttr: i.getAttribute('width'),
      natural: [i.naturalWidth, i.naturalHeight], rendered: [Math.round(r.width), Math.round(r.height)],
      broken: i.complete && i.naturalWidth === 0,
      overflows: r.right > rect.right + 2 || r.width > rect.width + 2,
      inFigure: !!i.closest('.imageblock'),
      caption: i.closest('.imageblock')?.querySelector('.title')?.textContent?.trim() || null,
    };
  });
  const headings = [...art.querySelectorAll('h1,h2,h3,h4')].map(h => ({
    level: h.tagName, text: h.textContent.trim().replace(/\\s+/g, ' '),
  }));
  const tables = [...art.querySelectorAll('table')].map(t => {
    const r = t.getBoundingClientRect();
    return {
      cols: t.querySelectorAll('thead th, tr:first-child td, tr:first-child th').length,
      width: Math.round(r.width), overflows: r.width > rect.width + 2,
    };
  });
  const codes = [...art.querySelectorAll('pre')].map(p => ({
    lang: p.querySelector('code')?.className || '',
    lines: (p.textContent.match(/\\n/g) || []).length + 1,
    scrollX: p.scrollWidth > p.clientWidth + 2,
  }));
  const admon = art.querySelectorAll('.admonitionblock').length;
  const words = (art.innerText || '').split(/\\s+/).filter(Boolean).length;
  return {
    title: document.title,
    h1: document.querySelector('h1')?.textContent?.trim() || null,
    articleWidth: Math.round(rect.width),
    docScrollX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 2,
    bodyScrollWidth: document.documentElement.scrollWidth,
    pageHeight: document.documentElement.scrollHeight,
    hasInlineToc: !!document.getElementById('toc'),
    imgs, headings, tables, codes, admon, words,
    hasLabsDisclaimer: /Neo4j Labs project/.test(art.innerText || ''),
    hasEditLink: !!document.querySelector('a.edit-this-page, .edit-this-page a'),
    breadcrumbs: [...document.querySelectorAll('.breadcrumbs li')].map(l => l.textContent.trim()),
    navActive: document.querySelector('.nav-menu .is-current-page a, .nav-link.is-current')?.textContent?.trim() || null,
  };
})()`;

const browser = await chromium.launch();
const report = { pages: {}, generated: new Date().toISOString(), site: SITE };

async function run(viewport, dir) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const errs = [];
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });
  page.on('pageerror', e => errs.push('pageerror: ' + String(e).slice(0, 200)));
  page.on('response', r => { if (r.status() >= 400 && r.url().startsWith(BASE)) errs.push(`http ${r.status()} ${r.url().replace(BASE, '')}`); });
  for (const p of pages) {
    errs.length = 0;
    const url = BASE + p;
    try {
      await page.goto(url, { waitUntil: 'load', timeout: 30000 });
      await page.evaluate(() => document.fonts.ready);
      await page.waitForTimeout(80);
    } catch (e) {
      (report.pages[p] ||= {})[dir] = { error: String(e).slice(0, 200) };
      continue;
    }
    const facts = await page.evaluate(PAGE_FACTS);
    facts.errors = [...errs];
    if (SCREENSHOTS) {
      const s = slug(p);
      try {
        await page.screenshot({ path: join(OUT, 'shots', dir, `${s}.png`), fullPage: dir === 'desktop' });
      } catch (e) {
        facts.screenshotError = String(e).slice(0, 200);
      }
    }
    (report.pages[p] ||= {})[dir] = facts;
  }
  await ctx.close();
}

await run({ width: 1280, height: 900 }, 'desktop');
await run({ width: 390, height: 844 }, 'mobile');
await browser.close();
await new Promise(resolve => server.close(resolve));

writeFileSync(join(OUT, 'report.json'), JSON.stringify(report, null, 1));
console.log(`rendered ${pages.length} pages into ${join(OUT, 'report.json')}`);
