from __future__ import annotations

from typing import Any, Dict

from .grader1 import grade as grade_task1
from .grader2 import grade as grade_task2
from .grader3 import grade as grade_task3


def grade(task_id: str, action: Any, ground_truth: Dict[str, Any]) -> float:
    if task_id == "task1_easy":
        return grade_task1(action, ground_truth)
    elif task_id == "task2_medium":
        return grade_task2(action, ground_truth)
    elif task_id == "task3_hard":
        return grade_task3(action, ground_truth)
    else:
        raise ValueError("Unknown task_id: {!r}".format(task_id))


__all__ = ["grade_task1", "grade_task2", "grade_task3", "grade"]