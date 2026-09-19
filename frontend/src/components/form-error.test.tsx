import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FormError } from "./form-error";

describe("FormError", () => {
  it("按 alert 语义播报，并把调用点给的样式原样落在段落上", () => {
    render(<FormError className="mb-3 text-t-sm text-bad-ink">导入失败</FormError>);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("导入失败");
    expect(alert.tagName).toBe("P");
    expect(alert.className).toBe("mb-3 text-t-sm text-bad-ink");
  });

  it("不传样式时只给语义，不替调用点塞类名", () => {
    render(<FormError>出错了</FormError>);

    expect(screen.getByRole("alert").getAttribute("class")).toBeNull();
  });
});
