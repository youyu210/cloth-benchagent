"""Resolve explicit cloth edits before a plan can be frozen."""
import re

from .conversation_service import (
    DIALOGUE_DEFAULT_FRAMES, SCENE_ALIASES, numbered_scene,
)
from .materials import MATERIAL_FIELDS, material_spec, validate_material
from .planning_store import PlanningError

ALIASES = {
    "膜杨氏模量": "young_pa", "杨氏模量": "young_pa", "杨氏模数": "young_pa",
    "体密度": "density_kg_m3", "密度": "density_kg_m3", "泊松比": "poisson_ratio",
}
UNSUPPORTED = {
    "弯曲杨氏模量": "弯曲模量随场景预设，独立于膜杨氏模量。",
    "弯曲模量": "弯曲模量随场景预设。",
    "摩擦": "摩擦系数是场景接触设置，未开放按布片覆盖。",
    "厚度": "厚度同时进入面积、质量和弯曲系数，随场景预设。",
    "拉梅": "拉梅系数由原生模型推导。",
    "阻尼": "当前材料任务未开放阻尼。",
    "屈服": "当前布料模型接口未开放屈服参数。",
    "弹性系数": "请明确指膜杨氏模量、体密度还是泊松比。",
    "网格": "网格资产随场景预设。",
    "约束条件": "固定点和约束条件随场景预设。",
}
NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?"
FIELDS = "|".join(sorted(ALIASES, key=len, reverse=True))
FRAMES = r"(?:运行|模拟|仿真|跑)?(\d{1,5})帧"
QUALITATIVE = ("更软", "柔软", "更硬", "更挺", "褶皱", "起皱")
MATERIAL_WORDS = ("材料", "参数", *ALIASES, *UNSUPPORTED, *QUALITATIVE)


CHANGE_VERBS = r"(?:修改为|修改成|调节为|调整为|调整到|设置为|改成|改为|设为|调到|变成|变为|到|至)"


def _value(name, expression, current):
    transition = re.fullmatch(rf"(?:从|由)({NUMBER}.*?){CHANGE_VERBS}(.+)", expression)
    if transition:
        # The source value is descriptive. The validated draft supplies the actual baseline.
        _absolute_value(name, transition[1])
        expression = transition[2]
    expr = re.sub(rf"^(?:{CHANGE_VERBS}|为|=|：|:)", "", expression)
    ratio = re.fullmatch(rf"(?:原来(?:的)?|当前(?:的)?)?({NUMBER}|两|二|一|三|四|五|半)倍", expr)
    percent = re.fullmatch(rf"(降低|下调|减少|提高|增加|上调)({NUMBER})%", expr)
    if ratio:
        digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "半": .5}
        factor = digits[ratio[1]] if ratio[1] in digits else float(ratio[1])
        return current * factor
    if percent:
        amount = float(percent[2]) / 100
        if not 0 <= amount <= 1:
            raise PlanningError("MATERIAL_RANGE", "相对百分比须在 0–100% 内。")
        return current * (1 - amount if percent[1] in {"降低", "下调", "减少"} else 1 + amount)
    return _absolute_value(name, expr)


def _absolute_value(name, expression):
    match = re.fullmatch(rf"({NUMBER})(.*)", expression)
    if not match:
        raise PlanningError("MATERIAL_VALUE", f"请给出{MATERIAL_FIELDS[name]['label']}的数值、倍数或百分比。")
    number, unit = float(match[1]), match[2]
    units = {
        "young_pa": {"": 1, "pa": 1, "kpa": 1e3, "mpa": 1e6, "帕": 1, "千帕": 1e3, "兆帕": 1e6},
        "density_kg_m3": {"": 1, "kg/m³": 1, "kg/m3": 1, "kg/m^3": 1, "千克/立方米": 1, "公斤/立方米": 1},
        "poisson_ratio": {"": 1, "无量纲": 1},
    }
    if unit not in units[name]:
        raise PlanningError("MATERIAL_UNIT", f"单位不匹配，请使用 {MATERIAL_FIELDS[name]['unit']}。")
    return number * units[name][unit]


def _scene_matches(text):
    aliases = sorted(SCENE_ALIASES, key=len, reverse=True)
    pattern = "|".join(re.escape(alias) for alias in aliases)
    matches = list(re.finditer(pattern, text))
    return {SCENE_ALIASES[m[0]] for m in matches}, re.sub(pattern, "", text)


def resolve_material(text, previous=None, selected=None):
    """Return a scene-bound patch or a non-executable advisory reply."""
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
        return None
    compact = re.sub(r"\s+", "", text.lower()).strip("。.!！?")
    if not any(word in compact for word in MATERIAL_WORDS):
        return None
    number = numbered_scene(text)
    named, rest = _scene_matches(compact)
    if number:
        named.add(number)
    if len(named) > 1 or (selected and named and selected not in named):
        raise PlanningError("MATERIAL_SCENE", "每份材料计划只允许指定一个场景。")
    scene = next(iter(named), selected or (previous or {}).get("scene"))
    spec = material_spec(scene)
    if not spec:
        raise PlanningError("MATERIAL_SCENE", "请选择固定布料、悬挂布料或球体与布料。")
    if any(term in rest for term in ("球体", "障碍物", "刚体", "弹性体", "第二片", "所有物体")):
        raise PlanningError("MATERIAL_OBJECT", "此接口只可修改该场景的布片，请明确对象。")
    previous = previous if previous and previous["scene"] == scene else {}
    baseline = previous.get("material", {}).get("values", {})
    current = {**spec["defaults"], **baseline}
    qualitative = any(word in rest for word in QUALITATIVE)
    query = any(word in rest for word in ("查询", "查看", "是多少", "是什么", "当前值", "默认值"))
    if qualitative or (query and "恢复" not in rest):
        values = "，".join(f"{MATERIAL_FIELDS[k]['label']} {v:g} {MATERIAL_FIELDS[k]['unit']}"
                           for k, v in current.items())
        advice = "修改时请指定参数、目标值及所需帧数。"
        if qualitative:
            direction = ("提高" if "更硬" in rest or "更挺" in rest else "降低"
                         if "更软" in rest or "柔软" in rest else None)
            focus = (f"可单独{direction}膜杨氏模量，比较面内拉伸和剪切响应。"
                     if direction else "可保持当前材料作为基线，再做单变量面内刚度对照。")
            advice = (focus + "弯曲和褶皱还受独立弯曲模量、厚度和接触影响。"
                      "请指定数值或百分比后预览对照计划。")
        return {"reply": spec["label"] + "当前为 " + values + "。" + advice}
    for word, reason in UNSUPPORTED.items():
        if word in rest:
            raise PlanningError("MATERIAL_FIELD", reason)
    frame_matches = re.findall(FRAMES, rest)
    if len(frame_matches) > 1:
        raise PlanningError("MATERIAL_FRAMES", "请为此次材料试验指定一个帧数。")
    capture = bool(re.search(r"(?:采集|保存)原生画面", rest))
    rest = re.sub(FRAMES, "", rest)
    rest = re.sub(r"(?:其他|其余)(?:材料)?参数保持不变|(?:并)?(?:采集|保存)原生画面", "", rest)
    rest = re.sub(r"^(?:请)?(?:在|将|把)?(?:场景)?(?:中)?(?:的)?(?:布料|布片)?(?:的)?(?:材料参数)?", "", rest)
    rest = rest.strip("，,、。；;")
    values = dict(baseline)
    if re.fullmatch(r"恢复(?:这个场景|场景|布片|布料)?(?:的)?默认(?:材料)?(?:参数|配置)?", rest):
        values.clear()
    else:
        clauses = [c for c in re.split(r"[，,、；;]|以及|并将|并把|并且|和(?=" + FIELDS + ")", rest) if c]
        seen = set()
        for clause in clauses:
            match = re.fullmatch(rf"(?:请|把|将|并|把布料的|布料的|布片的|的)*({FIELDS})(.+)", clause)
            if not match:
                raise PlanningError("MATERIAL_FIELD", "请逐项指定已开放的材料参数和数值。")
            name = ALIASES[match[1]]
            if name in seen:
                raise PlanningError("MATERIAL_FIELD", "同一轮中每个参数只可指定一次。")
            seen.add(name)
            values[name] = _value(name, match[2], current[name])
        if not seen:
            raise PlanningError("MATERIAL_FIELD", "请指定要修改的材料参数或明确恢复默认。")
    material = {"object": spec["object"], "values": values, "previous": dict(baseline)}
    validate_material(scene, material)
    fields = {
        "scene": scene, "mode": "custom", "material": material,
        "task_frames": [int(frame_matches[0])] if frame_matches else previous.get("task_frames", [DIALOGUE_DEFAULT_FRAMES]),
    }
    if capture or not previous or previous.get("native_render"):
        fields["native_render"] = True
    for key in ("timeout_sec", "output_metrics", "diagnostic_contact_trace"):
        if key in previous:
            fields[key] = previous[key]
    return fields

