"""dataset_factory — 打标流水线工具的核心库（一期：AI 打标能力）。

纯 Python 库、不依赖任何界面；CLI 与 HTTP 两个入口层复用它。
模块划分（自底向上）：llm（能力层）· prompts / skills / sessions（数据域）·
labeling（编排）· cli / api（入口层）；依赖只能自上而下，核心库不反向依赖入口层。
"""
