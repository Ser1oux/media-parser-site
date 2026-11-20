"""统一解析错误：携带可直接展示给用户的消息。"""


class ParseError(Exception):
    """解析失败，msg 为面向用户的中文提示。"""
