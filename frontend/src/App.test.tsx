import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("渲染应用标题", () => {
    render(<App />);

    expect(
      screen.getByRole("heading", { name: "Dataset Factory 打标测试台" }),
    ).toBeInTheDocument();
  });
});
