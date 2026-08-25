"""任务状态机与闭环规则。"""

from __future__ import annotations

from .enums import TaskStatus
from .problems import ProblemException

ACTION_TARGETS: dict[str, dict[TaskStatus, TaskStatus]] = {
    "accept": {TaskStatus.PENDING_RECEIPT: TaskStatus.IN_PROGRESS},
    "start": {
        TaskStatus.PENDING_BREAKDOWN: TaskStatus.IN_PROGRESS,
        TaskStatus.RETURNED: TaskStatus.IN_PROGRESS,
    },
    "wait_feedback": {TaskStatus.IN_PROGRESS: TaskStatus.WAITING_FEEDBACK},
    "resume": {TaskStatus.WAITING_FEEDBACK: TaskStatus.IN_PROGRESS},
    "submit_review": {
        TaskStatus.IN_PROGRESS: TaskStatus.PENDING_REVIEW,
        TaskStatus.WAITING_FEEDBACK: TaskStatus.PENDING_REVIEW,
    },
    "return": {TaskStatus.PENDING_REVIEW: TaskStatus.RETURNED},
    "approve": {TaskStatus.PENDING_REVIEW: TaskStatus.COMPLETED},
    "complete": {
        TaskStatus.IN_PROGRESS: TaskStatus.COMPLETED,
        TaskStatus.WAITING_FEEDBACK: TaskStatus.COMPLETED,
    },
    "archive": {TaskStatus.COMPLETED: TaskStatus.ARCHIVED},
    "reopen": {
        TaskStatus.COMPLETED: TaskStatus.IN_PROGRESS,
        TaskStatus.ARCHIVED: TaskStatus.IN_PROGRESS,
    },
}


def transition(current: TaskStatus, action: str) -> TaskStatus:
    target = ACTION_TARGETS.get(action, {}).get(current)
    if target is None:
        raise ProblemException(
            409,
            "INVALID_TRANSITION",
            "当前状态不能执行该操作",
            f"任务处于“{current.value}”，不能执行“{action}”。",
        )
    return target
