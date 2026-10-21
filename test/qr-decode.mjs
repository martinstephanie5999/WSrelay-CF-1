// Decodes the QR matrix exported by the page using jsqr (dev-only check).
// Usage: node qr-decode.mjs <path-to-jsqr-module-dir> <json-file>

import fs from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const jsQR = require(process.argv[2]);
const { matrix, link } = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

const n = matrix.length;
const quiet = 4;
const scale = 4;
const dim = (n + quiet * 2) * scale;
const data = new Uint8ClampedArray(dim * dim * 4);
for (let y = 0; y < dim; y++) {
  for (let x = 0; x < dim; x++) {
    const mx = Math.floor(x / scale) - quiet;
    const my = Math.floor(y / scale) - quiet;
    const dark = mx >= 0 && my >= 0 && mx < n && my < n && matrix[my][mx];
    const v = dark ? 0 : 255;
    const i = (y * dim + x) * 4;
    data[i] = v;
    data[i + 1] = v;
    data[i + 2] = v;
    data[i + 3] = 255;
  }
}
const res = jsQR(data, dim, dim);
if (!res) {
  console.error("no QR code found in matrix");
  process.exit(1);
}
if (res.data !== link) {
  console.error(`decoded mismatch: ${res.data} != ${link}`);
  process.exit(1);
}
console.log(res.data);
