import { describe, expect, it } from "vitest";

import {
  collectAdvParams,
  paramsToJson,
  setThinkingInJson,
  syncFormFromJson,
  thinkingOfJson,
} from "./adv-params";

describe("thinkingOfJson · 思考三态读取", () => {
  it("一等键顶层 enable_thinking 直接映射三态", () => {
    expect(thinkingOfJson('{"enable_thinking": true}')).toBe("on");
    expect(thinkingOfJson('{"enable_thinking": false}')).toBe("off");
    expect(thinkingOfJson("{}")).toBe("default");
    expect(thinkingOfJson("")).toBe("default");
  });

  it("旧形状（A1 时代的 chat_template_kwargs）回读为当前态——存量配置的开关如实显示", () => {
    const legacy = JSON.stringify({
      extra_body: { chat_template_kwargs: { enable_thinking: false } },
    });
    expect(thinkingOfJson(legacy)).toBe("off");

    const legacyOn = JSON.stringify({
      extra_body: { chat_template_kwargs: { enable_thinking: true } },
    });
    expect(thinkingOfJson(legacyOn)).toBe("on");
  });

  it("JSON 无效按「跟随模型默认」处理（不在这里报错打断）", () => {
    expect(thinkingOfJson("{oops")).toBe("default");
  });
});

describe("setThinkingInJson · 写一等键 + 旧形状迁移", () => {
  it("on / off 写顶层 enable_thinking，同时清掉旧形状键", () => {
    const legacy = JSON.stringify({
      temperature: 0.5,
      extra_body: { chat_template_kwargs: { enable_thinking: true }, top_k: 40 },
    });
    const next = setThinkingInJson(legacy, "off");

    expect(JSON.parse(next ?? "")).toEqual({
      temperature: 0.5,
      enable_thinking: false,
      extra_body: { top_k: 40 },
    });
  });

  it("default 摘掉一等键，extra_body 其余透传键原样保留", () => {
    const current = JSON.stringify({
      enable_thinking: false,
      extra_body: { top_k: 40 },
    });
    const next = setThinkingInJson(current, "default");

    expect(JSON.parse(next ?? "")).toEqual({ extra_body: { top_k: 40 } });
  });

  it("写开关时把直写在 extra_body 里的 enable_thinking 一并搬出到顶层", () => {
    const current = JSON.stringify({ extra_body: { enable_thinking: true } });
    const next = setThinkingInJson(current, "off");

    expect(JSON.parse(next ?? "")).toEqual({ enable_thinking: false });
  });

  it("JSON 无效返回 null（调用方不动原值）；清空后全空返回空串", () => {
    expect(setThinkingInJson("{oops", "on")).toBeNull();
    expect(setThinkingInJson("", "default")).toBe("");
    expect(setThinkingInJson('{"enable_thinking": true}', "default")).toBe("");
  });
});

describe("collectAdvParams · 保存载荷收集（含自动迁移）", () => {
  const base = {
    form: { temperature: "", top_p: "", max_tokens: "" },
    transport: { timeout_seconds: "", max_retries: "" },
  };

  it("顶层一等键进载荷；类型错（非布尔）被拦下", () => {
    const ok = collectAdvParams({ ...base, json: '{"enable_thinking": false}' });
    expect(ok.error).toBeNull();
    expect(ok.params.enable_thinking).toBe(false);

    const bad = collectAdvParams({ ...base, json: '{"enable_thinking": "false"}' });
    expect(bad.error).toContain("enable_thinking");
  });

  it("缺失一等键时从旧形状迁移：chat_template_kwargs 与直写 extra_body 两条路都认", () => {
    const viaKwargs = collectAdvParams({
      ...base,
      json: JSON.stringify({
        extra_body: { chat_template_kwargs: { enable_thinking: true } },
      }),
    });
    expect(viaKwargs.error).toBeNull();
    expect(viaKwargs.params.enable_thinking).toBe(true);
    // 被迁移清空的 extra_body 直接摘除，不残留空对象。
    expect(viaKwargs.params.extra_body).toBeUndefined();

    const viaDirect = collectAdvParams({
      ...base,
      json: JSON.stringify({ extra_body: { enable_thinking: false, top_k: 5 } }),
    });
    expect(viaDirect.error).toBeNull();
    expect(viaDirect.params.enable_thinking).toBe(false);
    expect(viaDirect.params.extra_body).toEqual({ top_k: 5 });
  });

  it("一等键与旧形状同时存在：一等键优先，旧键仍被清掉", () => {
    const result = collectAdvParams({
      ...base,
      json: JSON.stringify({
        enable_thinking: true,
        extra_body: { chat_template_kwargs: { enable_thinking: false } },
      }),
    });

    expect(result.error).toBeNull();
    expect(result.params.enable_thinking).toBe(true);
    expect(result.params.extra_body).toBeUndefined();
  });
});

describe("paramsToJson / syncFormFromJson · 展示与回填", () => {
  it("paramsToJson 带出一等键（在 extra_body 之前）", () => {
    const json = paramsToJson({ enable_thinking: false, extra_body: { top_k: 1 } });
    expect(JSON.parse(json)).toEqual({
      enable_thinking: false,
      extra_body: { top_k: 1 },
    });
  });

  it("回填不把顶层 enable_thinking 当「未知键」报忽略", () => {
    const synced = syncFormFromJson('{"enable_thinking": true, "weird": 1}');
    expect(synced.state.kind).toBe("ignored");
    expect(synced.state.text).toContain("weird");
    expect(synced.state.text).not.toContain("enable_thinking");
  });
});
