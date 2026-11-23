"""Versioned conversation drafts and strict offline rule parsing."""
import copy
import re
from jsonschema import Draft202012Validator
from .common import ROOT, read, now
from .planning_store import PlanningError, PlanningStore, new_id, require
from .materials import material_spec, validate_material

SCENE_CATALOG = read(ROOT / "configs/scene_catalog.json")
SCENES = {entry["id"]: entry for entry in SCENE_CATALOG["scenes"]}
DIALOGUE_DEFAULT_FRAMES = SCENE_CATALOG.get("dialogue_default_frames", 20)
SCENE_NUMBERS = {key: scene["native_id"] for key, scene in SCENES.items()
                 if scene["availability"] == "verified"}
SCENE_LABELS = {key: SCENES[key]["display_name"] for key in SCENE_NUMBERS}
OUTPUT_METRICS = ("step_time", "newton_iterations", "pcg_iterations", "collision_pairs")
LEGACY_CASE_NAMES = {20: "smoke-20", 50: "standard-50", 100: "extended-100"}


def case_name(frames):
    return LEGACY_CASE_NAMES.get(frames, f"frames-{frames}")


def scene_args(args, scene):
    result = list(args)
    result[result.index("--scene") + 1] = str(SCENE_NUMBERS[scene])
    if scene == "fixed-cloth":
        result.append("--export-states")
    return result


def observation_args(args, request):
    result = list(args)
    if request.get("native_render"):
        result.append("--native-render-frames")
    if request.get("diagnostic_contact_trace"):
        result.append("--trace-newton")
    return result


def scene_selector(scene):
    return SCENES[scene].get("cloth_scene", "")


def scene_display(scene):
    spec = SCENES.get(scene)
    if not spec:
        return "未记录场景"
    selector = spec.get("cloth_scene")
    suffix = f"CIPC_SCENE={selector}" if selector else f"场景 {spec['native_id']}"
    return f"{spec['display_name']}（{suffix}）"


def normalize_request(request, *, detailed=False):
    def invalid(code, message):
        return PlanningError(code if detailed else "INVALID_REQUEST", message)

    schema_file = "task_request_v2.schema.json" if (
        isinstance(request, dict) and request.get("schema_version") == "task-request-v2"
    ) else "task_request_v1.schema.json"
    schema = read(ROOT / "docs/contracts" / schema_file)
    error = next(Draft202012Validator(schema).iter_errors(request), None)
    if error:
        field = next(iter(error.absolute_path), None)
        if field in ("task_frames", "timeout_sec") and error.validator in ("minimum", "maximum"):
            raise invalid("PARAMETER_RANGE", "帧数须在 1–1000 内，超时须在 1–86400 秒内。")
        raise invalid("PLAN_SCHEMA", "计划字段、类型或测试模式不符合任务约定。")
    scene = request["scene"]
    if scene not in SCENE_NUMBERS:
        raise invalid("UNSUPPORTED_SCENE", "请选择目录中已验证的原生场景。")
    spec = SCENES[scene]
    if not all((ROOT / "simulator" / asset).is_file() for asset in spec["required_assets"]):
        raise invalid("SCENE_ASSETS", "所选场景缺少必要资产。")
    if (type(request["timeout_sec"]) is not int or
            any(type(v) is not int for v in request["task_frames"])):
        raise invalid("PLAN_SCHEMA", "帧数与超时必须为整数。")
    limits = spec["frames"]
    if not all(limits["min"] <= n <= limits["max"] for n in request["task_frames"]):
        raise invalid("PARAMETER_RANGE", f"所选场景支持 {limits['min']}–{limits['max']} 帧。")
    if request.get("native_render") and not spec["native_render_frames"]:
        raise invalid("UNSUPPORTED_OUTPUT", "所选场景尚未支持原生抓帧。")
    if "material" in request:
        if not material_spec(scene):
            raise invalid("MATERIAL_SCENE", "此场景未开放按任务修改材料。")
        validate_material(scene, request["material"])
    if request.get("diagnostic_contact_trace") and (
        not spec.get("goal_analysis", {}).get("contact_trace")
        or request["mode"] != "custom" or len(request["task_frames"]) != 1
        or request["task_frames"][0] > 100 or request["timeout_sec"] > 600
    ):
        raise invalid("DIAGNOSTIC_BUDGET", "接触追踪仅支持已验证场景、单用例、不超过100帧和600秒。")
    return {**copy.deepcopy(request), "task_frames": sorted(request["task_frames"])}


def _compact(text):
    return re.sub(r"\s+", "", text).strip("。.!！").lower()


SCENE_ALIASES = {_compact(alias): key for key, number in SCENE_NUMBERS.items()
                 for alias in (key, SCENE_LABELS[key], *SCENES[key].get("aliases", []),
                               scene_selector(key) or f"场景{number}")}
SCENE_PATTERN = "|".join(re.escape(name) for name in sorted(SCENE_ALIASES, key=len, reverse=True))
FRAME_PATTERN = r"[+-]?[0-9]{1,16}(?:[,，、/][+-]?[0-9]{1,16}){0,2}"


def scene_from_label(label):
    return SCENE_ALIASES.get(_compact(label))


def numbered_scene(text):
    number = re.search(r"场景[+-]?[0-9]+(?![0-9]|帧)", _compact(text))
    if not number:
        return None
    scene = scene_from_label(number[0])
    if scene is None:
        raise PlanningError("UNSUPPORTED_SCENE", "该编号未开放，请选择目录中已验证的场景。")
    return scene


def explicitly_named_scene(text, scene):
    compact = _compact(text)
    return any(key == scene and alias in compact for alias, key in SCENE_ALIASES.items())


def dialogue_intent(text, *, previous=None, selected=None):
    """Resolve bounded short commands. Leave free-form requests to the model."""
    compact = _compact(text)
    numbered_scene(text)
    revision = re.fullmatch(
        r"(?:把)?(?:仿真)?(?:帧数)?(?:改成|改为|调整为)([+-]?[0-9]{1,16})帧?", compact)
    switch = re.fullmatch(rf"(?:把场景)?(?:换成|切换到|改成|改为)(?P<scene>{SCENE_PATTERN})", compact)
    if revision or switch:
        if previous is None:
            raise PlanningError("MISSING_CONTEXT", "请先生成一份有效计划，再修改帧数或更换场景。")
        fields = {"task_frames": [int(revision[1])], "mode": "custom"} if revision else {
            "scene": SCENE_ALIASES[switch["scene"]]}
        return fields, True
    match = re.fullmatch(
        rf"(?:请)?(?:对|在|运行|执行|跑)?(?:stiff-gipc)?"
        rf"(?:(?P<scene>{SCENE_PATTERN})(?:场景)?)?(?:执行|运行|进行|跑)?[,，:：]?"
        rf"(?:(?P<standard>标准(?:基准)?(?:测试|评测|套件))|"
        rf"(?P<frames>{FRAME_PATTERN})帧(?:基准测试|测试|评测)?)?"
        r"(?P<capture>[,，]?(?:并)?(?:采集|保存)原生画面)?", compact)
    if not match or not (match["scene"] or match["frames"] or match["standard"]):
        return {}, False
    scene = SCENE_ALIASES[match["scene"]] if match["scene"] else selected or (
        previous["scene"] if previous else None)
    if scene is None:
        raise PlanningError("MISSING_CONTEXT", "请指定场景，或先在计划入口选择场景。")
    if match["standard"]:
        frames = [20, 50, 100]
    elif match["frames"]:
        frames = [int(v) for v in re.split(r"[,，、/]", match["frames"])]
    else:
        frames = [DIALOGUE_DEFAULT_FRAMES]
    fields = {"scene": scene, "task_frames": frames, "mode": "standard" if match["standard"] else "custom"}
    if match["capture"]:
        fields["native_render"] = True
    return fields, False


def task_request(request_id, fields):
    return {
        "schema_version": "task-request-v2", "request_id": request_id, "mode": "custom",
        "backend_id": "c0-003-benchagent-v1", "timeout_sec": 3600, **fields,
    }


def request_from_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise PlanningError("UNSUPPORTED_REQUEST", "请输入场景与帧数。")
    fields, _ = dialogue_intent(text)
    if not fields:
        raise PlanningError("UNSUPPORTED_REQUEST", "请输入目录中的场景名称或编号，例如「运行场景5，37帧」。")
    return normalize_request(task_request(new_id("intent"), fields))


class ConversationService:
    def __init__(self, store=None):
        self.store = store or PlanningStore()

    def create_conversation(self, title="新测试对话"):
        if not isinstance(title,str) or not title.strip() or len(title)>200:
            raise PlanningError("INVALID_TITLE", "标题不能为空且不超过200字")
        value={"conversation_id":new_id("conv"),"title":title.strip(),"created_at":now(),
               "messages":[],"active_draft_id":None}
        with self.store.transaction() as state:
            state["conversations"][value["conversation_id"]]=value
        return copy.deepcopy(value)

    def get_conversation(self, conversation_id):
        return self.store.get("conversations",conversation_id)

    def get_draft(self, draft_id):
        return self.store.get("drafts",draft_id)

    def record_model_reply(self, conversation_id, text, reply, request_id, *, audit=None):
        with self.store.transaction() as state:
            conversation = require(state["conversations"], conversation_id)
            for actor, content in (("user", text), ("model", reply)):
                conversation["messages"].append({
                    "message_id": new_id("msg"), "actor": actor, "content": content,
                    "source_kind": "model", "request_id": request_id, "created_at": now(),
                    **({"planning_audit": audit} if audit and actor == "model" else {}),
                })

    def update_draft(self, conversation_id, request, expected_revision, *, text=None, model=False, audit=None):
        if type(expected_revision) is not int or expected_revision<0:
            raise PlanningError("DRAFT_CONFLICT","草稿版本无效")
        if text is not None and (not isinstance(text,str) or len(text)>4000):
            raise PlanningError("INVALID_MESSAGE","消息格式无效")
        origin="model" if model else ("rule" if isinstance(request,str) or text else "form")
        user_text=request if isinstance(request, str) else text
        normalized=request_from_text(request) if isinstance(request, str) else normalize_request(request)
        with self.store.transaction() as state:
            conversation=require(state["conversations"],conversation_id)
            active=conversation["active_draft_id"]
            previous=require(state["drafts"],active) if active else None
            revision=previous["revision"] if previous else 0
            if revision!=expected_revision:
                raise PlanningError("DRAFT_CONFLICT","草稿已变化，请读取最新版本")
            # Equivalent forms do not create another revision; text messages remain visible.
            changed=previous is None or previous["task_request"]!=normalized
            draft_id=active or new_id("draft")
            if changed:
                state["drafts"][draft_id]={
                    "draft_id":draft_id,"revision":revision+1,"conversation_id":conversation_id,
                    "task_request":normalized,"origin":origin,"updated_at":now(),
                    **({"planning_audit": audit} if audit else {})}
            conversation["active_draft_id"]=draft_id
            if user_text:
                conversation["messages"].append({"message_id":new_id("msg"),"actor":"user",
                    "content":user_text,"source_kind":origin,"request_id":normalized["request_id"],
                    "draft_id":draft_id,"draft_revision":state["drafts"][draft_id]["revision"],"created_at":now()})
                conversation["messages"].append({"message_id":new_id("msg"),"actor":origin,
                    "content":("已生成" + SCENE_LABELS[normalized["scene"]] + "计划，帧数："
                               + " / ".join(map(str, normalized["task_frames"]))
                               + "。请确认后执行。" if model else "已生成受限草稿；尚未启动仿真。"),
                    "source_kind":origin,
                    **({"planning_audit": audit} if audit else {}),
                    "request_id":normalized["request_id"],"draft_id":draft_id,
                    "draft_revision":state["drafts"][draft_id]["revision"],"created_at":now()})
            value=copy.deepcopy(state["drafts"][draft_id])
        return value
