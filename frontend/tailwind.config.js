/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        page: "var(--page)",
        surface: "var(--surface-1)",
        raised: "var(--surface-2)",
        hover: "var(--surface-3)",
        line: "var(--line)",
        ink: "var(--text-primary)",
        dim: "var(--text-secondary)",
        mute: "var(--text-muted)",
        brand: "var(--brand)",
        "brand-ink": "var(--brand-ink)",
        accent: "var(--series-1)",
        good: "var(--good)",
        warn: "var(--warning)",
        bad: "var(--critical)",
      },
      fontFamily: {
        sans: ['system-ui', '-apple-system', '"Segoe UI"', "sans-serif"],
      },
    },
  },
  plugins: [],
};
