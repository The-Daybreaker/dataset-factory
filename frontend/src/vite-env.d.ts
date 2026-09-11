/// <reference types="vite/client" />
// Vite 的客户端类型声明：让 TypeScript 认识 import "./style.css" 这类副作用导入、
// 以及 import.meta.env 等 Vite 注入的东西。缺了它，类型检查会报「找不到模块」。
