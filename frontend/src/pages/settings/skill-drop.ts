/** 读取拖放的完整目录树；目录读取器一次可能只返回部分子项。 */
async function readEntry(entry: FileSystemEntry, prefix: string): Promise<File[]> {
  const path = `${prefix}${entry.name}`;
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) =>
      (entry as FileSystemFileEntry).file(resolve, reject),
    );
    Object.defineProperty(file, "webkitRelativePath", { value: path });
    return [file];
  }
  if (!entry.isDirectory) return [];
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  const files: File[] = [];
  for (;;) {
    const entries = await new Promise<FileSystemEntry[]>((resolve, reject) =>
      reader.readEntries(resolve, reject),
    );
    if (entries.length === 0) return files;
    for (const child of entries) files.push(...(await readEntry(child, `${path}/`)));
  }
}

export async function readSkillDrop(transfer: DataTransfer): Promise<File[]> {
  const entries = Array.from(
    transfer.items ?? [],
    (item) => item.webkitGetAsEntry?.() ?? null,
  );
  if (entries.length === 0 || entries.every((entry) => entry === null)) {
    return Array.from(transfer.files);
  }
  if (entries.some((entry) => entry === null))
    throw new Error("部分拖入文件无法读取，请使用文件夹选择器。");
  const files: File[] = [];
  for (const entry of entries) {
    if (entry !== null) files.push(...(await readEntry(entry, "")));
  }
  return files;
}
