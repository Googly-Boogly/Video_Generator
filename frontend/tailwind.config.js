/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0b0f17",
        panel: "#121826",
        panel2: "#1a2234",
        edge: "#243049",
        accent: "#f59e0b",
        accent2: "#0ea5e9",
      },
    },
  },
  plugins: [],
};
