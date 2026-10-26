import { MapContainer, Marker, TileLayer, useMapEvents } from "react-leaflet";
import { ownBranchIcon } from "./icons";

export interface PickedLocation {
  latitude: number;
  longitude: number;
}

/* Nobody knows their shop's latitude, but everybody recognises their own
 * corner on a map -- so the form asks for the point directly instead of
 * asking for coordinates and hoping.
 *
 * There is no address search: the geocoder this used to proxy was the only
 * reason the backend needed a route to the internet, and marking the corner
 * by hand is the more accurate of the two anyway. Geocoders land on the
 * street centroid or the wrong side of a wide avenue often enough that the
 * marker had to stay draggable regardless, and the radius is measured from
 * this point. */
export function LocationPicker({
  value,
  onChange,
}: {
  value: PickedLocation | null;
  onChange: (next: PickedLocation) => void;
}) {
  return (
    <div className="space-y-3">
      <div>
        <p className="block text-[13px] font-medium text-ink-2">Ubicación del comercio</p>
        <p className="mt-1 text-[13px] text-muted">
          Tocá tu local en el mapa para marcarlo. Acercá el mapa antes de marcar: el punto define
          qué comercios cuentan como tu competencia.
        </p>
      </div>

      <div className="h-72 overflow-hidden rounded-xl border border-line">
        <MapContainer
          center={[value?.latitude ?? -34.6037, value?.longitude ?? -58.3816]}
          zoom={value ? 15 : 12}
          scrollWheelZoom={false}
          className="h-full w-full"
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <ClickToPlace onPlace={(lat, lon) => onChange({ latitude: lat, longitude: lon })} />
          {value && (
            <Marker
              position={[value.latitude, value.longitude]}
              icon={ownBranchIcon()}
              draggable
              eventHandlers={{
                dragend: (e) => {
                  const { lat, lng } = e.target.getLatLng();
                  onChange({ latitude: lat, longitude: lng });
                },
              }}
            />
          )}
        </MapContainer>
      </div>

      <p className="tnum text-[13px] text-muted" role="status" aria-live="polite">
        {value
          ? `Punto elegido: ${value.latitude.toFixed(5)}, ${value.longitude.toFixed(5)}. Arrastrá el marcador para ajustarlo.`
          : "Todavía no elegiste un punto. Tocá el mapa donde está tu local."}
      </p>
    </div>
  );
}

function ClickToPlace({ onPlace }: { onPlace: (lat: number, lon: number) => void }) {
  useMapEvents({
    click: (e) => onPlace(e.latlng.lat, e.latlng.lng),
  });
  return null;
}
