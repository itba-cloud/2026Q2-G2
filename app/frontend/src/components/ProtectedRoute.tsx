import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Spinner } from "./Spinner";

export function ProtectedRoute({
  children,
  requireOwner = false,
  allowWithoutOrg = false,
}: {
  children: ReactNode;
  /* Only the onboarding: everywhere else a user with no org is sent there,
   * since the API refuses it everything until it names one. */
  allowWithoutOrg?: boolean;
  /* Hiding the link was never the same as protecting the route: anyone could
   * type /pipeline. The backend refuses the writes either way, but a member
   * should not land on a screen that is not theirs. */
  requireOwner?: boolean;
}) {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center bg-plane">
        <Spinner label="Verificando sesión…" />
      </div>
    );
  }

  if (!user) {
    // LoginPage reads state.from to send you back where you were headed --
    // without it, every bounce to /login lands back on the dashboard instead.
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }

  if (!allowWithoutOrg && user.org_id == null) {
    return <Navigate to="/onboarding" replace />;
  }

  if (requireOwner && user.role !== "owner") {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
}
