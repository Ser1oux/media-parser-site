"""解析器注册表：每平台暴露 async parse(target) -> ParseResult。"""
from . import douyin, twitter, xhs, youtube

PARSERS = {
    "douyin": douyin,
    "xhs": xhs,
    "twitter": twitter,
    "youtube": youtube,
}
