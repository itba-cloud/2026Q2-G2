import assert from "node:assert/strict";
import { once } from "node:events";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { createApp, SEPA_PACKAGE_URL } from "../src/app.js";

const resources = [
  {
    name: "Lunes",
    format: "ZIP",
    last_modified: "2026-09-07T16:20:53.458556",
    url: "https://datos.produccion.gob.ar/download/sepa_lunes.zip"
  },
  {
    name: "Domingo",
    format: "ZIP",
    last_modified: "2026-09-06T16:19:07.577936",
    url: "https://datos.produccion.gob.ar/download/sepa_domingo.zip"
  }
];

function mockFetch(url) {
  if (url === SEPA_PACKAGE_URL) {
    return Promise.resolve(
      new Response(JSON.stringify({ success: true, result: { resources } }), {
        status: 200,
        headers: { "content-type": "application/json" }
      })
    );
  }

  if (url === resources[0].url) {
    return Promise.resolve(
      new Response(new Uint8Array([80, 75, 3, 4]), {
        status: 200,
        headers: {
          "content-type": "application/zip",
          "content-length": "4",
          etag: "zip-etag"
        }
      })
    );
  }

  return Promise.resolve(new Response(null, { status: 404 }));
}

async function withServer(callback, fetchImpl = mockFetch, options = {}) {
  // fallbackDir: null por defecto para que los tests no toquen sepa_files/ real
  // (330 MB por archivo); los tests de respaldo lo pisan explícitamente.
  const app = createApp({ fetchImpl, connectTimeoutMs: 30_000, fallbackDir: null, ...options });
  app.listen(0, "127.0.0.1");
  await once(app, "listening");

  try {
    const { port } = app.address();
    await callback(`http://127.0.0.1:${port}`);
  } finally {
    app.close();
    await once(app, "close");
  }
}

function fakeZipBuffer(dateFolder) {
  const name = Buffer.from(`${dateFolder}/`, "utf8");
  const header = Buffer.alloc(30);
  header.writeUInt32LE(0x04034b50, 0); // firma de local file header ZIP
  header.writeUInt16LE(name.length, 26); // largo del nombre de la entrada
  return Buffer.concat([header, name]);
}

async function withFallbackDir(entries, fn) {
  const dir = await mkdtemp(join(tmpdir(), "sepa-fallback-"));
  try {
    for (const [day, dateFolder] of Object.entries(entries)) {
      await writeFile(join(dir, `sepa_${day}.zip`), fakeZipBuffer(dateFolder));
    }
    await fn(pathToFileURL(`${dir}/`));
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

function alwaysFailFetch() {
  return Promise.reject(new Error("red caída"));
}

function badZipStatusFetch(url, options) {
  if (url === SEPA_PACKAGE_URL) return mockFetch(url, options);
  if (url === resources[0].url) {
    return Promise.resolve(new Response(null, { status: 500 }));
  }
  return Promise.resolve(new Response(null, { status: 404 }));
}

function midStreamErrorFetch(url, options) {
  if (url === SEPA_PACKAGE_URL) return mockFetch(url, options);
  if (url !== resources[0].url) {
    return Promise.resolve(new Response(null, { status: 404 }));
  }

  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array([80, 75, 3, 4]));
      setTimeout(() => controller.error(new Error("corte de red a mitad del ZIP")), 10);
    }
  });

  return Promise.resolve(
    new Response(body, { status: 200, headers: { "content-type": "application/zip" } })
  );
}

function slowZipFetch(url, options) {
  if (url === SEPA_PACKAGE_URL) return mockFetch(url, options);

  if (url !== resources[0].url) {
    return Promise.resolve(new Response(null, { status: 404 }));
  }

  let timer;
  const body = new ReadableStream({
    start(controller) {
      timer = setTimeout(() => {
        controller.enqueue(new Uint8Array([80, 75, 3, 4]));
        controller.close();
      }, 50);
      options.signal.addEventListener(
        "abort",
        () => {
          clearTimeout(timer);
          controller.error(options.signal.reason);
        },
        { once: true }
      );
    },
    cancel() {
      clearTimeout(timer);
    }
  });

  return Promise.resolve(
    new Response(body, {
      status: 200,
      headers: { "content-type": "application/zip" }
    })
  );
}

test("health check", async () => {
  await withServer(async (baseUrl) => {
    const response = await fetch(`${baseUrl}/health`);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { status: "ok" });
  });
});

test("entrega por streaming el ZIP más reciente", async () => {
  await withServer(async (baseUrl) => {
    const response = await fetch(`${baseUrl}/v1/sepa/precios`);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("content-type"), "application/zip");
    assert.equal(response.headers.get("content-disposition"), 'attachment; filename="sepa_lunes.zip"');
    assert.equal(response.headers.get("x-sepa-recurso"), "Lunes");
    assert.deepEqual([...new Uint8Array(await response.arrayBuffer())], [80, 75, 3, 4]);
  });
});

test("acepta día sin tilde", async () => {
  await withServer(async (baseUrl) => {
    const response = await fetch(`${baseUrl}/v1/sepa/precios?dia=lunes`);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("x-sepa-recurso"), "Lunes");
  });
});

test("el timeout termina al recibir headers, no durante el streaming del ZIP", async () => {
  const app = createApp({ fetchImpl: slowZipFetch, connectTimeoutMs: 10 });
  app.listen(0, "127.0.0.1");
  await once(app, "listening");

  try {
    const { port } = app.address();
    const response = await fetch(`http://127.0.0.1:${port}/v1/sepa/precios`);
    assert.equal(response.status, 200);
    assert.deepEqual([...new Uint8Array(await response.arrayBuffer())], [80, 75, 3, 4]);
  } finally {
    app.close();
    await once(app, "close");
  }
});

test("rechaza un día inválido antes de llamar a SEPA", async () => {
  let calls = 0;
  await withServer(
    async (baseUrl) => {
      const response = await fetch(`${baseUrl}/v1/sepa/precios?dia=feriado`);
      assert.equal(response.status, 400);
      assert.match((await response.json()).error, /parámetro dia/);
      assert.equal(calls, 0);
    },
    async () => {
      calls += 1;
      throw new Error("No debería llamar al upstream.");
    }
  );
});

test("rechaza un día con caracteres de path traversal sin tocar disco ni red", async () => {
  await withFallbackDir({}, async (fallbackDir) => {
    let calls = 0;
    await withServer(
      async (baseUrl) => {
        const response = await fetch(
          `${baseUrl}/v1/sepa/precios?dia=${encodeURIComponent("../../etc/passwd")}`
        );
        assert.equal(response.status, 400);
        assert.equal(calls, 0);
      },
      async () => {
        calls += 1;
        throw new Error("No debería llamar al upstream.");
      },
      { fallbackDir }
    );
  });
});

test("usa el respaldo local cuando SEPA no contesta", async () => {
  await withFallbackDir({ lunes: "2026-09-07" }, async (fallbackDir) => {
    await withServer(
      async (baseUrl) => {
        const response = await fetch(`${baseUrl}/v1/sepa/precios?dia=lunes`);
        assert.equal(response.status, 200);
        assert.equal(response.headers.get("x-sepa-origen"), "respaldo-local");
        assert.equal(response.headers.get("x-sepa-fecha-datos"), "2026-09-07");
        assert.equal(
          response.headers.get("content-disposition"),
          'attachment; filename="sepa_lunes.zip"'
        );

        const body = new Uint8Array(await response.arrayBuffer());
        const expected = fakeZipBuffer("2026-09-07");
        assert.equal(response.headers.get("content-length"), String(expected.length));
        assert.deepEqual([...body], [...expected]);
      },
      alwaysFailFetch,
      { fallbackDir }
    );
  });
});

test("latest en modo respaldo sirve el ZIP con la fecha de datos más nueva, no el día de hoy", async () => {
  await withFallbackDir(
    {
      miercoles: "2026-09-02",
      jueves: "2026-09-03",
      viernes: "2026-09-04",
      sabado: "2026-09-05",
      domingo: "2026-09-06",
      lunes: "2026-09-07",
      martes: "2026-09-08"
    },
    async (fallbackDir) => {
      await withServer(
        async (baseUrl) => {
          const response = await fetch(`${baseUrl}/v1/sepa/precios`);
          assert.equal(response.status, 200);
          assert.equal(response.headers.get("x-sepa-fecha-datos"), "2026-09-08");
          assert.equal(
            response.headers.get("content-disposition"),
            'attachment; filename="sepa_martes.zip"'
          );
        },
        alwaysFailFetch,
        { fallbackDir }
      );
    }
  );
});

test("responde 503 si no hay ningún ZIP de respaldo legible para latest", async () => {
  await withFallbackDir({}, async (fallbackDir) => {
    await withServer(
      async (baseUrl) => {
        const response = await fetch(`${baseUrl}/v1/sepa/precios`);
        assert.equal(response.status, 503);
        assert.match((await response.json()).error, /respaldo local/);
      },
      alwaysFailFetch,
      { fallbackDir }
    );
  });
});

test("responde 404 si el día está ausente del catálogo, aunque exista el ZIP local", async () => {
  await withFallbackDir({ martes: "2026-09-08" }, async (fallbackDir) => {
    await withServer(
      async (baseUrl) => {
        const response = await fetch(`${baseUrl}/v1/sepa/precios?dia=martes`);
        assert.equal(response.status, 404);
      },
      mockFetch,
      { fallbackDir }
    );
  });
});

test("sin respaldo configurado, un fallo de conexión sigue devolviendo 504", async () => {
  await withServer(
    async (baseUrl) => {
      const response = await fetch(`${baseUrl}/v1/sepa/precios`);
      assert.equal(response.status, 504);
    },
    alwaysFailFetch,
    { fallbackDir: null }
  );
});

test("cae al respaldo si SEPA contesta el catálogo pero falla al descargar el ZIP", async () => {
  await withFallbackDir({ lunes: "2026-09-07" }, async (fallbackDir) => {
    await withServer(
      async (baseUrl) => {
        const response = await fetch(`${baseUrl}/v1/sepa/precios?dia=lunes`);
        assert.equal(response.status, 200);
        assert.equal(response.headers.get("x-sepa-origen"), "respaldo-local");
      },
      badZipStatusFetch,
      { fallbackDir }
    );
  });
});

test("no cae al respaldo si el ZIP de SEPA se corta luego de enviar headers", async () => {
  await withFallbackDir({ lunes: "2026-09-07" }, async (fallbackDir) => {
    await withServer(
      async (baseUrl) => {
        const response = await fetch(`${baseUrl}/v1/sepa/precios?dia=lunes`);
        assert.equal(response.status, 200);
        assert.equal(response.headers.get("x-sepa-origen"), "sepa");
        await assert.rejects(() => response.arrayBuffer());
      },
      midStreamErrorFetch,
      { fallbackDir }
    );
  });
});
