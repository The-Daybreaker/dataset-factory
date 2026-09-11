"""skills 数据域：Skill 包（agentskills.io 标准）的导入、启用/停用与读取（全项目只有本模块碰 skill 文件）。

对外接口：
- 模型：Skill（name / description / enabled）+ SkillImport（导入结果：skill + 体积）
- 操作：import_skill / list_skills / read_skill / set_enabled / delete_skill
- 解析：parse_skill_frontmatter（读 SKILL.md 的 name + description）
- 异常：SkillError 基类 + SkillNameError / SkillNotFoundError / SkillExistsError / SkillFormatError
"""

from .errors import (
    SkillError,
    SkillExistsError,
    SkillFormatError,
    SkillNameError,
    SkillNotFoundError,
)
from .model import Skill, SkillImport, parse_skill_frontmatter
from .store import (
    delete_skill,
    import_skill,
    list_skills,
    read_skill,
    set_enabled,
)

__all__ = [
    "Skill",
    "SkillError",
    "SkillExistsError",
    "SkillFormatError",
    "SkillImport",
    "SkillNameError",
    "SkillNotFoundError",
    "delete_skill",
    "import_skill",
    "list_skills",
    "parse_skill_frontmatter",
    "read_skill",
    "set_enabled",
]
