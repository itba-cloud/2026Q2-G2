import L from "leaflet";

// react-leaflet's default marker icon resolves image paths relative to the
// bundler's asset pipeline, which breaks under Vite unless you re-point it at
// imported asset URLs. Simplest fix: skip the image icons entirely and draw
// markers as small styled <div>s -- which also lets distance be encoded on the
// marker itself.

const SURFACE = "#fcfcfb";
const OWN = "#eb6834";

// Distance is a continuous magnitude, so it gets a sequential ramp: one hue,
// light to dark, near = dark. The previous version swept the hue wheel from
// red to blue, which reads as four different categories rather than as one
// scale, and puts the least legible colours in the middle of the range.
const DISTANCE_RAMP = ["#184f95", "#2a78d6", "#5598e7", "#86b6ef", "#b7d3f6"];

/** The subscribed branch: the one mark that is not on the distance scale, so
 * it takes a different hue and a bigger radius rather than a darker step. */
export function ownBranchIcon(): L.DivIcon {
  return L.divIcon({
    className: "",
    html: `<div style="width:20px;height:20px;border-radius:9999px;background:${OWN};
      border:3px solid ${SURFACE};box-shadow:0 1px 4px rgba(11,11,11,0.35);"></div>`,
    iconSize: [20, 20],
    iconAnchor: [10, 10],
    // Keeps the branch above the competitor markers when they overlap.
    // (Leaflet sorts DivIcons by their pane order otherwise.)
  });
}

/** Competitors: darkest and largest nearby, fading outward. Size repeats the
 * encoding so distance does not rest on colour alone. */
export function competitorIcon(distanceM: number, maxDistanceM: number): L.DivIcon {
  const t = maxDistanceM > 0 ? Math.min(1, Math.max(0, distanceM / maxDistanceM)) : 0;
  const step = Math.min(DISTANCE_RAMP.length - 1, Math.floor(t * DISTANCE_RAMP.length));
  const size = Math.round(16 - t * 6);
  return L.divIcon({
    className: "",
    html: `<div style="width:${size}px;height:${size}px;border-radius:9999px;
      background:${DISTANCE_RAMP[step]};border:2px solid ${SURFACE};
      box-shadow:0 1px 3px rgba(11,11,11,0.25);"></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

/** The five steps and what they mean, for the map's own legend. */
export const DISTANCE_LEGEND = DISTANCE_RAMP.map((color, i) => ({
  color,
  label: i === 0 ? "más cerca" : i === DISTANCE_RAMP.length - 1 ? "más lejos" : "",
}));
