import { Link } from "react-router-dom";
import { Button } from "../components/ui";

export function NotFoundPage() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
      <p className="text-5xl font-semibold tracking-tight text-ink">404</p>
      <p className="max-w-sm text-[15px] text-ink-2">
        Esta página no existe. Puede que el enlace esté viejo o que la sucursal ya no esté en el
        feed.
      </p>
      <Link to="/" className="mt-1">
        <Button variant="primary">Volver al tablero</Button>
      </Link>
    </div>
  );
}
