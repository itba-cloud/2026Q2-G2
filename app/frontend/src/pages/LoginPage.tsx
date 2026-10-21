import { useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { AuthShell, Button } from "../components/ui";

// What the callback says when a login does not complete (app/auth_callback).
const ERRORS: Record<string, string> = {
  missing_code: "Cognito no devolvió el código de acceso. Volvé a intentar.",
  invalid_grant: "El código de acceso venció o ya se usó. Volvé a ingresar.",
  cognito_unreachable: "No pudimos hablar con Cognito. Probá de nuevo en un rato.",
};

/* The passwords are Cognito's, not the portal's: this page only sends the
 * browser to the Hosted UI, to log in or to sign up. */
export function LoginPage() {
  const { redirectToLogin } = useAuth();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const error = new URLSearchParams(location.search).get("error");

  return (
    <AuthShell
      title="Iniciar sesión"
      description="Entrá o creá tu cuenta con Cognito. La primera vez te pedimos el nombre de tu organización."
    >
      <div className="mt-5 space-y-3">
        {error && (
          <p role="alert" className="rounded-lg bg-[#fdeceb] px-3 py-2 text-[15px] text-critical">
            {ERRORS[error] ?? `No se pudo iniciar sesión: ${error}`}
          </p>
        )}

        <Button
          type="button"
          variant="primary"
          className="w-full"
          onClick={() => redirectToLogin("login", from)}
        >
          Ingresar
        </Button>
        <Button
          type="button"
          variant="secondary"
          className="w-full"
          onClick={() => redirectToLogin("signup", from)}
        >
          Crear una cuenta
        </Button>
      </div>
    </AuthShell>
  );
}
