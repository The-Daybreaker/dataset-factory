/** 页头状态 chip：当前激活的端点配置（只读展示；切换在工作台切换器 / 本子页详情）。 */
import type { ReactElement } from "react";
import { useEffect, useState } from "react";
import type { EndpointConfigSummary } from "../../api";
import { api } from "../../api";

export function ActiveEndpointChip(): ReactElement {
  const [active, setActive] = useState<EndpointConfigSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api
      .listEndpoints()
      .then((list) => {
        if (!cancelled) {
          setActive(list.find((item) => item.is_active) ?? null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setActive(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <span className="inline-flex h-[30px] items-center gap-[7px] rounded-full border border-border bg-card px-3 text-[12px] text-muted-foreground">
      <span
        className={
          "size-[7px] rounded-full " +
          (active !== null ? "bg-success" : "bg-muted-foreground/40")
        }
        aria-hidden
      />
      {active !== null ? `${active.name} · ${active.model}` : "未配置端点"}
    </span>
  );
}
