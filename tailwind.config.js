/**
 * Tailwind build for the static frontend (replaces the cdn.tailwindcss.com runtime compiler).
 * Version is pinned to 3.4.17, the release the CDN served when this file was created, so the generated CSS matches.
 * Build: npm run build:css   ->   frontend/tailwind.css (committed; Firebase serves it as a static file)
 */
module.exports = {
  content: ['./frontend/**/*.html', './frontend/**/*.js'],
  // Class names that JS assembles at runtime (e.g. `bg-${tone}-50`) never appear whole in the source, so the
  // scanner cannot see them. List them here.
  safelist: [
    // backtest.js: Adaptive Buy/Sell Ladder rows (color = emerald | purple)
    ...['emerald', 'purple'].flatMap(c => [
      `border-${c}-100`, `bg-${c}-50/40`, `text-${c}-800`, `accent-${c}-500`, `text-${c}-600`, `border-${c}-200`, `text-${c}-900`,
    ]),
    // research-shared.js: evidence tiles (tone = violet)
    'border-violet-100', 'bg-violet-50/60', 'text-violet-600',
    // screener.js: alert cards (color = rose | emerald | slate)
    ...['rose', 'emerald', 'slate'].flatMap(c => [`border-${c}-200`, `bg-${c}-50/50`, `text-${c}-700`]),
    // index.html: sign-in status line (tone = cyan | amber | rose)
    'text-cyan-300', 'text-amber-300', 'text-rose-300',
  ],
};
