def outfit_payload(summary: str = "清爽的日常造型", style: str = "日常休闲风") -> dict:
    return {
        "style": style,
        "summary": summary,
        "items": [
            {"category": "hairstyle", "name": "自然披肩发", "details": "梳理整齐"},
            {"category": "underwear", "name": "浅色无痕内衣", "details": "轻薄舒适"},
            {"category": "underpants", "name": "浅色无痕内裤", "details": "棉质中腰"},
            {"category": "top", "name": "白色短袖衬衫", "details": "透气棉质"},
            {"category": "bottom", "name": "深蓝百褶裙", "details": "长度及膝"},
            {"category": "shoes", "name": "黑色乐福鞋", "details": "低跟软底"},
        ],
    }
