// Vitest 的测试环境准备：
// 1. 装上 jest-dom 的断言扩展（toBeInTheDocument 等）——所有测试共用；
// 2. 每个测试结束后卸载已渲染的组件并清空 body。Testing Library 的自动清理只在
//    「测试框架以 globals 模式运行」时生效，我们的 vitest 配置没有开 globals，
//    不手动 cleanup 的话上一条测试的 DOM 会残留，导致查询匹配到多个元素。
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import "@testing-library/jest-dom/vitest";

afterEach(() => {
  cleanup();
});
