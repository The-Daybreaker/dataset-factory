/**
 * 「一个测试文件一份独立服务 + 独立数据根」的端口表。
 *
 * 为什么需要：Playwright 在 workers=1 时会把多个测试文件塞进同一个 worker 顺序跑
 * （官方文档原话是 worker 会尽量复用），于是它们先后撞在同一份数据根上——前一个文件
 * 建的提示词、登记的工作目录、留下的锁，都变成了后一个文件的地基。症状是「单独跑必绿、
 * 连跑偶发红」。给每个文件一份自己的服务与数据根，这条因果链就断了。
 *
 * 端口 = 基准端口 + 在名单里的序号，所以结构上不会撞号；名单里每加一个文件，
 * playwright.config.ts 会自动为它多起一份服务，这里不用再改别处。
 *
 * 打桩的 spec（visual-baseline / perf-probe / labeling-layout）不发真后端请求，
 * 不进这份名单：它们共用 8765 那个只托管前端静态文件的服务。
 */

/** 名单里第一个文件的端口，其余按序号顺延。 */
const BASE_PORT = 8801;

/** 需要独立服务与数据根的 spec 文件，按此顺序分配端口。 */
export const ISOLATED_SPEC_FILES = [
  "smoke.spec.ts",
  "run-control.spec.ts",
  "workdir-maintenance.spec.ts",
  "rebuild-imports.spec.ts",
  "strategy-workbench.spec.ts",
  "skill-editing.spec.ts",
  "page-state.spec.ts",
] as const;

/** 名单里的文件名联合类型：名字写错在类型检查阶段就会红。 */
export type IsolatedSpecFile = (typeof ISOLATED_SPEC_FILES)[number];

/**
 * 取某个 spec 文件的专属服务端口。
 *
 * @param file 名单里的 spec 文件名。
 * @returns 该文件专属的监听端口。
 */
export function isolatedPort(file: IsolatedSpecFile): number {
  const index = ISOLATED_SPEC_FILES.indexOf(file);
  if (index < 0) {
    throw new Error(`未在隔离名单里登记：${file}`);
  }
  return BASE_PORT + index;
}

/**
 * 取某个 spec 文件的专属服务地址，写进该文件的 `test.use({ baseURL })`。
 *
 * @param file 名单里的 spec 文件名。
 * @returns 形如 `http://127.0.0.1:8801` 的基地址。
 */
export function isolatedBaseURL(file: IsolatedSpecFile): string {
  return `http://127.0.0.1:${isolatedPort(file)}`;
}

/**
 * 取某个 spec 文件自己的假模型端点地址。
 *
 * 打标请求必须指向自己的服务：假端点带「等待点」这类跨请求状态，指向别人就等于把
 * 自己的运行控制权交给了另一个文件。
 *
 * @param file 名单里的 spec 文件名。
 * @returns 形如 `http://127.0.0.1:8801/fake-llm/v1` 的端点地址。
 */
export function isolatedFakeLlmURL(file: IsolatedSpecFile): string {
  return `${isolatedBaseURL(file)}/fake-llm/v1`;
}
