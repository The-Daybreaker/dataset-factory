export interface ImportReport {
  source: string;
  imported: string[];
  skipped_identical: string[];
  skipped_conflict: {
    name: string;
    existing_size: number;
    incoming_size: number;
    existing_sha256: string;
    incoming_sha256: string;
  }[];
  skipped_duplicate: { name: string; duplicate_of: string }[];
  rejected: { name: string; reason: string }[];
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("导入结果格式异常");
  return value as Record<string, unknown>;
}

function text(value: unknown): string {
  if (typeof value !== "string") throw new Error("导入结果格式异常");
  return value;
}

function bytes(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0)
    throw new Error("导入结果格式异常");
  return value;
}

function entries<T>(value: unknown, parse: (entry: unknown) => T): T[] {
  if (!Array.isArray(value)) throw new Error("导入结果格式异常");
  return value.map(parse);
}

/** 长任务结果在契约中为动态载荷，渲染前必须验证业务字段。 */
export function parseImportReport(value: unknown): ImportReport {
  const report = record(value);
  return {
    source: text(report.source),
    imported: entries(report.imported, text),
    skipped_identical: entries(report.skipped_identical, text),
    skipped_conflict: entries(report.skipped_conflict, (entry) => {
      const row = record(entry);
      return {
        name: text(row.name),
        existing_size: bytes(row.existing_size),
        incoming_size: bytes(row.incoming_size),
        existing_sha256: text(row.existing_sha256),
        incoming_sha256: text(row.incoming_sha256),
      };
    }),
    skipped_duplicate: entries(report.skipped_duplicate, (entry) => {
      const row = record(entry);
      return { name: text(row.name), duplicate_of: text(row.duplicate_of) };
    }),
    rejected: entries(report.rejected, (entry) => {
      const row = record(entry);
      return { name: text(row.name), reason: text(row.reason) };
    }),
  };
}

/** 恢复任务按来源返回多个报告，合并展示并保留逐条失败原因。 */
export function parseReimportReport(value: unknown): ImportReport {
  const result = record(value);
  const reports = entries(result.imports, parseImportReport);
  return {
    source: "",
    imported: reports.flatMap((report) => report.imported),
    skipped_identical: reports.flatMap((report) => report.skipped_identical),
    skipped_conflict: reports.flatMap((report) => report.skipped_conflict),
    skipped_duplicate: reports.flatMap((report) => report.skipped_duplicate),
    rejected: [
      ...reports.flatMap((report) => report.rejected),
      ...entries(result.unavailable, (entry) => {
        const row = record(entry);
        return { name: text(row.name), reason: text(row.reason) };
      }),
    ],
  };
}
