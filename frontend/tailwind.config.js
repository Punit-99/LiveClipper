/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        dark: {
          bg: '#0a0d14',
          card: '#121722',
          border: '#1f293d',
          hover: '#1a2233',
          input: '#0d131f',
        },
        brand: {
          blue: '#3b82f6',
          purple: '#9146ff',
          red: '#ff0033',
          cyan: '#06b6d4',
          amber: '#f59e0b',
          emerald: '#10b981',
        }
      }
    },
  },
  plugins: [],
}
