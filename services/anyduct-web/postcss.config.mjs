// Tailwind v4 PostCSS plugin entry — see DESIGN.md §11.2.
// All tokens live in app/globals.css via the @theme block; no JS config file.
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
