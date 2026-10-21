import { Circle, MapContainer, Marker, Popup, TileLayer } from "react-leaflet";
import { formatDistance, toNumber } from "../../lib/format";
import type { Competitor } from "../../types";
import { competitorIcon, DISTANCE_LEGEND, ownBranchIcon } from "./icons";

interface Props {
  ownName: string;
  ownLatitude: string | number;
  ownLongitude: string | number;
  competitors: Competitor[];
  /** Drawn as the catchment circle, so the radius control has something
   * visible to move. */
  radiusKm?: number;
}

export function CompetitorsMap({
  ownName,
  ownLatitude,
  ownLongitude,
  competitors,
  radiusKm,
}: Props) {
  const lat = toNumber(ownLatitude);
  const lon = toNumber(ownLongitude);

  if (Number.isNaN(lat) || Number.isNaN(lon)) {
    return (
      <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-axis bg-plane p-6 text-center text-sm text-ink-2">
        La sucursal no tiene coordenadas cargadas, así que no se puede ubicar en el mapa.
      </div>
    );
  }

  const maxDistance = competitors.reduce((max, c) => Math.max(max, c.distance_m), 0);

  return (
    <div className="relative h-full">
      <MapContainer
        key={`${lat}-${lon}`}
        center={[lat, lon]}
        zoom={14}
        scrollWheelZoom={false}
        className="h-full w-full rounded-lg"
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        {radiusKm !== undefined && radiusKm > 0 && (
          <Circle
            center={[lat, lon]}
            radius={radiusKm * 1000}
            pathOptions={{ color: "#2a78d6", weight: 1.5, fillColor: "#2a78d6", fillOpacity: 0.06 }}
          />
        )}

        {competitors.map((c) => {
          const cLat = toNumber(c.latitude);
          const cLon = toNumber(c.longitude);
          if (Number.isNaN(cLat) || Number.isNaN(cLon)) return null;
          return (
            <Marker
              key={`${c.company_id}-${c.banner_id}-${c.branch_id}`}
              position={[cLat, cLon]}
              icon={competitorIcon(c.distance_m, maxDistance)}
            >
              <Popup>
                <strong>{c.banner_name}</strong>
                <br />
                {c.name}
                <br />
                {c.locality}, {c.province}
                <br />
                <span style={{ color: "#52514e" }}>a {formatDistance(c.distance_m)}</span>
              </Popup>
            </Marker>
          );
        })}

        {/* Drawn last so it sits above the competitors it overlaps. */}
        <Marker position={[lat, lon]} icon={ownBranchIcon()} zIndexOffset={1000}>
          <Popup>
            <strong>{ownName}</strong>
            <br />
            Tu sucursal
          </Popup>
        </Marker>
      </MapContainer>

      {/* Two marks on the map, two entries in the legend: identity is never
          left to colour alone. */}
      <div className="pointer-events-none absolute bottom-3 left-3 z-[400] rounded-lg border border-line bg-surface/95 px-2.5 py-2 text-[11px] shadow-sm backdrop-blur">
        <div className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rounded-full ring-2 ring-surface" style={{ background: "#eb6834" }} />
          <span className="text-ink-2">Tu sucursal</span>
        </div>
        <div className="mt-1.5 flex items-center gap-1.5">
          <span className="text-muted">{DISTANCE_LEGEND[0].label}</span>
          <span aria-hidden="true" className="flex gap-0.5">
            {DISTANCE_LEGEND.map((s) => (
              <span key={s.color} className="size-2.5 rounded-full" style={{ background: s.color }} />
            ))}
          </span>
          <span className="text-muted">{DISTANCE_LEGEND[DISTANCE_LEGEND.length - 1].label}</span>
        </div>
      </div>
    </div>
  );
}
