/** 正文编辑区：等宽字体 + 行号槽（行号随滚动同步平移）。 */
import type { ReactElement } from "react";
import { useState } from "react";

export function BodyEditor({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}): ReactElement {
  const [scrollTop, setScrollTop] = useState(0);
  const lineCount = value === "" ? 1 : value.split("\n").length;

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden rounded-md border border-input bg-background focus-within:ring-2 focus-within:ring-ring/55">
      <div
        aria-hidden
        className="min-w-[30px] shrink-0 overflow-hidden border-r border-border bg-muted/50 px-2 py-2.5 text-right font-mono text-[12px] leading-[1.7] text-muted-foreground select-none"
      >
        <div style={{ transform: `translateY(-${scrollTop}px)` }}>
          {Array.from({ length: lineCount }, (_, index) => index + 1).map(
            (lineNumber) => (
              <div key={lineNumber}>{lineNumber}</div>
            ),
          )}
        </div>
      </div>
      <textarea
        data-slot="prompt-body"
        aria-label="正文（Markdown）"
        className="min-h-0 w-full resize-none bg-transparent px-3 py-2.5 font-mono text-[12.5px] leading-[1.7] focus-visible:outline-none"
        value={value}
        onInput={(event) => onChange(event.currentTarget.value)}
        onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
        placeholder={"你是……\n（Markdown 正文，即基础提示词本体）"}
      />
    </div>
  );
}
