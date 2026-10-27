// Used by `npm run build:css` (tailwindcss --postcss). Autoprefixer matches what the old CDN script did at runtime,
// e.g. -webkit-backdrop-filter, which iOS Safari before 18 needs for the glass blur.
module.exports = {
  plugins: {
    autoprefixer: {},
  },
};
