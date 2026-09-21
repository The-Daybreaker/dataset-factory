/** @vitest-environment node */
import { readFileSync } from "node:fs";
import { parse } from "postcss";
import { describe, expect, it } from "vitest";
import { buttonVariants } from "./components/ui/button";
import { cn } from "./lib/utils";

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
  it("正文长段落行高引用原型的三档刻度", () => {
    const light = declarations(":root");

    expect(["tight", "base", "loose"].map((name) => light.get(`--lh-${name}`))).toEqual(
      ["1.35", "1.55", "1.75"],
    );
  });
  it("合并自定义字号时保留语义文字颜色，并允许覆盖字号", () => {
    expect(cn(buttonVariants({ size: "sm" }))).toContain("text-primary-foreground");
    expect(cn("text-bad-ink text-t-md", "text-t-sm")).toBe("text-bad-ink text-t-sm");
    expect(cn("text-t-sm text-text-4", "text-text-2")).toBe("text-t-sm text-text-2");
  });
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

describe("设计系统类门禁", () => {
  it("ui-spec 定义的 .cb 复选框形态必须存在于构建（V13：规范有、构建无 = 死类名）", () => {
    // 门禁判据：规范里声明过的设计系统类名，globals.css 里必须有对应实体——
    // .cb 曾静默溜过（5 处 className 写了它、CSS 零命中），新类名进规范时同步加进这份清单。
    const required = [".cb"];
    for (const selector of required) {
      expect(css.includes(selector)).toBe(true);
    }
  });
});
