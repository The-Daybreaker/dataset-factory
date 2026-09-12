// 前端入口：把 React 应用挂到 index.html 的 #app 上。
//
// 样式在这里用 import 引入（而不是 html 里的 <link>）：Vite 会把它们纳入构建图、
// 生产构建时一并打包与压缩，不用手工维护资源路径。
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
// globals.css = 新 UI 底座（Tailwind + 设计令牌）；style.css = 旧四页签的过渡样式，
// 页面按新信息架构重构完成后移除。
import "./globals.css";
import "./style.css";

const container = document.getElementById("app");
if (container === null) {
  throw new Error("找不到挂载点 #app；请检查 index.html 是否正确。");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
