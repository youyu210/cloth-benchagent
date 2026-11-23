"""Two-panel intelligent workbench backed by the existing planning services."""
import copy
from html import escape

import pandas as pd
import streamlit as st

from .common import ROOT
from .conversation_service import DIALOGUE_DEFAULT_FRAMES, SCENE_LABELS, SCENES, scene_display
from .job_manager import TERMINAL
from .materials import MATERIAL_FIELDS, material_rows, material_spec
from .planning_store import new_id
from .ui_models import activate_plan, initial_context, inputs_changed, select_job

TABS = ("任务计划", "运行监控", "结果报告", "历史实验分析")
SOURCES = ("表单配置", "智能测试（DeepSeek V4）")
SOURCE_LABELS = dict(zip(SOURCES, ("手动表单", "DeepSeek V4")))


def _new_chat():
    st.session_state["workspace"] = initial_context()
    st.session_state["rule_prompt"] = ""
    st.session_state["_smart_next_tab"] = TABS[0]
    st.session_state.pop("_smart_error", None)


def _suggest(prompt):
    st.session_state["rule_prompt"] = prompt
    if st.session_state.get("input_source") == SOURCES[0]:
        st.session_state["input_source"] = SOURCES[1]


def _preview(service, request, **options):
    from .ui_app import context, error

    ctx = context()
    try:
        result = service.preview(ctx["conversation_id"], request, ctx["draft_revision"], **options)
        if "reply" in result:
            ctx.update(conversation_id=result["conversation_id"], plan=None, mode="empty",
                       confirmation_request_id=None, execution_request_id=None, message=result["reply"])
        else:
            activate_plan(ctx, result)
        if isinstance(request, str):
            st.session_state["_clear_sent_prompt"] = request
            ctx["input_signature"] = (st.session_state["input_source"], "", options.get("selected_scene"))
        st.session_state["_smart_next_tab"] = TABS[0]
        st.session_state.pop("_smart_error", None)
        st.rerun()
    except Exception as exc:
        error(exc)


def _plan_summary(request):
    frames = " / ".join(map(str, request["task_frames"]))
    return f"{SCENES[request['scene']]['display_name']} · {frames} 帧"


def assistant_panel(service):
    from .ui_app import context

    ctx = context()
    # Reset a submitted widget before it is created on the next Streamlit run.
    sent_prompt = st.session_state.pop("_clear_sent_prompt", None)
    if sent_prompt is not None and st.session_state.get("rule_prompt") == sent_prompt:
        st.session_state["rule_prompt"] = ""
    heading, action = st.columns([2, 1])
    heading.markdown("### 智能测试助手")
    action.button("新建对话 ＋", key="smart-new-chat", on_click=_new_chat, width="stretch")
    if st.session_state.get("input_source") not in SOURCES:
        st.session_state["input_source"] = SOURCES[1]
    source = st.radio("计划入口", SOURCES, format_func=SOURCE_LABELS.get,
                      horizontal=True, key="input_source", label_visibility="collapsed")
    conversation = service.conversation(ctx["conversation_id"])
    draft = service.conversations.get_draft(conversation["active_draft_id"]) if (
        conversation and conversation.get("active_draft_id")) else None
    label = _plan_summary(draft["task_request"]) if draft else "等待描述测试需求"
    revision = f"草稿 {draft['revision']:02d}" if draft else "新对话"
    st.markdown('<div class="smart-context"><span>' + escape(label) +
                '</span><b>' + revision + '</b></div>', unsafe_allow_html=True)
    with st.container(height=300, key="smart-messages", autoscroll=True):
        messages = (conversation or {}).get("messages", [])
        if not messages:
            with st.chat_message("assistant", avatar="🤖"):
                st.write("描述仿真场景、运行帧数或材料修改要求，我会生成待确认计划。")
                st.caption("也可以在右侧「历史实验分析」中查询已有实验并分析证据。")
        for message in messages[-12:]:
            actor = message["actor"]
            with st.chat_message("user" if actor == "user" else "assistant", avatar=None if actor == "user" else "🤖"):
                audit = message.get("planning_audit") or {}
                label = "你" if actor == "user" else (
                    "BenchAgent · DeepSeek V4" if actor == "model" and audit.get("provider_calls", 0)
                    and audit.get("outcome") != "LOCAL_PATCH_VALIDATED" else "BenchAgent · 本地规则")
                st.caption(label)
                st.write(message["content"])
        if ctx["plan"] and "execution_snapshot" in ctx["plan"]:
            req = ctx["plan"]["execution_snapshot"]["request"]
            st.markdown('<div class="smart-recognized"><b>已识别的任务要求</b><p>' +
                        escape(_plan_summary(req)) + '</p><small>' +
                        ("已生成布片材料修改方案" if "material" in req else "沿用场景材料配置") +
                        '</small></div>', unsafe_allow_html=True)
        if ctx.get("message") and not ctx["plan"]:
            st.info(ctx["message"])
    if source == SOURCES[0]:
        st.info("在右侧手动配置并预览计划。切换 DeepSeek 后可继续自然语言调整。")
        return source
    with st.expander("对话场景与统一测试规则"):
        selected = st.selectbox("对话场景（可选）", [None, *SCENE_LABELS],
                                format_func=lambda s: scene_display(s) if s else "随对话指定")
        st.caption(f"帧数范围依场景目录校验，新任务省略时为 {DIALOGUE_DEFAULT_FRAMES} 帧。")
    examples = (("修改运行帧数", "改成30帧"), ("修改材料参数", "把杨氏模量调整为900000，其他参数保持不变"),
                ("查询材料配置", "查询当前材料参数")) if draft else (
                    ("悬挂布料", "悬挂布料运行20帧"), ("球体与布料", "球体与布料运行29帧"),
                    ("查询材料配置", "查询悬挂布料的材料参数"))
    cols = st.columns(3)
    for col, (label, prompt) in zip(cols, examples):
        col.button(label, key="smart-suggest-" + label, on_click=_suggest, args=(prompt,), width="stretch")
    text = st.text_area("输入任务需求", key="rule_prompt", height=100, max_chars=4000,
                        placeholder="描述测试需求，或继续修改当前计划…\n例如：将悬挂布料泊松比改为0.3，运行20帧。")
    inputs_changed(ctx, (source, text, selected))
    if st.button("发送并预览", type="primary", width="stretch", key="preview"):
        _preview(service, text, use_model=True, api_key=st.session_state.get("deepseek_api_key"),
                 selected_scene=selected)
    ready = service.model_status(st.session_state.get("deepseek_api_key"))["ready"]
    st.caption("DeepSeek V4 · 已配置密钥" if ready else "DeepSeek V4 · 请在系统设置保存密钥")
    return source

def form_controls(service):
    from .ui_app import context
    ctx = context()
    source = '表单配置'
    conversation = service.conversation(ctx['conversation_id'])
    group=st.selectbox("场景类别",["全部",*sorted({v["category"] for v in SCENES.values()})])
    available=[k for k in SCENE_LABELS if group=="全部" or SCENES[k]["category"]==group]
    scene_id=st.selectbox("测试场景",available,format_func=scene_display)
    spec = SCENES[scene_id]
    st.caption(spec["reason"])
    from .native_render import MANIFEST
    variant = spec.get("runtime_variant")
    manifest = f"versions/{variant}-manifest.json" if variant else MANIFEST
    capture = st.checkbox("采集原生画面（初态、中间帧、末帧）",
                          disabled=not spec["native_render_frames"] or not (ROOT / manifest).is_file())
    if scene_id=="fixed-cloth":st.caption("固定布料会保存逐帧状态，便于查看仿真画面。")
    if st.session_state.get("task_kind") not in ("固定帧数", "自定义帧数"):
        st.session_state["task_kind"] = "固定帧数"
    kind=st.radio("测试类型",["固定帧数","自定义帧数"],horizontal=True,key="task_kind")
    if kind=="固定帧数":
        st.caption("从 20、50、100 帧中选择一个帧数，运行一个用例。")
        frames=[st.selectbox("仿真帧数",[20,50,100],index=2,key="single_frames")]
    else:
        minimum, maximum = spec["frames"]["min"], spec["frames"]["max"]
        st.caption(f"在所选场景允许的 {minimum}–{maximum} 帧范围内输入整数，运行一个用例。")
        frames = [st.number_input("自定义仿真帧数", min_value=minimum, max_value=maximum,
                                  value=min(max(120, minimum), maximum), step=1)]
    timeout=st.number_input("单项超时（秒）",min_value=1,max_value=86400,value=3600,step=60,key="timeout")
    material = None
    material_capability = material_spec(scene_id)
    if material_capability:
        active = (conversation or {}).get("active_draft_id")
        prior = service.conversations.get_draft(active)["task_request"] if active else {}
        prior_values = prior.get("material", {}).get("values", {}) if prior.get("scene") == scene_id else {}
        with st.expander("布片材料参数"):
            st.caption(material_capability["label"])
            if st.checkbox("为本次任务配置材料", key=f"material-enable-{scene_id}"):
                defaults = material_capability["defaults"]
                reset = st.checkbox("恢复场景默认值", key=f"material-default-{scene_id}")
                values = {} if reset else dict(prior_values)
                if not reset:
                    for name in material_capability["editable"]:
                        field = MATERIAL_FIELDS[name]
                        current = float(prior_values.get(name, defaults[name]))
                        value = st.number_input(
                            f"{field['label']}（{field['unit']}）", min_value=float(field["min"]),
                            max_value=float(field["max"]), value=current, format="%.9g",
                            key=f"material-{scene_id}-{name}",
                        )
                        if value != current:
                            values[name] = value
                material = {"object": "cloth", "values": values, "previous": dict(prior_values)}
                st.dataframe(material_rows(scene_id, material), hide_index=True, width="stretch")
    else:
        st.caption("该场景使用预设材料。当前接口支持三个单布片目标场景。")
    signature=(source,scene_id,kind,capture,tuple(sorted(frames)),timeout,
               tuple(sorted(material["values"].items())) if material is not None else None)
    inputs_changed(ctx,signature)
    request={"schema_version":"task-request-v2" if scene_id!="default" or kind=="自定义帧数" or capture else "task-request-v1","request_id":ctx["intent_id"],
             "mode":"custom","scene":scene_id,
             "backend_id":"c0-003-benchagent-v1","task_frames":frames,"timeout_sec":int(timeout)}
    if capture:request["native_render"]=True
    if material is not None:request["material"]=material
    label="预览计划"
    if st.button("预览计划", type="primary", width="stretch", key="preview"):
        _preview(service, request)


def _edit_plan(service, plan):
    """Apply edits through the same draft validator and freeze a new plan."""
    from .conversation_service import task_request
    req = plan["execution_snapshot"]["request"]
    key = "plan-edit-" + plan["plan_id"]
    with st.expander("调整任务计划"):
        scene = st.selectbox("计划场景", list(SCENE_LABELS), index=list(SCENE_LABELS).index(req["scene"]),
                             format_func=scene_display, key=key + "-scene")
        same_scene = scene == req["scene"]
        spec = SCENES[scene]
        frames = st.number_input("计划运行帧数", min_value=spec["frames"]["min"],
                                 max_value=spec["frames"]["max"], value=req["task_frames"][0],
                                 step=1, key=key + "-frames-" + scene)
        patch = copy.deepcopy(req)
        patch.update(schema_version="task-request-v2", scene=scene, mode="custom", task_frames=[frames])
        if not same_scene:
            patch.pop("material", None)
            st.caption("更换场景使用目标场景默认材料，可在下方继续配置。")
        material = material_spec(scene)
        if material:
            baseline = req.get("material", {}).get("values", {}) if same_scene else {}
            current = {**material["defaults"], **baseline}
            values = dict(baseline)
            reset = st.checkbox("恢复默认材料参数", key=key + "-reset-" + scene)
            if reset:
                values = {}
            else:
                for name in material["editable"]:
                    field = MATERIAL_FIELDS[name]
                    value = st.number_input(field["label"] + "（" + field["unit"] + "）",
                        min_value=float(field["min"]), max_value=float(field["max"]),
                        value=float(current[name]), format="%.9g", key=key + "-" + scene + "-" + name)
                    if value != current[name]:
                        values[name] = value
            if values != baseline or (reset and "material" in req):
                patch["material"] = {"object": material["object"], "values": values, "previous": dict(baseline)}
        material_changed = patch.get("material", {}).get("values", {}) != req.get("material", {}).get("values", {})
        changed = scene != req["scene"] or frames != req["task_frames"][0] or material_changed
        if changed:
            st.info("有尚未应用的修改，请先更新计划预览。")
        if st.button("应用修改并重新预览", key=key + "-apply", disabled=not changed, width="stretch"):
            _preview(service, task_request(new_id("intent"), patch))
        return changed


def plan_panel(service, source):
    from .ui_app import context, error

    ctx = context()
    if source == SOURCES[0]:
        form_controls(service)
    plan = ctx["plan"]
    if not plan:
        st.markdown('<div class="smart-empty"><strong>等待生成任务计划</strong><p>在左侧描述需求，或切换手动表单配置。</p></div>',
                    unsafe_allow_html=True)
        with st.expander("统一测试规则"):
            st.write("固定帧数提供 20、50、100 帧快捷选项，自定义帧数按所选场景能力校验。")
            st.caption("当前启用双容差残差补偿 TOI + PCG 双段 Graph + indexed。超时用于执行保护。")
        return
    snapshot = plan["execution_snapshot"]
    req = snapshot["request"]
    gpu = (snapshot.get("profile_identity") or {}).get("gpu", "待启动时复核")
    cards = (("测试场景", SCENES[req["scene"]]["display_name"]), ("运行帧数", " / ".join(map(str, req["task_frames"])) + " 帧"),
             ("求解配置", "工程当前配置"), ("计算设备", "GPU " + str(gpu) if gpu != "待启动时复核" else gpu))
    st.markdown('<div class="smart-summary-grid">' + "".join(
        '<div class="smart-summary-card"><span>' + label + '</span><strong title="' +
        escape(value, quote=True) + '">' + escape(value) + '</strong></div>' for label, value in cards) + '</div>',
        unsafe_allow_html=True)
    st.caption(f"草稿第 {plan['draft_revision']} 版 · 已保存到实验记录")
    material = req.get("material")
    spec = material_spec(req["scene"])
    if material or spec:
        st.markdown("#### 材料参数变更")
        rows = material_rows(req["scene"], material or {"object": spec["object"], "values": {}, "previous": {}})
        table = pd.DataFrame([{**{k: row[k] for k in ("参数", "单位", "修改前", "计划值")},
                               "变更状态": "已修改" if row["修改前"] != row["计划值"] else "保持不变"} for row in rows])
        st.dataframe(table.style.format({"修改前": "{:.9g}", "计划值": "{:.9g}"}).apply(
            lambda row: ["background-color: #f1edff" if row["变更状态"] == "已修改"
                         else "" for _ in row], axis=1), hide_index=True, width="stretch")
        st.caption("修改前值取自场景默认配置或上一轮已校验计划。")
    dirty = _edit_plan(service, plan) if source != SOURCES[0] else False
    st.markdown("#### 计划完整性检查")
    st.markdown('<div class="smart-checks"><span>✓ 场景已通过能力校验</span><span>✓ 运行帧数已校验</span>'
                '<span>✓ 材料与执行配置已校验</span><span class="pending">○ GPU 环境在运行前复核</span></div>',
                unsafe_allow_html=True)
    st.markdown('<div class="smart-task-note"><b>任务执行摘要</b><p>' + escape(_plan_summary(req)) +
                ('，应用上方材料配置。' if material else '，沿用场景默认材料。') + '</p></div>', unsafe_allow_html=True)
    audit = plan["provenance"].get("planning_audit")
    if audit:
        if audit["outcome"] == "LOCAL_PATCH_VALIDATED":
            st.warning("DeepSeek 补丁校验未通过，已按本轮明确修改生成本地预览，请核对后确认。")
        else:
            st.caption(f"DeepSeek 已响应并通过计划校验 · API 调用 {audit['provider_calls']} 次")
    with st.expander("冻结计划、执行程序与统一测试规则"):
        runtime = snapshot.get("native_runtime") or snapshot.get("runtime_identity") or {}
        st.caption("场景标识 · " + req["scene"])
        st.caption("实际程序 · " + str(runtime.get("executable", "见运行前身份校验")))
        st.caption("输出帧耗时、Newton/PCG 迭代和源码口径碰撞计数。超时为执行保护上限。")
        st.caption("计划标识 · " + plan["plan_id"])
        st.code(plan["plan_digest"])
        st.json(snapshot)
    # Preserve the existing explicit confirmation boundary and submission identities.
    st.caption("确认此版本后将实际启动 GPU 仿真。")
    a, b = st.columns([1, 1.6])
    if a.button("保存草稿", key="smart-save-draft", disabled=dirty, width="stretch"):
        if service.plans.get_plan(plan["plan_id"]):
            st.success("当前草稿已保存 · " + plan["plan_id"])
    if b.button("确认并开始执行", type="primary", key="submit", width="stretch",
                disabled=dirty or ctx["mode"] != "plan_preview"):
        try:
            receipt = service.submit(plan["plan_id"], plan["plan_digest"],
                                     ctx["confirmation_request_id"], ctx["execution_request_id"])
            select_job(ctx, receipt["job_id"])
            st.session_state["_smart_next_tab"] = TABS[1]
            st.rerun()
        except Exception as exc:
            error(exc)


def monitor_panel(service):
    from .ui_app import context
    from .ui_telemetry import job_panel
    ctx = context()
    if ctx["mode"] == "live" and ctx["job_id"]:
        job_panel(service, ctx["job_id"], show_results=False)
    else:
        st.markdown("#### 运行监控")
        st.info("确认计划并开始执行后，显示真实完成帧数、运行阶段与增量日志。")
        st.progress(0, text="尚未启动")
        st.code("等待任务启动，运行日志将在此显示。", language=None, height=150)


@st.fragment(run_every="2s")
def result_panel(service):
    from .ui_app import context, results_dashboard
    from .ui_exports import export_panel
    ctx = context()
    report_id = ctx["archive_id"] if ctx["mode"] == "archive" else None
    if ctx["mode"] == "live" and ctx["job_id"]:
        job = service.job(ctx["job_id"])
        if job["state"] in TERMINAL:
            report_id = job.get("report_id")
    if not report_id:
        st.markdown("#### 测试结果报告")
        st.info("任务完成并归档后，显示真实性能指标、原生关键帧与报告导出。")
        st.caption("当前尚未产生本次任务的结果数据。")
        return
    view = service.report(report_id)
    case, case_id = results_dashboard(service, view, key="smart-result")
    if case is not None:
        export_panel(service, report_id, case_id, view["kind"] == "suite")


def smart_page(service):
    from .ui_app import context
    ctx = context()
    with st.container(key="smart-layout"):
        left, right = st.columns([.84, 1.16], gap="medium")
        with left, st.container(border=True, key="smart-chat-panel"):
            source = assistant_panel(service)
        with right, st.container(border=True, key="smart-task-panel"):
            st.markdown("### 测试任务工作区")
            state = {"empty": "等待生成计划", "plan_preview": "待确认计划", "live": "已提交任务", "archive": "历史归档"}
            st.caption("对话生成计划，确认后执行，结果自动归档 · " + state[ctx["mode"]])
            next_tab = st.session_state.pop("_smart_next_tab", None)
            if next_tab:
                st.session_state["smart-tab"] = next_tab
            if st.session_state.get("smart-tab") == "目标驱动分析":
                st.session_state["smart-tab"] = TABS[3]
            tabs = st.tabs(TABS, key="smart-tab", on_change="rerun")
            # Keep plan inputs mounted while monitoring so their values and task binding survive tab switches.
            with tabs[0]:
                plan_panel(service, source)
            with tabs[1]:
                monitor_panel(service)
            with tabs[2]:
                result_panel(service)
            with tabs[3]:
                from .ui_objectives import objective_panel
                objective_panel(service)
