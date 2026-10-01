#!/usr/bin/env node
// Export editable Excalidraw scenes using the actual renderer, on a white
// background that remains readable inside light and dark documentation chrome.
// Install optional tooling in docs/diagrams; see docs/MAINTAINING.md.
import { createRequire } from 'node:module';
import { readFile, writeFile, mkdtemp, rm, mkdir } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const toolsDir = process.env.DIAGRAM_TOOLS_DIR || path.join(root, 'docs/diagrams');
const require = createRequire(path.join(toolsDir, 'package.json'));
const { build } = require('esbuild');
const { chromium } = process.env.PLAYWRIGHT_MODULE_PATH ? require(process.env.PLAYWRIGHT_MODULE_PATH) : require('playwright');
const renderer = path.dirname(require.resolve('@excalidraw/excalidraw'));
// Only files the manifest tracks may land in images/diagrams: an untracked file
// fails the orphan check in scripts/manage_diagrams.py. So the SVG is always
// written, and its sibling PNG only with --png or when the manifest tracks it.
const manifest = JSON.parse(await readFile(path.join(root, 'docs/diagrams/manifest.json'), 'utf8'));
const exportable = record => record.status !== 'unresolved-provenance';
const trackedPngs = new Set(manifest.diagrams.filter(record => record.output.endsWith('.png') && exportable(record))
  .map(record => path.join(root, record.output)));
const args = process.argv.slice(2);
const forcePng = args.includes('--png');
const positional = args.filter(value => value !== '--png');
let jobs;
if (positional.length === 1 && positional[0] === '--all') {
  // Re-export the SVGs the manifest records. PNG records and records whose
  // provenance is unresolved keep their committed files.
  jobs = manifest.diagrams.filter(record => record.source && record.output.endsWith('.svg') && exportable(record))
    .map(record => [path.join(root, record.source), path.join(root, record.output)]);
} else {
  if (positional.length !== 2) {
    throw new Error('Usage: node scripts/export_diagrams.mjs SOURCE.excalidraw OUTPUT.svg [--png] (or --all)');
  }
  jobs = [positional.map(value => path.resolve(value))];
}
if (jobs.some(([, output]) => !output.endsWith('.svg'))) throw new Error('Use an .svg output path.');
const pngFor = output => {
  const png = output.replace(/\.svg$/, '.png');
  return forcePng || trackedPngs.has(png) ? png : null;
};
const directory = await mkdtemp(path.join(os.tmpdir(), 'agent-memory-excalidraw-'));
let browser;
let server;
try {
  await build({
    stdin: {
      contents: `import {exportToSvg, exportToBlob, restoreElements} from '@excalidraw/excalidraw';
        window.render = async scene => {
          const options = {elements: restoreElements(scene.elements, null), files: scene.files || {},
            appState: {...scene.appState, exportBackground: true, exportWithDarkMode: false,
              viewBackgroundColor: '#ffffff'}, exportPadding: 24};
          const svg = await exportToSvg(options);
          const png = await exportToBlob({...options, mimeType: 'image/png'});
          const bytes = new Uint8Array(await png.arrayBuffer());
          let binary = ''; for (const byte of bytes) binary += String.fromCharCode(byte);
          return {svg: svg.outerHTML, png: btoa(binary)};
        };`,
      resolveDir: toolsDir,
    },
    bundle: true, format: 'iife', outfile: path.join(directory, 'bundle.js'),
    define: {'process.env.NODE_ENV': '"production"'},
    loader: {'.css': 'empty', '.woff2': 'file', '.ttf': 'file'},
  });
  const html = `<script>window.EXCALIDRAW_ASSET_PATH='/';</script><script src='/bundle.js'></script>`;
  server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://localhost');
      if (url.pathname === '/') { response.setHeader('Content-Type', 'text/html'); response.end(html); return; }
      const base = url.pathname === '/bundle.js' ? directory : renderer;
      const file = path.resolve(base, '.' + decodeURIComponent(url.pathname));
      if (!file.startsWith(base + path.sep)) { response.writeHead(403).end(); return; }
      response.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'application/octet-stream');
      response.end(await readFile(file));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  browser = await chromium.launch({headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH} : {})});
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForFunction(() => typeof window.render === 'function');
  for (const [source, output] of jobs) {
    const result = await page.evaluate(scene => window.render(scene), JSON.parse(await readFile(source, 'utf8')));
    await mkdir(path.dirname(output), {recursive: true});
    await writeFile(output, result.svg + '\n');
    const png = pngFor(output);
    if (png) await writeFile(png, Buffer.from(result.png, 'base64'));
    console.log(`Exported ${path.relative(root, output)}${png ? ' and PNG' : ''} from ${path.relative(root, source)}`);
  }
} finally {
  if (browser) await browser.close();
  if (server?.listening) await new Promise(resolve => server.close(resolve));
  await rm(directory, {recursive: true, force: true});
}
