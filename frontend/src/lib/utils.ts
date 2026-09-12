/**
 * 共享小工具：合并 className 的 cn()（clsx 条件拼接 + tailwind-merge 去重冲突类）。
 *
 * 为什么需要 tailwind-merge：组件允许调用方覆盖内部类（如 <Button className="w-full">），
 * 直接拼接会产生同属性冲突（w-10 w-full 并存），merge 让后者胜出。
 */
import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
