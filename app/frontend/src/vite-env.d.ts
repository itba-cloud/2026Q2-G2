/// <reference types="vite/client" />

/* `vite/client` types `import.meta.env` with an index signature, so an unknown
 * VITE_* name reads as `any` rather than as an error -- a typo in one would
 * type-check and then be undefined at runtime. Declaring the ones this app
 * actually reads makes them `string` and makes anything else a mistake. */
interface ImportMetaEnv {
  /** Read by vite.config.ts, not by the app: where the dev server proxies
   * /api/* to. Declared here so the two names stay documented in one place. */
  readonly VITE_API_PROXY_TARGET: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
