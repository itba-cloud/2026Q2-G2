import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { errorMessage } from "../components/ErrorNotice";
import { Spinner } from "../components/Spinner";
import { AuthShell } from "../components/ui";

/* Where the API's /auth/callback sends the browser after trading Cognito's
 * code: #/auth/callback?id_token=...&state=... The token is in the fragment,
 * which the browser never sends to S3, and it leaves the address bar as soon
 * as this page has kept it. */
export function AuthCallbackPage() {
  const { completeLogin } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  // StrictMode runs effects twice in development; the state is single use.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const params = new URLSearchParams(location.search);
    const idToken = params.get("id_token");
    if (!idToken) {
      setError("No llegó el token de Cognito.");
      return;
    }
    completeLogin(idToken, params.get("state") ?? "")
      .then((from) => navigate(from, { replace: true }))
      .catch((err) => {
        setError(errorMessage(err));
        navigate("/auth/callback", { replace: true });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!error) {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center bg-plane">
        <Spinner label="Iniciando sesión…" />
      </div>
    );
  }

  return (
    <AuthShell title="No se pudo iniciar sesión" description={error}>
      <p className="mt-5 text-[15px]">
        <Link to="/login" replace className="rounded font-medium text-brand-ink hover:underline">
          Volver a ingresar
        </Link>
      </p>
    </AuthShell>
  );
}
