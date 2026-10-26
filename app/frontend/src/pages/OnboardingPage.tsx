import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { errorMessage } from "../components/ErrorNotice";
import { AuthShell, Button, Field, Input } from "../components/ui";

/* The first login of a user with no org: it names the org and becomes its
 * owner. Until then the API answers 403 to everything but GET /auth/me. */
export function OnboardingPage() {
  const { user, createOrg, logout } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await createOrg(name);
      navigate("/", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthShell
      title="Creá tu organización"
      description={`Entraste como ${user?.email ?? ""}. Falta el nombre de tu organización: vas a quedar como su dueño.`}
      footer={
        <button type="button" onClick={logout} className="rounded font-medium text-brand-ink hover:underline">
          Usar otra cuenta
        </button>
      }
    >
      <form onSubmit={handleSubmit} className="mt-5 space-y-4">
        <Field label="Nombre de la organización" hint="Como querés que aparezca en el portal.">
          {({ id, describedBy }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              type="text"
              required
              minLength={2}
              maxLength={120}
              autoFocus
              autoComplete="organization"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="min-h-11"
            />
          )}
        </Field>

        {error && (
          <p role="alert" className="rounded-lg bg-[#fdeceb] px-3 py-2 text-[15px] text-critical">
            {error}
          </p>
        )}

        <Button type="submit" variant="primary" disabled={isSubmitting} className="w-full">
          {isSubmitting ? "Creando…" : "Crear organización"}
        </Button>
      </form>
    </AuthShell>
  );
}
