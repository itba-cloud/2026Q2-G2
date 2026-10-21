import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { clearToken, getToken, setToken, setUnauthorizedHandler } from "../api/client";
import { createOrg as apiCreateOrg, getMe } from "../api/endpoints";
import { portalConfig } from "../config";
import { ACTIVE_BRANCH_STORAGE_KEY } from "../hooks/useActiveBranch";
import type { User } from "../types";

// The login is Cognito's Hosted UI. The portal sends the browser there with a
// random `state`, Cognito sends it to the API's /auth/callback with a code,
// and the callback trades the code for the tokens and comes back to
// #/auth/callback with the ID token and the same state (AuthCallbackPage).
// The state is how the portal knows the login it lands from is one it
// started; `from` is where to go once it does.
const LOGIN_STATE_KEY = "lgdc_login_state";
const LOGIN_FROM_KEY = "lgdc_login_from";

export type HostedPage = "login" | "signup";

interface AuthContextValue {
  user: User | null;
  isLoading: boolean;
  /** To the Hosted UI, to log in or sign up; back at `from` afterwards. */
  redirectToLogin: (page: HostedPage, from?: string) => void;
  /** The callback's landing: keeps the token if the state is ours. Returns where to go. */
  completeLogin: (idToken: string, state: string) => Promise<string>;
  /** The first login's onboarding. */
  createOrg: (name: string) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

// A Hosted UI page, with its query.
function hostedUi(path: string, params: Record<string, string>): string {
  return `${portalConfig().authUrl}${path}?${new URLSearchParams(params)}`;
}

function randomState(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const navigate = useNavigate();
  const location = useLocation();
  // The 401 handler below is registered once and fires from a fetch callback,
  // not from a render, so it cannot close over `location` directly -- it would
  // always see the path from the moment the app mounted. A ref kept current on
  // every navigation is what lets a session that expires mid-use send you back
  // to the page you were on, the same way ProtectedRoute does for a cold visit.
  const locationRef = useRef(location);
  useEffect(() => {
    locationRef.current = location;
  }, [location]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser(null);
      const { pathname, search } = locationRef.current;
      navigate("/login", { replace: true, state: { from: pathname + search } });
    });
    return () => setUnauthorizedHandler(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setIsLoading(false);
      return;
    }
    getMe()
      .then(setUser)
      .catch(() => {
        clearToken();
        setUser(null);
      })
      .finally(() => setIsLoading(false));
  }, []);

  function redirectToLogin(page: HostedPage, from = "/") {
    const { clientId, redirectUri } = portalConfig();
    const state = randomState();
    sessionStorage.setItem(LOGIN_STATE_KEY, state);
    sessionStorage.setItem(LOGIN_FROM_KEY, from);
    const path = page === "signup" ? "/signup" : "/oauth2/authorize";
    window.location.assign(hostedUi(path, {
      response_type: "code",
      client_id: clientId,
      redirect_uri: redirectUri,
      scope: "openid email profile",
      state,
    }));
  }

  async function completeLogin(idToken: string, state: string): Promise<string> {
    const expected = sessionStorage.getItem(LOGIN_STATE_KEY);
    const from = sessionStorage.getItem(LOGIN_FROM_KEY) ?? "/";
    sessionStorage.removeItem(LOGIN_STATE_KEY);
    sessionStorage.removeItem(LOGIN_FROM_KEY);
    if (!expected || state !== expected) {
      throw new Error("El inicio de sesión no salió de este navegador. Volvé a ingresar.");
    }
    setToken(idToken);
    // The first GET /auth/me creates the user, with no org yet.
    setUser(await getMe());
    return from;
  }

  async function createOrg(name: string) {
    setUser(await apiCreateOrg({ name }));
  }

  function logout() {
    clearToken();
    localStorage.removeItem(ACTIVE_BRANCH_STORAGE_KEY);
    setUser(null);
    // Cognito's session too: without it, "Ingresar" would log the same user
    // straight back in.
    const { clientId, logoutUri } = portalConfig();
    window.location.assign(hostedUi("/logout", { client_id: clientId, logout_uri: logoutUri }));
  }

  async function refreshUser() {
    setUser(await getMe());
  }

  return (
    <AuthContext.Provider
      value={{ user, isLoading, redirectToLogin, completeLogin, createOrg, logout, refreshUser }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
