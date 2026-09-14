"""명령어와 자동 공지가 함께 사용하는 작은 별 제목입니다."""

import re

STAR_EMOJI = "<:ppojji_star_small:1548846409791574129>"
CALCULATOR_COLOR = 0x5865F2
CASH_COLOR = 0x9B59B6


def embed_title(title: str | None) -> str | None:
    if not title:
        return None
    # 의미가 있는 기존 전용 이모지는 그대로 두고 별을 덧붙이지 않습니다.
    if re.match(r"^<a?:\w+:\d+>", title.strip()):
        result = re.sub(r"^(<a?:\w+:\d+>)\s*", r"\1 ", title.strip())
        return result if len(result) <= 256 else result[:255] + "…"
    # 일반 장식만 교체하고 공지 제목은 보존합니다.
    text = re.sub(r"^(?:(?:<a?:\w+:\d+>|[✨🫐☀️🔥📝⚠️🔮🚦📚🛠])\s*)+", "", title).strip()
    if text.startswith("[ ") and text.endswith(" ]"):
        text = text[2:-2].strip()
    text = text.removesuffix(" ☀️")
    # 긴 공식 제목도 별을 포함해 Discord의 256자 제한 안에 들어갑니다.
    result = f"{STAR_EMOJI} {text}"
    return result if len(result) <= 256 else result[:255] + "…"
