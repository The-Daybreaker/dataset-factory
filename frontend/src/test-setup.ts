// Vitest 的测试环境准备：装上 jest-dom 的断言扩展（toBeInTheDocument 等）。
// 放在这里而不是逐个测试文件引入，是为了让所有测试共用同一套断言能力。
import "@testing-library/jest-dom/vitest";
