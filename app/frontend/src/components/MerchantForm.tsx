import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { setMerchant } from "../api/endpoints";
import type { MerchantResponse } from "../types";
import { errorMessage } from "./ErrorNotice";
import { LocationPicker, type PickedLocation } from "./map/LocationPicker";
import { Button, Field, Input } from "./ui";

/* La sucursal propia de la org, para un comercio que no publica en el SEPA.
 * Su ubicación es lo que define su conjunto competitivo, igual que para una
 * sucursal del SEPA -- que es por qué esto tiene que existir antes de que una
 * carga sea aceptada.
 *
 * Vive acá y no dentro de una pantalla porque lo usan dos: la de carga, donde
 * es el paso que falta antes de subir el archivo, y la de cuenta, donde es
 * edición. La única diferencia entre las dos es el texto del botón, así que
 * es un parámetro y no un componente duplicado. */
export function MerchantForm({
  merchant,
  onSaved,
  submitLabel,
}: {
  merchant?: MerchantResponse | null;
  onSaved?: (saved: MerchantResponse) => void;
  submitLabel?: string;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(merchant?.name ?? "");
  const [province, setProvince] = useState(merchant?.province ?? "");
  const [locality, setLocality] = useState(merchant?.locality ?? "");
  const [location, setLocation] = useState<PickedLocation | null>(
    merchant?.latitude != null && merchant?.longitude != null
      ? { latitude: merchant.latitude, longitude: merchant.longitude }
      : null,
  );
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      setMerchant({
        name,
        province,
        locality,
        latitude: location!.latitude,
        longitude: location!.longitude,
      }),
    onSuccess: (saved) => {
      setError(null);
      setMessage("Sucursal guardada. Ya podés subir tu lista de precios.");
      // Dar de alta la sucursal propia escribe también en `org_branch`, así
      // que hay que invalidar las dos: la de la sucursal propia y la lista de
      // sucursales que alimenta el selector de la barra lateral. La segunda
      // usa la clave ["me", "branches"] -- una clave que no matchee no da
      // error, simplemente no refresca nada, y la sucursal recién creada no
      // aparece en el selector hasta recargar la página entera.
      queryClient.invalidateQueries({ queryKey: ["me", "merchant"] });
      queryClient.invalidateQueries({ queryKey: ["me", "branches"] });
      onSaved?.(saved);
    },
    onError: (err) => {
      setMessage(null);
      setError(errorMessage(err));
    },
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setMessage(null);
    if (!location) {
      setError("Marcá el comercio en el mapa: su ubicación es lo que define su competencia.");
      return;
    }
    setError(null);
    mutation.mutate();
  }

  return (
    <form onSubmit={handleSubmit}>
      <div className="space-y-5">
        <Field label="Nombre del comercio">
          {({ id }) => (
            <Input
              id={id}
              required
              minLength={2}
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="min-h-11"
            />
          )}
        </Field>

        {/* La API quiere un punto, pero nadie debería tener que tipearlo: el
            picker lo saca del mapa. Localidad y Provincia se tipean -- no
            quedó geocodificador del que derivarlas de una dirección. */}
        <LocationPicker value={location} onChange={setLocation} />

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Localidad" hint="Como figura en la dirección del local.">
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                required
                value={locality}
                onChange={(e) => setLocality(e.target.value)}
                className="min-h-11"
              />
            )}
          </Field>
          <Field label="Provincia" hint="Sin abreviar, ej. Buenos Aires.">
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                required
                value={province}
                onChange={(e) => setProvince(e.target.value)}
                className="min-h-11"
              />
            )}
          </Field>
        </div>
      </div>

      {message && (
        <p role="status" className="mt-3 rounded-lg bg-[#eaf7ea] px-3 py-2 text-[15px] text-good-ink">
          {message}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 rounded-lg bg-[#fdeceb] px-3 py-2 text-[15px] text-critical">
          {error}
        </p>
      )}

      <Button type="submit" variant="primary" disabled={mutation.isPending} className="mt-4">
        {mutation.isPending ? "Guardando…" : (submitLabel ?? "Guardar sucursal")}
      </Button>
    </form>
  );
}
