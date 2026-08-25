"""一事一档材料类别预设。"""

from __future__ import annotations

DEFAULT_MATERIAL_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("final", "最终报送稿"),
    ("draft", "初稿／起草材料"),
    ("revision", "修改稿"),
    ("leader_approved", "领导审定稿"),
    ("notice", "通知／文件依据"),
    ("receipt", "报送回执"),
    ("form", "表格／台账"),
    ("roster", "名册／人员清单"),
    ("plan", "方案／计划"),
    ("summary", "总结／报告"),
    ("minutes", "会议记录／纪要"),
    ("signin", "签到表"),
    ("photo", "图片／影像资料"),
    ("evidence", "佐证材料"),
    ("approval", "请示／审批材料"),
    ("feedback", "反馈意见"),
    ("publicity", "宣传稿件"),
    ("certificate", "证书／证明"),
    ("statistics", "统计数据"),
    ("correspondence", "函件／往来材料"),
    ("other", "其他材料"),
)
