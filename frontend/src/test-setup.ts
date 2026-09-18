// Vitest 的测试环境准备：
// 1. 装上 jest-dom 的断言扩展（toBeInTheDocument 等）——所有测试共用；
// 2. 补 jsdom 缺失的浏览器 API（matchMedia——主题钩子的「跟随系统」判定要用）；
// 3. 每个测试结束后卸载已渲染的组件并清空 body。Testing Library 的自动清理只在
//    「测试框架以 globals 模式运行」时生效，我们的 vitest 配置没有开 globals，
//    不手动 cleanup 的话上一条测试的 DOM 会残留，导致查询匹配到多个元素。
//
// 纯逻辑测试（api 层、条目状态机、拖拽解析、CSS 令牌）改走 node 环境以省掉 jsdom 建场
// 开销（每个 jsdom 约 218ms × 26 文件），此时没有 window / HTMLElement——DOM 侧的准备
// 只在看得到浏览器的环境里做。
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import "@testing-library/jest-dom/vitest";

const hasDom = typeof window !== "undefined";

// jsdom 没实现 matchMedia：给主题用的极简桩（返回不匹配 + 可记录监听器即可）。
if (hasDom && typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// Radix Select uses pointer capture and scrolling APIs absent from jsdom.
const domStubs: Record<string, () => unknown> = hasDom
  ? {
      hasPointerCapture: (): boolean => false,
      setPointerCapture: (): void => {},
      releasePointerCapture: (): void => {},
      scrollIntoView: (): void => {},
    }
  : {};

for (const [name, value] of Object.entries(domStubs)) {
  if (!(name in HTMLElement.prototype)) {
    Object.defineProperty(HTMLElement.prototype, name, { configurable: true, value });
  }
}

afterEach(() => {
  if (hasDom) cleanup();
});
