import type { ReactElement } from "react";

/**
 * 应用根组件。
 *
 * T12 阶段先落一个最小骨架，目的是让工程跑起来（能构建、能测、CI 能验）；
 * T13 会把原来的四个界面（聊天打标 / 提示词库 / Skill / 配置）迁移进来替换它。
 */
export function App(): ReactElement {
  return (
    <div className="app">
      <header>
        <h1>Dataset Factory 打标测试台</h1>
      </header>
      <div className="card">
        <p>前端工程化迁移中：工具链已就绪，界面迁移见下一步。</p>
      </div>
    </div>
  );
}
