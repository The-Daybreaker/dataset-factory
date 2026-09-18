/** @vitest-environment node */
import { describe, expect, it, vi } from "vitest";
import { readSkillDrop } from "./skill-drop";

function fileEntry(name: string): FileSystemEntry {
  return {
    name,
    isFile: true,
    isDirectory: false,
    file: (success: (file: File) => void) => success(new File([name], name)),
  } as FileSystemFileEntry;
}

describe("readSkillDrop", () => {
  it("读取目录的每一批子项，保留嵌套路径", async () => {
    const batches = [[fileEntry("SKILL.md")], [fileEntry("notes.md")], []];
    const readEntries = vi.fn((success: (entries: FileSystemEntry[]) => void) =>
      success(batches.shift() ?? []),
    );
    const root = {
      name: "caption",
      isFile: false,
      isDirectory: true,
      createReader: () => ({ readEntries }),
    } as unknown as FileSystemDirectoryEntry;
    const transfer = {
      items: [{ webkitGetAsEntry: () => root }],
      files: [],
    } as unknown as DataTransfer;

    const files = await readSkillDrop(transfer);

    expect(files.map((file) => file.webkitRelativePath)).toEqual([
      "caption/SKILL.md",
      "caption/notes.md",
    ]);
    expect(readEntries).toHaveBeenCalledTimes(3);
  });

  it("目录读取失败时整体拒绝，不返回部分文件", async () => {
    const root = {
      name: "caption",
      isFile: false,
      isDirectory: true,
      createReader: () => ({
        readEntries: (_success: unknown, reject: (error: Error) => void) =>
          reject(new Error("读取失败")),
      }),
    } as unknown as FileSystemDirectoryEntry;
    const transfer = {
      items: [{ webkitGetAsEntry: () => root }],
      files: [],
    } as unknown as DataTransfer;

    await expect(readSkillDrop(transfer)).rejects.toThrow("读取失败");
  });

  it("没有目录API时保留浏览器文件清单", async () => {
    const file = new File(["body"], "SKILL.md");
    const transfer = { items: [], files: [file] } as unknown as DataTransfer;

    expect(await readSkillDrop(transfer)).toEqual([file]);
  });
});
