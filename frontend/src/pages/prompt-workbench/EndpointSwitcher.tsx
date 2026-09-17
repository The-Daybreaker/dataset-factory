/** 端点配置切换器（chip = 「名称 · 模型名」；切换调 activate，对新请求立即生效）。 */
import { ChevronDownIcon } from "lucide-react";
import type { ReactElement } from "react";
import type { EndpointConfigSummary } from "../../api";
import { Button } from "../../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";

export function EndpointSwitcher({
  endpoints,
  disabled = false,
  onActivate,
  onManage,
}: {
  endpoints: EndpointConfigSummary[];
  disabled?: boolean;
  onActivate: (name: string) => void;
  onManage: () => void;
}): ReactElement {
  const active = endpoints.find((item) => item.is_active);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="min-w-0 max-w-60 shrink px-2 text-t-md text-text-2"
          aria-label="端点配置切换器"
          disabled={disabled}
        >
          <span
            className={`size-1.5 shrink-0 rounded-full ${active ? "bg-ok-dot" : "bg-n-400"}`}
            aria-hidden
          />
          <span className="truncate">
            {active ? `${active.name} · ${active.model}` : "未配置端点"}
          </span>
          <ChevronDownIcon className="size-3 shrink-0" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel>端点配置（当前使用）</DropdownMenuLabel>
        {endpoints.length === 0 && (
          <DropdownMenuLabel>（还没有配置——去「管理配置」新增）</DropdownMenuLabel>
        )}
        {endpoints.map((item) => (
          <DropdownMenuItem
            key={item.name}
            disabled={disabled}
            onSelect={() => onActivate(item.name)}
          >
            <span className="flex-1 truncate">
              {item.name} · {item.model}
            </span>
            {item.is_active && <span className="size-2 rounded-full bg-success" />}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled={disabled} onSelect={onManage}>
          管理配置…
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
