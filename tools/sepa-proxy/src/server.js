import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createApp, DEFAULT_FALLBACK_DIR } from "./app.js";

const port = Number.parseInt(process.env.PORT ?? "3000", 10);
const host = process.env.HOST ?? "0.0.0.0";

if (!Number.isInteger(port) || port < 1 || port > 65_535) {
  throw new Error("PORT debe ser un número entre 1 y 65535.");
}

const fallbackDisabled = ["true", "1"].includes(
  (process.env.SEPA_FALLBACK_DISABLED ?? "").trim().toLowerCase()
);

const appOptions = {};
if (fallbackDisabled) {
  appOptions.fallbackDir = null;
} else if (process.env.SEPA_FALLBACK_DIR) {
  appOptions.fallbackDir = process.env.SEPA_FALLBACK_DIR;
}

const app = createApp(appOptions);

if (!fallbackDisabled) {
  const fallbackPath = appOptions.fallbackDir ?? fileURLToPath(DEFAULT_FALLBACK_DIR);
  if (!existsSync(fallbackPath)) {
    console.warn(
      `Aviso: el directorio de respaldo local "${fallbackPath}" no existe. Si SEPA no responde, el proxy devolverá 502/504/503 sin poder servir un ZIP local.`
    );
  }
}

app.listen(port, host, () => {
  console.log(`SEPA prices proxy escuchando en http://${host}:${port}`);
});
