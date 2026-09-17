import { readFileSync } from "node:fs";
import { parse } from "postcss";
import { describe, expect, it } from "vitest";
import { buttonVariants } from "./components/ui/button";

const css = readFileSync("src/globals.css", "utf8");

function declarations(selector: string): Map<string, string> {
  const result = new Map<string, string>();
  parse(css).walkRules(selector, (rule) => {
    if (rule.parent?.type === "root") {
      rule.walkDecls((declaration) => {
        result.set(declaration.prop, declaration.value);
      });
    }
  });
  return result;
}

describe("设计令牌", () => {
  it("按钮高度引用原型四档刻度，图标主钮固定为 34px", () => {
    const light = declarations(":root");
    expect(["xs", "sm", "md", "lg"].map((size) => light.get(`--h-${size}`))).toEqual([
      "22px",
      "26px",
      "30px",
      "34px",
    ]);
    for (const [size, token] of [
      ["xs", "xs"],
      ["sm", "sm"],
      ["default", "md"],
      ["lg", "lg"],
    ] as const) {
      expect(buttonVariants({ size })).toContain(`h-(--h-${token})`);
    }
    expect(buttonVariants({ size: "icon-lg" })).toContain("size-(--h-lg)");
    expect(buttonVariants()).not.toContain("focus-visible:ring");
    expect(buttonVariants()).not.toContain("disabled:opacity");
    expect(light.has("--ring")).toBe(false);
  });

  it("警告色各档同为 36 度色相，危险状态点与危险文字同族", () => {
    const light = declarations(":root");
    const dark = declarations(".dark");

    for (const name of ["--warn-bg", "--warn-bd", "--warn-ink", "--warn-dot"]) {
      expect(light.get(name)?.split(" ")[0]).toBe("36");
    }
    for (const name of ["--warn-bg", "--warn-bd", "--warn-ink"]) {
      expect(dark.get(name)?.split(" ")[0]).toBe("36");
    }
    expect(light.get("--bad-dot")?.split(" ")[0]).toBe("4");
  });

  it("正文和辅助文字使用四个独立中性档位", () => {
    const light = declarations(":root");

    expect([1, 2, 3, 4].map((level) => light.get(`--text-${level}`))).toEqual([
      "var(--n-900)",
      "var(--n-800)",
      "var(--n-700)",
      "var(--n-600)",
    ]);
  });
});
