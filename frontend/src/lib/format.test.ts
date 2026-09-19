import { describe, expect, it } from "vitest";
import { formatBytes, formatBytesAuto } from "./format";

/** 收成助手之前各调用点写的原式（照搬过来作对照，不是再实现一遍给人读）。 */
function legacyKiB(bytes: number): string {
  return `${(bytes / 1024).toFixed(1)} KiB`;
}
function legacyMiB2(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(2)} MiB`;
}
function legacyAuto(bytes: number): string {
  return bytes < 1024
    ? `${bytes} B`
    : bytes < 1024 ** 2
      ? `${(bytes / 1024).toFixed(1)} KiB`
      : `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
}

const SAMPLES: number[] = [
  0, 1, 512, 1023, 1024, 1536, 10_747, 60_336, 1_048_575, 1_048_576, 3_145_728,
  104_857_600,
];

describe("字节显示格式化", () => {
  it("KiB 一位小数与收成助手前的原式逐字一致", () => {
    for (const bytes of SAMPLES) {
      expect(formatBytes(bytes, "KiB", 1)).toBe(legacyKiB(bytes));
    }
  });

  it("MiB 两位小数与收成助手前的原式逐字一致", () => {
    for (const bytes of SAMPLES) {
      expect(formatBytes(bytes, "MiB", 2)).toBe(legacyMiB2(bytes));
    }
  });

  it("自动升档在三个量级上的取值与边界", () => {
    for (const bytes of SAMPLES) {
      expect(formatBytesAuto(bytes)).toBe(legacyAuto(bytes));
    }
    expect([
      formatBytesAuto(1023),
      formatBytesAuto(1024),
      formatBytesAuto(1_048_575),
      formatBytesAuto(1_048_576),
    ]).toEqual(["1023 B", "1.0 KiB", "1024.0 KiB", "1.0 MiB"]);
  });

  it("位数与单位都由调用点决定，不在助手内改写", () => {
    expect([
      formatBytes(1_048_576, "KiB", 1),
      formatBytes(1_048_576, "MiB", 1),
      formatBytes(1_048_576, "MiB", 2),
    ]).toEqual(["1024.0 KiB", "1.0 MiB", "1.00 MiB"]);
  });
});
