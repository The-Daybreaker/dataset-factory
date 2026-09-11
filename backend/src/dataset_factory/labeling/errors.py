"""labeling 编排层的异常。

下游数据域（prompts / skills / sessions）与能力层（llm）的类型化异常直接冒泡给入口层
（它们的消息已可操作）；这里只放编排层自己的错误——一轮打标的输入不满足组装前提。
"""

from __future__ import annotations


class LabelingError(Exception):
    """labeling 编排层错误基类。"""


class PromptNotSelectedError(LabelingError):
    """会话尚未选定基础提示词，本轮调用也没有传入（一轮打标必须有一个基础提示词作 system 底座）。"""


class EmptyTurnError(LabelingError):
    """本轮没有任何可打标的内容（指令为空且未附图片）。"""


class AttachmentReadError(LabelingError):
    """读取会话附件副本字节失败（副本刚写入却读不出，通常是磁盘或权限问题）。"""


class SettingsFormatError(LabelingError):
    """会话设置事件的结构非法（正常写入不会产生，通常是会话文件被手改坏）。"""
