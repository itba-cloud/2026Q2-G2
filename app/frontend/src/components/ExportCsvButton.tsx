/* The one CSV button, so four pages do not grow four of them.
 *
 * It exports what is already in memory and nothing else -- no extra request,
 * no "fetch every page first". What that means per page is on the page: the
 * whole result set where the page holds it, the current page where paging is
 * server-side. The button says which, so nobody has to guess whether they got
 * 50 rows or 8000. */
import type { ReactNode } from "react";
import { Button } from "./ui";
import { csvFilename, downloadCsv, toCsv } from "../lib/csv";

export function ExportCsvButton<T extends object>({
  rows,
  columns,
  filenameStem,
  feedDate,
  label = "Exportar CSV",
  title,
}: {
  rows: readonly T[];
  /** Column order in the file. Anything not listed is left out, so adding a
   * field to the API response does not silently change the export. */
  columns: string[];
  filenameStem: string;
  feedDate?: string;
  label?: ReactNode;
  /** Says what the file will contain. Worth setting wherever "what is on
   * screen" and "the whole set" are not the same thing. */
  title?: string;
}) {
  return (
    <Button
      size="sm"
      disabled={rows.length === 0}
      title={title}
      onClick={() => downloadCsv(csvFilename(filenameStem, feedDate), toCsv(rows, columns))}
    >
      <span aria-hidden="true">↓</span>
      {label}
    </Button>
  );
}
