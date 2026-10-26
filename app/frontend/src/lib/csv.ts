/* Turning a table on screen into a file you can open in a spreadsheet.
 *
 * The values go out exactly as the API sent them. NUMERIC fields arrive as
 * strings (see types.ts) and are written through unchanged -- deliberately
 * NOT via toNumber() or formatCurrency(): a CSV is for reopening somewhere
 * else, and "$ 1.234,56" is a string, not a number, to every spreadsheet
 * that reads it.
 *
 * Whether the spreadsheet reads "1234.56" as a number depends on the reader's
 * locale (decimal comma vs. point). That is the reader's setting, not
 * something this file can decide from here. */

/** RFC 4180: quote a field only when it contains a comma, a quote or a
 * newline, and double any quote inside it.
 *
 * `rows` is `readonly object[]` rather than `Record<string, unknown>[]` so the
 * API's own interfaces can be passed straight in -- none of them declares an
 * index signature, and adding one to each would turn off every typo check
 * TypeScript does on them elsewhere. `columns` is unchecked against the row
 * shape either way: ComparePage builds its column list at runtime from which
 * rivals are selected. */
export function toCsv(rows: readonly object[], columns: string[]): string {
  const escape = (value: unknown) => {
    const s = String(value ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const header = columns.map(escape).join(",");
  const body = rows.map((row) =>
    columns.map((c) => escape((row as Record<string, unknown>)[c])).join(","));
  return [header, ...body].join("\n");
}

/** Hand the browser a file it never fetched: the rows are already in memory,
 * so there is nothing to ask the API for. */
export function downloadCsv(filename: string, content: string): void {
  // The BOM is what makes Excel on Windows read the file as UTF-8 instead of
  // as the system code page -- without it every accented product name in the
  // catalogue comes out mojibake.
  const blob = new Blob(["\uFEFF", content], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** `mis-precios-2026-09-07.csv`: what the file is, and which feed it came
 * from, so two downloads a week apart do not collide in the same folder. */
export function csvFilename(stem: string, feedDate?: string): string {
  return feedDate ? `${stem}-${feedDate}.csv` : `${stem}.csv`;
}
