// What the portal needs to know about its stack: where the API and Cognito's
// Hosted UI are. Terraform writes it to config.json next to index.html
// (infra/modules/frontend), so the same build works in any account and
// nothing about a deploy is baked into the bundle. main.tsx loads it before
// the first render; everything else reads it with portalConfig().

export interface PortalConfig {
  /** The API's base URL; every call goes to `${apiUrl}/api/*`. */
  apiUrl: string;
  /** Base URL of the Hosted UI: /oauth2/authorize, /signup, /logout. */
  authUrl: string;
  /** The portal's Cognito client. Public: its secret is only in the callback. */
  clientId: string;
  /** The API's /auth/callback, where Cognito sends the code. */
  redirectUri: string;
  /** The portal itself: where Cognito sends the browser after a logout. */
  logoutUri: string;
}

let loaded: PortalConfig | null = null;

export async function loadConfig(): Promise<PortalConfig> {
  // Relative to the page: the portal is <bucket>/index.html, and so is its
  // config. no-store: a deploy can change it under the same name.
  const res = await fetch(new URL("config.json", window.location.href), { cache: "no-store" });
  if (!res.ok) throw new Error(`config.json: HTTP ${res.status}`);
  loaded = (await res.json()) as PortalConfig;
  return loaded;
}

export function portalConfig(): PortalConfig {
  if (!loaded) throw new Error("portalConfig() before loadConfig()");
  return loaded;
}
