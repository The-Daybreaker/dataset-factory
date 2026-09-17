/** 提示词正文编辑区。 */
import type { ReactElement } from "react";

export function BodyEditor({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}): ReactElement {
  return (
    <div className="flex min-h-0 flex-1 overflow-hidden rounded-md border border-input bg-card">
      <textarea
        data-slot="prompt-body"
        aria-label="正文（Markdown）"
        className="min-h-0 w-full resize-none bg-transparent p-3 font-sans text-t-md leading-(--lh-loose) focus-visible:outline-none"
        value={value}
        onInput={(event) => onChange(event.currentTarget.value)}
        placeholder="你是……"
      />
    </div>
  );
}
