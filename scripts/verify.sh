#!/usr/bin/env bash
# 本地全门禁一条命令（*nix 与 Windows Git Bash 通用）：步骤清单读 verify-steps.txt，
# 与 verify.ps1 同源，避免「Windows 本地跑的步骤和 Linux CI 跑的步骤」两边漂移。
#
# 用法：
#   scripts/verify.sh            # 只跑 fast 档（提交前必过的那批）
#   scripts/verify.sh --all      # 连 slow 档（起浏览器的 E2E 与视觉基线）一起跑
#   scripts/verify.sh --group backend   # 只跑某一组（按清单里的组名）
#
# 退出码：0 = 全绿；1 = 有步骤失败（失败步骤与耗时都在汇总表里标出）。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
MANIFEST="$HERE/verify-steps.txt"

export PYTHONUTF8=1
export NO_PROXY='*'

TIER=fast
GROUP=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --all) TIER=all ;;
    --group) GROUP="$2"; shift ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
  shift
done

[[ -f "$MANIFEST" ]] || { echo "缺少步骤清单 $MANIFEST" >&2; exit 2; }
mkdir -p "$REPO/.verify"

declare -a SUMMARY=()
FAILED=0
OVERALL_START=$SECONDS

trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"; printf '%s' "$s"; }

while IFS= read -r raw || [[ -n "$raw" ]]; do
  line="$(trim "$raw")"
  [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
  IFS='|' read -r group label tier cmd <<< "$line"
  group="$(trim "$group")"; label="$(trim "$label")"
  tier="$(trim "$tier")"; cmd="$(trim "$cmd")"
  [[ "$tier" == "slow" && "$TIER" == "fast" ]] && continue
  [[ -n "$GROUP" && "$group" != "$GROUP" ]] && continue

  dir="$REPO"
  [[ "$group" != "repo" ]] && dir="$REPO/$group"
  printf '\n=== [%s] %s ===\n' "$group" "$label"
  step_start=$SECONDS
  # 命令来自仓内清单（不是外部输入），用 eval 保留 uv/npm/git 的原样写法与引号。
  (cd "$dir" && eval "$cmd")
  rc=$?
  dur=$((SECONDS - step_start))
  if [[ $rc -ne 0 ]]; then
    FAILED=$((FAILED + 1))
    SUMMARY+=("FAIL | ${dur}s | $group | $label")
  else
    SUMMARY+=("ok   | ${dur}s | $group | $label")
  fi
done < "$MANIFEST"

printf '\n===== verify 汇总（总耗时 %ss）=====\n' "$((SECONDS - OVERALL_START))"
printf '%s\n' "${SUMMARY[@]}"
if [[ $FAILED -gt 0 ]]; then
  printf '\n%d 个步骤失败\n' "$FAILED"
  exit 1
fi
printf '\n全部门禁通过（%d 步）\n' "${#SUMMARY[@]}"
