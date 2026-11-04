"""Tools that join local agent execution to the learning rules."""


def definition(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}}


GUIDANCE_TOOL_DEFINITIONS = [
    definition("plan_learning", "针对用户真实的学习目标创建分步计划。每步只有一个具体任务和完成标准，最后安排新的独立/迁移练习。独立和迁移任务必须与历史及本计划中所有先前任务不同，普通追加也不能重复旧题。已有计划只能追加，不能覆盖或抹掉进度。retry_requested=true 时以 append=true 安排恰好两步：新的 independent 和新的 transfer，系统会导航到新练习并保留旧证据。任务不限学科；保持用户原本想学的内容。", {
        "goal": {"type": "string"}, "append": {"type": "boolean"},
        "steps": {"type": "array", "minItems": 1, "maxItems": 12, "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "task": {"type": "string", "description": "交给用户亲手尝试的一个小任务，不直接填写答案。"},
            "success_criteria": {"type": "string", "description": "可观察的完成标准，不泄露参考答案。"},
            "kind": {"type": "string", "enum": ["explain", "guided", "independent", "transfer"]},
        }, "required": ["title", "task", "success_criteria", "kind"], "additionalProperties": False}},
    }, ["goal", "steps"]),
    definition("review_learning_attempt", "审阅当前记录中真实用户提交的尝试。submission.artifacts是提交时固定的作品快照，包含SHA-256指纹、系统检查和文本；评价作品时引用该快照及具体文本行，content_truncated=true时只能依据已提供部分作出判断。不要将当前磁盘文件冒充提交时版本。系统checks只验证对应的客观条件，不能等同于完整正确性。submission.prediction/confidence/observed_result记录用户先前的预测、信心和实际结果，请对照反馈。不接受模型自行编造的用户答案。依据任务标准和实际证据评价；证据不足用 uncertain/unavailable。该工具不推进步骤，掌握资格由 Python 决定。", {
        "submission_id": {"type": "string"},
        "correctness": {"type": "string", "enum": ["correct", "partially_correct", "incorrect", "uncertain"]},
        "reasoning_quality": {"type": "string", "enum": ["sound", "partial", "unsound", "unavailable"]},
        "evidence": {"type": "string", "description": "评价依据，应引用实际作答或读取结果。"},
        "feedback": {"type": "string", "description": "做对了什么、一个需改进点与原因，不泄露完整答案或擅自宣告掌握。"},
    }, ["submission_id", "correctness", "reasoning_quality", "evidence", "feedback"]),
    definition("record_learning_help", "用户在自然语言中请求提示、讲解或完整演示时，先记录帮助程度再回应。不能降低或清空帮助记录。界面按钮已记录的帮助不要重复调用。", {"kind": {"type": "string", "enum": ["hint", "explain", "demonstrate"]}}, ["kind"]),
]
