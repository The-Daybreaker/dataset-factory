/** 界面显示用的数值换算（渲染前最后一道格式化，不参与任何计算）。 */

/**
 * 字节数 → 带单位显示串（如 `1.5 KiB`、`2.00 MiB`）。
 *
 * 单位和位数由调用点指定并原样保留：各屏既有约定不同（清单条目按 KiB 一位小数、
 * 体积合计按 MiB 两位小数），把它们统一成一种会改到已验收的显示值。舍入沿用
 * `toFixed`，与收成助手之前的写法逐字一致。
 */
export function formatBytes(
  bytes: number,
  unit: "KiB" | "MiB",
  digits: number,
): string {
  const divisor = unit === "KiB" ? 1024 : 1024 ** 2;
  return `${(bytes / divisor).toFixed(digits)} ${unit}`;
}

/** 自动升档：< 1 KiB 给整数 B，< 1 MiB 给 KiB 一位小数，再往上给 MiB 一位小数。 */
export function formatBytesAuto(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 ** 2) {
    return formatBytes(bytes, "KiB", 1);
  }
  return formatBytes(bytes, "MiB", 1);
}
