import { createServer as createHttpServer } from "node:http";
import { open } from "node:fs/promises";
import { resolve } from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { fileURLToPath, pathToFileURL } from "node:url";

export const SEPA_PACKAGE_URL =
  "https://datos.produccion.gob.ar/api/3/action/package_show?id=sepa-precios";

const DAYS = new Set([
  "lunes",
  "martes",
  "miercoles",
  "jueves",
  "viernes",
  "sabado",
  "domingo"
]);
const DEFAULT_CONNECT_TIMEOUT_MS = 30_000;
export const DEFAULT_FALLBACK_DIR = new URL("../sepa_files/", import.meta.url);
const ZIP_LOCAL_FILE_HEADER_SIGNATURE = 0x04034b50;

class HttpError extends Error {
  constructor(statusCode, message) {
    super(message);
    this.statusCode = statusCode;
  }
}

function normalizeDay(value) {
  return value
    .trim()
    .toLocaleLowerCase("es-AR")
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "");
}

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff"
  });
  response.end(JSON.stringify(payload));
}

function logEvent(event, data) {
  console.error(JSON.stringify({ event, ...data }));
}

function setCorsHeaders(response) {
  response.setHeader("access-control-allow-origin", "*");
  response.setHeader("access-control-allow-methods", "GET, OPTIONS");
}

function assertValidPackage(payload) {
  if (!payload?.success || !Array.isArray(payload?.result?.resources)) {
    throw new HttpError(502, "SEPA devolvió un catálogo inválido.");
  }

  return payload.result.resources.filter(
    (resource) =>
      typeof resource?.url === "string" &&
      resource.format?.toUpperCase() === "ZIP" &&
      resource.url.startsWith("https://datos.produccion.gob.ar/")
  );
}

function selectResource(resources, requestedDay) {
  if (requestedDay !== "latest") {
    const found = resources.find((resource) => {
      const name = normalizeDay(String(resource.name ?? ""));
      const fileName = normalizeDay(
        new URL(resource.url).pathname.split("/").at(-1) ?? ""
      );
      return name === requestedDay || fileName.includes(`sepa_${requestedDay}`);
    });

    if (!found) {
      throw new HttpError(404, `No hay un ZIP de SEPA para ${requestedDay}.`);
    }

    return found;
  }

  return resources.reduce((newest, resource) => {
    const newestDate = Date.parse(newest.last_modified) || 0;
    const resourceDate = Date.parse(resource.last_modified) || 0;
    return resourceDate > newestDate ? resource : newest;
  });
}

function zipFileName(resource) {
  const rawName = decodeURIComponent(
    new URL(resource.url).pathname.split("/").at(-1) ?? "sepa-precios.zip"
  );
  const safeName = rawName.replace(/[^a-zA-Z0-9._-]/g, "_");
  return safeName.endsWith(".zip") ? safeName : "sepa-precios.zip";
}

function normalizeFallbackDir(fallbackDir) {
  if (!fallbackDir) return null;
  if (fallbackDir instanceof URL) return fallbackDir;
  return pathToFileURL(`${resolve(fallbackDir)}/`);
}

// Los ZIP de respaldo tienen una única carpeta raíz "YYYY-MM-DD/": leemos el
// local file header del primer entry (ZIP local, sin dependencias) en vez de
// confiar en el mtime del archivo, que solo refleja cuándo se copió acá.
async function readZipDataDate(path) {
  let fh;
  try {
    fh = await open(path, "r");
    const buffer = Buffer.alloc(512);
    const { bytesRead } = await fh.read(buffer, 0, buffer.length, 0);
    if (bytesRead < 30 || buffer.readUInt32LE(0) !== ZIP_LOCAL_FILE_HEADER_SIGNATURE) {
      return null;
    }

    const nameLength = buffer.readUInt16LE(26);
    if (bytesRead < 30 + nameLength) return null;

    const name = buffer.toString("utf8", 30, 30 + nameLength);
    const match = /^(\d{4}-\d{2}-\d{2})\//.exec(name);
    return match ? match[1] : null;
  } catch {
    return null;
  } finally {
    await fh?.close();
  }
}

async function resolveFallbackDay(fallbackBase, requestedDay) {
  if (requestedDay !== "latest") return requestedDay;

  const candidates = await Promise.all(
    [...DAYS].map(async (day) => ({
      day,
      date: await readZipDataDate(fileURLToPath(new URL(`sepa_${day}.zip`, fallbackBase)))
    }))
  );

  const newest = candidates
    .filter((candidate) => candidate.date)
    .reduce((best, candidate) => (!best || candidate.date > best.date ? candidate : best), null);

  if (!newest) {
    logEvent("sepa_fallback_missing", { dia: "latest" });
    throw new HttpError(503, "No hay respaldo local disponible.");
  }

  return newest.day;
}

function resolveFallbackPath(fallbackBase, day) {
  if (!DAYS.has(day)) {
    throw new HttpError(500, "Día de respaldo inválido.");
  }

  return fileURLToPath(new URL(`sepa_${day}.zip`, fallbackBase));
}

async function fetchWithHeadersTimeout(fetchImpl, url, options, connectTimeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), connectTimeoutMs);

  try {
    // fetch() se resuelve al recibir status y headers. Al limpiar el timer acá,
    // un ZIP grande puede seguir transmitiéndose sin quedar abortado a los 30 s.
    return await fetchImpl(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

async function fetchPackage(fetchImpl, packageUrl, connectTimeoutMs) {
  let response;
  try {
    response = await fetchWithHeadersTimeout(
      fetchImpl,
      packageUrl,
      { headers: { accept: "application/json" } },
      connectTimeoutMs
    );
  } catch {
    throw new HttpError(504, "No se pudo contactar a SEPA.");
  }

  if (!response.ok) {
    throw new HttpError(502, `SEPA respondió ${response.status} al consultar el catálogo.`);
  }

  try {
    return await response.json();
  } catch {
    throw new HttpError(502, "SEPA devolvió un catálogo que no es JSON válido.");
  }
}

async function streamZip({
  request,
  response,
  resource,
  fetchImpl,
  connectTimeoutMs
}) {
  let upstream;
  try {
    upstream = await fetchWithHeadersTimeout(
      fetchImpl,
      resource.url,
      { headers: { accept: "application/zip" } },
      connectTimeoutMs
    );
  } catch {
    throw new HttpError(504, "No se pudo descargar el ZIP desde SEPA.");
  }

  if (!upstream.ok || !upstream.body) {
    throw new HttpError(502, `SEPA respondió ${upstream.status} al descargar el ZIP.`);
  }

  const headers = {
    "content-type": upstream.headers.get("content-type") ?? "application/zip",
    "content-disposition": `attachment; filename="${zipFileName(resource)}"`,
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "x-sepa-origen": "sepa",
    "x-sepa-recurso": String(resource.name ?? "ZIP de precios SEPA")
  };

  for (const header of ["content-length", "etag", "last-modified"]) {
    const value = upstream.headers.get(header);
    if (value) headers[header] = value;
  }

  response.writeHead(200, headers);
  const stream = Readable.fromWeb(upstream.body);
  request.once("aborted", () => stream.destroy());
  await pipeline(stream, response);
}

async function streamLocalZip({
  request,
  response,
  fallbackBase,
  day,
  upstreamStatus,
  upstreamError
}) {
  const path = resolveFallbackPath(fallbackBase, day);

  let fh;
  try {
    fh = await open(path, "r");
  } catch {
    logEvent("sepa_fallback_missing", { dia: day, upstreamStatus, upstreamError });
    throw new HttpError(503, `No hay respaldo local para ${day}.`);
  }

  try {
    const stats = await fh.stat();
    if (!stats.isFile()) {
      logEvent("sepa_fallback_missing", { dia: day, upstreamStatus, upstreamError });
      throw new HttpError(503, `No hay respaldo local para ${day}.`);
    }

    const dataDate = await readZipDataDate(path);
    const antiguedadDias = dataDate
      ? Math.floor((Date.now() - Date.parse(`${dataDate}T00:00:00Z`)) / 86_400_000)
      : null;

    logEvent("sepa_fallback_served", {
      dia: day,
      fechaDatos: dataDate,
      bytes: stats.size,
      antiguedadDias,
      upstreamStatus,
      upstreamError
    });
    if (antiguedadDias !== null && antiguedadDias > 1) {
      logEvent("sepa_fallback_stale", { dia: day, fechaDatos: dataDate, antiguedadDias });
    }

    const headers = {
      "content-type": "application/zip",
      "content-disposition": `attachment; filename="sepa_${day}.zip"`,
      "content-length": String(stats.size),
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "x-sepa-origen": "respaldo-local",
      "x-sepa-recurso": `Respaldo local ${day}`,
      "x-sepa-fecha-datos": dataDate ?? "desconocida"
    };

    response.writeHead(200, headers);
    const stream = fh.createReadStream();
    request.once("aborted", () => stream.destroy());
    await pipeline(stream, response);
  } finally {
    await fh.close();
  }
}

export function createApp({
  fetchImpl = fetch,
  packageUrl = SEPA_PACKAGE_URL,
  connectTimeoutMs = DEFAULT_CONNECT_TIMEOUT_MS,
  fallbackDir = DEFAULT_FALLBACK_DIR
} = {}) {
  const fallbackBase = normalizeFallbackDir(fallbackDir);

  return createHttpServer(async (request, response) => {
    setCorsHeaders(response);

    if (request.method === "OPTIONS") {
      response.writeHead(204);
      response.end();
      return;
    }

    if (request.method !== "GET") {
      sendJson(response, 405, { error: "Método no permitido." });
      return;
    }

    const url = new URL(request.url, "http://localhost");

    try {
      if (url.pathname === "/health") {
        sendJson(response, 200, { status: "ok" });
        return;
      }

      if (url.pathname !== "/v1/sepa/precios") {
        sendJson(response, 404, { error: "Endpoint no encontrado." });
        return;
      }

      const requestedDay = normalizeDay(url.searchParams.get("dia") ?? "latest");
      if (requestedDay !== "latest" && !DAYS.has(requestedDay)) {
        throw new HttpError(
          400,
          "El parámetro dia debe ser latest, lunes, martes, miercoles, jueves, viernes, sabado o domingo."
        );
      }

      try {
        const payload = await fetchPackage(fetchImpl, packageUrl, connectTimeoutMs);
        const resources = assertValidPackage(payload);
        if (resources.length === 0) {
          throw new HttpError(502, "SEPA no publicó ZIPs de precios en el catálogo.");
        }

        const resource = selectResource(resources, requestedDay);
        await streamZip({
          request,
          response,
          resource,
          fetchImpl,
          connectTimeoutMs
        });
      } catch (upstreamError) {
        const canUseFallback =
          fallbackBase &&
          upstreamError instanceof HttpError &&
          (upstreamError.statusCode === 502 || upstreamError.statusCode === 504) &&
          !response.headersSent;

        if (!canUseFallback) throw upstreamError;

        const day = await resolveFallbackDay(fallbackBase, requestedDay);
        await streamLocalZip({
          request,
          response,
          fallbackBase,
          day,
          upstreamStatus: upstreamError.statusCode,
          upstreamError: upstreamError.message
        });
      }
    } catch (error) {
      if (response.headersSent) {
        response.destroy(error);
        return;
      }

      const statusCode = error instanceof HttpError ? error.statusCode : 500;
      const message =
        error instanceof HttpError ? error.message : "Error interno del proxy.";
      sendJson(response, statusCode, { error: message });
    }
  });
}
