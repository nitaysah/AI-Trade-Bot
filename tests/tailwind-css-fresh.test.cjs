const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const root = path.join(__dirname, '..');

test('frontend/tailwind.css is up to date with the HTML and JS (run `npm run build:css` after adding Tailwind classes)', () => {
    const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'tw-')), 'tailwind.css');
    execFileSync(path.join(root, 'node_modules/.bin/tailwindcss'),
        ['-c', 'tailwind.config.js', '-i', 'tailwind.input.css', '-o', out, '--postcss', '--minify'],
        { cwd: root, stdio: 'pipe' });
    const committed = fs.readFileSync(path.join(root, 'frontend/tailwind.css'), 'utf8');
    assert.ok(fs.readFileSync(out, 'utf8') === committed, 'frontend/tailwind.css is stale: run `npm run build:css`');
});

test('no page loads the Tailwind CDN runtime, and every page loads the built CSS last in <head>', () => {
    for (const page of ['dashboard', 'screener', 'bots', 'backtest', 'strategy', 'index']) {
        const html = fs.readFileSync(path.join(root, 'frontend', `${page}.html`), 'utf8');
        assert.doesNotMatch(html, /cdn\.tailwindcss\.com/, page);
        // last in <head>, where the CDN injected its <style>, so Tailwind still wins ties against styles.css
        assert.match(html, /<link rel="stylesheet" href="tailwind\.css\?v=\d+">\s*<\/head>/, page);
    }
});

test('dynamic class names that JS assembles at runtime are present in the build', () => {
    const css = fs.readFileSync(path.join(root, 'frontend/tailwind.css'), 'utf8');
    const esc = (c) => c.replace(/[/.:[\]]/g, (m) => `\\${m}`);
    for (const cls of require(path.join(root, 'tailwind.config.js')).safelist) {
        assert.ok(css.includes(`.${esc(cls)}{`) || css.includes(`.${esc(cls)}:`), `missing ${cls}`);
    }
});

test('Safari glass blur keeps its -webkit- prefix', () => {
    const css = fs.readFileSync(path.join(root, 'frontend/tailwind.css'), 'utf8');
    assert.match(css, /-webkit-backdrop-filter:/);
});
