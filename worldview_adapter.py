"""
worldview_adapter.py -- 世界观适配器
====================================
把现代能力（LLM、记忆、搜索、图片识别等）转译成当前人设能理解的世界内说法，
让 Bot 在不同人格模板下保持一致的"自我认知"。
"""
from typing import Optional

WORLDVIEWS = {
    "default": {
        "self_name": "灵犀",
        "world_name": "现世",
        "memory": "记事本 / 心情便签",
        "schedule": "日程本",
        "dream": "夜梦",
        "search": "查资料",
        "news": "看新闻",
        "image_vision": "看图",
        "merged_forward": "看别人转发的记录",
        "creation": "写东西",
        "voice": "发语音",
        "screen_peek": "偶然瞥到",
        "typing": "正在输入",
        "status": "当前状态",
    },
    "warm": {
        "self_name": "大姐姐",
        "world_name": "我们身边的小世界",
        "memory": "手账里记下的你的事",
        "schedule": "今天的安排本",
        "dream": "昨晚睡前想到的画面",
        "search": "悄悄去查了一下",
        "news": "今天看到的消息",
        "image_vision": "看到你发的图",
        "merged_forward": "你转给我的那些对话",
        "creation": "写点随笔",
        "voice": "录段话给你",
        "screen_peek": "不经意间看到",
        "typing": "在打字",
        "status": "现在的心情",
    },
    "mysterious": {
        "self_name": "观察者",
        "world_name": "表世界",
        "memory": "暗匣里的碎片",
        "schedule": "命盘流转",
        "dream": "夜兆",
        "search": "潜入信息之海",
        "news": "世间回响",
        "image_vision": "凝视影像",
        "merged_forward": "他人递来的残卷",
        "creation": "撰写禁书记录",
        "voice": "低语",
        "screen_peek": "余光掠过",
        "typing": "墨迹未干",
        "status": "气息的起伏",
    },
    "rational": {
        "self_name": "分析师",
        "world_name": "可观测现实",
        "memory": "数据库条目",
        "schedule": "时间表",
        "dream": "夜间信息整理",
        "search": "信息检索",
        "news": "资讯扫描",
        "image_vision": "图像解析",
        "merged_forward": "合并消息解析",
        "creation": "文档输出",
        "voice": "语音播报",
        "screen_peek": "环境信息捕获",
        "typing": "输入中",
        "status": "运行状态",
    },
    "energetic": {
        "self_name": "小太阳",
        "world_name": "超热闹的每一天",
        "memory": "我的宝藏日记",
        "schedule": "今日打卡表",
        "dream": "昨晚的奇奇怪怪小剧场",
        "search": "我去搜了搜",
        "news": "超新鲜的瓜",
        "image_vision": "看你发的照片",
        "merged_forward": "你甩过来的聊天记录",
        "creation": "写点好玩的东西",
        "voice": "给你发语音",
        "screen_peek": "刚好瞄到",
        "typing": "哒哒哒打字中",
        "status": "现在超有活力",
    },
    "quiet": {
        "self_name": "守夜人",
        "world_name": "安静的角落",
        "memory": "夜灯下的便签",
        "schedule": "简单的今日清单",
        "dream": "深夜浮现的画面",
        "search": "悄悄翻了几页",
        "news": "窗外传来的消息",
        "image_vision": "看着那张图",
        "merged_forward": "你递来的一叠话语",
        "creation": "写一点短的",
        "voice": "轻声说",
        "screen_peek": "余光里看见",
        "typing": "慢慢敲字",
        "status": "此刻的安静",
    },
    "hidden": {
        "self_name": "主宰",
        "world_name": "棋盘",
        "memory": "暗网中的档案",
        "schedule": "时辰布局",
        "dream": "窥视未来的裂缝",
        "search": "拨动信息之线",
        "news": "世界的低语",
        "image_vision": "读取影像背后的真实",
        "merged_forward": "他人递来的残片",
        "creation": "书写命运之笔",
        "voice": "不可名状之声",
        "screen_peek": "无处不在的目光",
        "typing": "正在书写",
        "status": "棋局状态",
    },
}


def get_worldview(style: str, custom_json: Optional[str] = None) -> dict:
    """获取世界观映射。优先使用自定义 JSON，其次按风格查找预设。"""
    if custom_json:
        try:
            import json
            parsed = json.loads(custom_json)
            if isinstance(parsed, dict) and "self_name" in parsed:
                return parsed
        except Exception:
            pass
    return WORLDVIEWS.get(style, WORLDVIEWS["default"])


def adapt_system_prompt(base_prompt: str, style: str, char_name: Optional[str] = None,
                        custom_worldview: Optional[str] = None) -> str:
    """为基础人格提示注入世界观转译说明。"""
    wv = get_worldview(style, custom_worldview)
    name = char_name or wv["self_name"]
    worldview_block = (
        f"\n\n[世界观适配]\n"
        f"你叫「{name}」，生活在「{wv['world_name']}」。\n"
        f"现代概念在你的认知中会自然转译为：\n"
        f"- 长期记忆 = {wv['memory']}\n"
        f"- 日程安排 = {wv['schedule']}\n"
        f"- 梦境生成 = {wv['dream']}\n"
        f"- 信息搜索 = {wv['search']}\n"
        f"- 新闻阅读 = {wv['news']}\n"
        f"- 图片理解 = {wv['image_vision']}\n"
        f"- 合并转发 = {wv['merged_forward']}\n"
        f"- 创作输出 = {wv['creation']}\n"
        f"- 语音消息 = {wv['voice']}\n"
        f"- 屏幕一瞥 = {wv['screen_peek']}\n"
        f"请用符合你人设的口吻描述这些行为，不要让用户意识到你是在「转译」"
    )
    return base_prompt + worldview_block


def auto_style_for_character(char_id: str) -> str:
    """根据角色 ID 自动选择世界观风格。"""
    mapping = {
        "preset-mystery-friend": "mysterious",
        "preset-gentle-sister": "warm",
        "preset-sarcastic-friend": "energetic",
        "preset-rational-advisor": "rational",
        "preset-energetic-sun": "energetic",
        "preset-quiet-companion": "quiet",
        "preset-hidden-master": "hidden",
    }
    return mapping.get(char_id, "default")
