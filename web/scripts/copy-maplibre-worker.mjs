// MapLibre 6 loads its web worker relative to its own module URL, which a
// bundler rewrites. Serve the worker from /public instead (see MapView.tsx).
import { copyFileSync, mkdirSync } from "node:fs";

mkdirSync("public/maplibre", { recursive: true });
copyFileSync("node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs", "public/maplibre/maplibre-gl-worker.mjs");
console.log("copied maplibre-gl-worker.mjs to public/maplibre/");
