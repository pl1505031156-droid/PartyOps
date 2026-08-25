"""东方皮肤上下文与偏好。

视觉资源完全由前端静态包提供；后端只返回季节选择和用户偏好，避免
主题切换与任何业务页面形成依赖。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .enums import ArtLevel, SeasonTheme
from .models import SystemSetting, User, UserAppearancePreference

# 中国现行标准时间全年固定为 UTC+8。使用标准库固定偏移，避免部分
# Windows/UOS 精简运行时缺少 IANA tzdata 时导致整个服务无法启动。
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
GLOBAL_APPEARANCE_KEY = "appearance.global"
DEFAULT_GLOBAL_APPEARANCE = {
    "theme_mode": "auto",
    "fixed_theme": SeasonTheme.SPRING.value,
    "default_art_level": ArtLevel.STANDARD.value,
    "default_reduce_motion": False,
    "version": 1,
}


def automatic_season(moment: datetime | None = None) -> SeasonTheme:
    """按立春、立夏、立秋、立冬的常用日期边界计算主题季节。"""

    if moment is None:
        local = datetime.now(SHANGHAI)
    elif moment.tzinfo is None:
        local = moment.replace(tzinfo=SHANGHAI)
    else:
        local = moment.astimezone(SHANGHAI)
    month_day = (local.month, local.day)
    if (2, 4) <= month_day < (5, 5):
        return SeasonTheme.SPRING
    if (5, 5) <= month_day < (8, 7):
        return SeasonTheme.SUMMER
    if (8, 7) <= month_day < (11, 7):
        return SeasonTheme.AUTUMN
    return SeasonTheme.WINTER


def global_appearance(db: Session) -> dict:
    setting = db.get(SystemSetting, GLOBAL_APPEARANCE_KEY)
    value = setting.value if setting and isinstance(setting.value, dict) else {}
    merged = {**DEFAULT_GLOBAL_APPEARANCE, **value}
    if merged["theme_mode"] not in {"auto", "fixed"}:
        merged["theme_mode"] = "auto"
    if merged["fixed_theme"] not in {item.value for item in SeasonTheme}:
        merged["fixed_theme"] = SeasonTheme.SPRING.value
    if merged["default_art_level"] not in {item.value for item in ArtLevel}:
        merged["default_art_level"] = ArtLevel.STANDARD.value
    merged["version"] = max(1, int(merged.get("version", 1)))
    return merged


def effective_season(config: dict, preference: UserAppearancePreference | None = None) -> str:
    if preference and preference.theme_override:
        return preference.theme_override.value
    if config["theme_mode"] == "fixed":
        return str(config["fixed_theme"])
    return automatic_season().value


def ensure_user_appearance(db: Session, user: User) -> UserAppearancePreference:
    preference = db.get(UserAppearancePreference, user.id)
    if preference:
        return preference
    config = global_appearance(db)
    preference = UserAppearancePreference(
        user_id=user.id,
        art_level=ArtLevel(config["default_art_level"]),
        reduce_motion=bool(config["default_reduce_motion"]),
    )
    db.add(preference)
    db.flush()
    return preference
