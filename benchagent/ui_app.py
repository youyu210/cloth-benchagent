"""V3.1 six-navigation Streamlit workbench. All execution goes through AppService."""
from html import escape
import json
import pandas as pd
import streamlit as st
from .common import ROOT
from .materials import MATERIAL_FIELDS, material_rows, material_spec
from .app_service import AppService
from .planning_store import PlanningError, new_id
from .export_service import ExportError
from .job_manager import TERMINAL
from .presentation import check_table, diagnostic_text, display_value, status_text
from .view_models import metric_rows, keyframe_rows, KPI_KEYS
from .ui_models import (NAVIGATION, JOB_LABELS, PHASE_LABELS, READ_LABELS, initial_context,
    inputs_changed, activate_plan, select_job, select_archive, phase_markup, case_summary_rows)

@st.cache_resource
def get_app_service():
    return AppService()

def context():
    if "workspace" not in st.session_state:st.session_state.workspace=initial_context()
    return st.session_state.workspace

def error(exc):
    if isinstance(exc,(PlanningError,ExportError)):
        messages={"PLAN_STALE":"计划已过期，请重新预览后提交。",
                  "DRAFT_CONFLICT":"草稿已更新，请读取最新版本。",
                  "MANAGER_BUSY":"已有工作器或遗留所有权记录，请到系统设置查看占用。",
                  "CLEANUP_REQUIRED":"进程清理尚未确认，已阻止后续执行。",
                  "REQUEST_CONFLICT":"本次操作标识已绑定其他计划，请重新预览。",
                  "SERVICE_BUSY":"服务暂忙，请重试本次操作。",
                  "UNSUPPORTED_REQUEST":"请输入已开放场景和合法帧数，例如「运行场景5，37帧」。",
                  "INVALID_REQUEST":"请检查所选场景、帧数与测试模式。",
                  "STALE_EXPORT":"来源报告或导出版本已变化，请重新生成。",
                  "CORRUPT_EXPORT":"产物校验失败，请重新生成。",
                  "PDF_NOT_ACCEPTED":"当前PDF版本尚未完成渲染验收。",
                  "EXPORT_BUSY":"相同导出正在生成，请稍后重试。",
                  "JOB_ACTIVE":"任务尚未归档，请等待最终报告。",
                  "SOURCE_CHANGED":"生成期间来源发生变化，请重新生成。",
                  "NO_REPORT":"归档尚未形成报告入口，暂不可导出。"}
        st.error(messages.get(exc.code,str(exc).split(": ",1)[-1]))
    else:st.error(str(exc))

def source_badge(text):
    st.markdown('<div class="source-badge">'+escape(text)+'</div>',unsafe_allow_html=True)

def goto_report(report_id):
    select_archive(context(),report_id)
    st.session_state.navigation="测试报告"

def goto_job(job_id):
    select_job(context(),job_id)
    st.session_state["_smart_next_tab"]="运行监控"
    st.session_state.navigation="智能测试"

def cards(view=None):
    metrics=view["metrics"] if view else None
    labels=("平均每帧耗时","平均牛顿迭代","平均PCG迭代","牛顿迭代总次数","PCG迭代总次数","累计碰撞对计数")
    units=("毫秒/帧","次/帧","次/帧","次","次","次（源码口径）")
    parts=[]
    for key,label,unit in zip(KPI_KEYS,labels,units):
        item=metrics[key] if metrics else None
        value=display_value(item["value"],item["format"]) if item else "未采集"
        parts.append('<div class="kpi"><div class="kpi-label">'+label+'</div><div class="kpi-value">'+escape(str(value))+'</div><div class="kpi-unit">'+unit+'</div></div>')
    st.markdown('<div class="kpi-grid">'+"".join(parts)+'</div>',unsafe_allow_html=True)

def charts(view):
    frames=view["frames"]
    left,right=st.columns(2)
    with left:
        st.markdown("**帧耗时曲线**")
        if frames and any(f["solver_time_ms"] is not None for f in frames):
            data=pd.DataFrame({"帧序号":[f["frame"] for f in frames],"物理步耗时（毫秒）":[f["solver_time_ms"] for f in frames]})
            st.line_chart(data,x="帧序号",y="物理步耗时（毫秒）",color="#7850e6",height=210)
        else:st.info("未采集到逐帧耗时。")
    with right:
        st.markdown("**逐帧迭代曲线**")
        if frames and any(f.get("newton_iterations") is not None or f.get("pcg_iterations") is not None for f in frames):
            data=pd.DataFrame({"帧序号":[f["frame"] for f in frames],
                "牛顿迭代（次/帧）":[f["newton_iterations"] for f in frames],
                "PCG迭代（次/帧）":[f["pcg_iterations"] for f in frames]})
            st.line_chart(data,x="帧序号",y=["牛顿迭代（次/帧）","PCG迭代（次/帧）"],
                          color=["#a16aef","#4267e7"],height=210)
        else:st.info("未记录逐帧迭代数据。")

def selected_case(view,key):
    if view["kind"]!="suite":return view,None
    cases=view["cases"]
    if not cases:
        st.warning("此归档没有可展示的子报告。");return None,None
    options=[c["case_id"] for c in cases]
    default=view.get("selected_case_id") or options[0]
    current=st.session_state.get(key)
    if current not in options:st.session_state[key]=default
    choice=st.selectbox("分析用例",options,key=key,format_func=lambda cid:
        str(next(c["metrics"]["actual_frames"]["value"] for c in cases if c["case_id"]==cid) or "未记录")+"帧 · "+cid)
    return next(c for c in cases if c["case_id"]==choice),choice

def results_dashboard(service,view,*,key="dashboard",show_logs=True,fixed_case_id=None):
    if view["kind"]=="suite":
        st.dataframe(case_summary_rows(view),hide_index=True,width="stretch")
    if fixed_case_id is not None and view["kind"]=="suite":
        matched=[c for c in view["cases"] if c["case_id"]==fixed_case_id]
        if len(matched)!=1:
            st.warning("所选用例尚无匹配报告，未用其他用例代替。")
            return
        case,case_id=matched[0],fixed_case_id
    else:case,case_id=selected_case(view,key+"-case")
    if case is None:return
    if case["data_source"]["producer_kind"] == "synthetic":
        st.info("此记录为合成数据，已停止展示。")
        return
    source_badge("历史归档 · "+case["report_id"]+" · "+READ_LABELS.get(case["archive_read_status"],"未知"))
    raw_plan=(case.get("raw_report") or {}).get("plan") or {}
    a,b=st.columns([.85,1.25])
    with a,st.container(border=True):
        st.markdown("### 运行概览")
        x,y=st.columns([.8,1.2])
        with x:
            if ((case.get("raw_report") or {}).get("visualization") or {}).get("kind") == "native_render_frame":
                st.caption("原生画面见下方，可按归档帧号切换。")
            elif raw_plan.get("scene")=="fixed-cloth":st.caption("画面见下方归档帧查看。")
            else:st.caption("该归档暂无可查看的仿真画面。")
        with y:
            st.write(scene_display(raw_plan.get("scene")))
            n=case["metrics"]["actual_frames"]["value"]
            st.write("实际帧数："+(str(n) if n is not None else "未采集"))
            st.write("原校验结论："+status_text(case["recorded_result_status"]))
    native_frame_panel(service,case,key)
    if raw_plan.get("scene") == "fixed-cloth":
        with st.expander("基于真实仿真状态的几何回放（非原生渲染）"):
            if st.checkbox("查看归档帧",key=key+"-replay-"+str(case_id)):
                try:
                    replay=service.cloth_replay(case["report_id"])
                    if replay is not None:
                        if replay.full:
                            frame=st.slider("仿真帧",0,replay.last,key=key+"-frame-"+str(case_id))
                        else:
                            frame=st.selectbox("已归档帧",[0,replay.last],key=key+"-frame-"+str(case_id))
                        st.image(replay.png(frame),caption=f"固定布料 · 第 {frame} 帧 · 真实状态渲染")
                        if replay.full:
                            play_key=key+"-play-"+case["report_id"]
                            if st.button("生成循环播放",key=play_key):
                                gif,count=replay.animation()
                                st.session_state["cloth_animation"]=(case["report_id"],gif,count)
                            animation=st.session_state.get("cloth_animation")
                            if animation and animation[0]==case["report_id"]:
                                _,gif,count=animation
                                st.image(gif,caption=f"固定布料 · 循环预览 · 采样 {count} 帧")
                                st.download_button("下载回放 GIF",gif,"cloth-replay.gif","image/gif",
                                                   key=play_key+"-download",on_click="ignore")
                        else:
                            st.caption("该任务只归档了初态和末帧。")
                except (OSError,ValueError,KeyError):
                    st.warning("该归档的几何数据缺失或与原始资产不匹配。")
    with b,st.container(border=True):
        st.markdown("### 关键指标（均值 / 总计）")
        cards(case)
    with st.container(border=True):
        st.markdown("### 性能分析")
        charts(case)
        if case["recorded_result_status"]=="PASS":st.success("原记录的运行与统计证据校验通过；物理精度未评估。")
        else:st.info("沿用原报告结论，请结合逐项检查和异常查看。")
    with st.container(border=True):
        st.markdown("### 详细数据（关键帧）")
        st.dataframe(keyframe_rows(case),hide_index=True,width="stretch")
        st.caption("逐帧碰撞缺项显示“未记录”，不从累计值反推。")
    if show_logs:
        with st.container(border=True):
            st.markdown("### 运行日志（摘要）")
            stream=st.radio("日志流",["stdout","stderr"],horizontal=True,key=key+"-stream",
                format_func=lambda s:"标准输出" if s=="stdout" else "标准错误")
            st.code(service.archive_log(view["report_id"],case_id,stream),language=None,height=180)
            st.caption("尾部快照，最多16000字节；完整日志保留在归档中。")
    return case,case_id

from .conversation_service import DIALOGUE_DEFAULT_FRAMES, SCENE_LABELS, SCENES, scene_display

def native_frame_panel(service, case, key):
    visual=(case.get("raw_report") or {}).get("visualization") or {}
    if visual.get("kind")=="no_visualization":
        st.caption("本次原生画面未生成。"+str(visual.get("reason","")))
    if visual.get("kind")!="native_render_frame":
        return
    with st.expander("原生 OpenGL 画面"):
        frame=st.selectbox("已采集的原生帧",visual["frames"],
                           key=key+"-native-frame-"+case["report_id"])
        try:
            scene = (case["raw_report"].get("plan") or {}).get("scene")
            st.image(service.native_frame(case["report_id"], frame),
                     caption=f"{scene_display(scene)} · 原生 OpenGL · 第 {frame} 帧")
            st.caption(f"采集耗时 {visual['capture_ms']:.1f} 毫秒，单列于求解计时之外。")
        except (OSError, ValueError, KeyError):
            st.warning("归档原生帧不可读或校验失败。")


def smart_page(service):
    from .ui_smart import smart_page as render_smart_page
    render_smart_page(service)


def history_page(service):
    st.markdown("### 活动与已登记任务")
    jobs=service.jobs()
    if jobs:
        st.dataframe([{"任务标识":j["job_id"],"任务状态":JOB_LABELS.get(j["state"],j["state"]),
            "校验结论":status_text(j["result_status"]) if j["result_status"] else "尚未产生",
            "用例帧数":" / ".join(str(c["frames"]) for c in j["cases"]),"登记时间（UTC）":j["created_at"]}
            for j in jobs],hide_index=True,width="stretch")
        job=st.selectbox("选择任务",[j["job_id"] for j in jobs],key="history-job")
        st.button("查看任务",on_click=goto_job,args=(job,))
    else:st.info("尚无已登记任务。")
    st.markdown("### 历史归档（只读）")
    labels={"全部":None,"通过":"PASS","未通过":"FAIL","部分通过":"PARTIAL","已跳过":"SKIPPED"}
    selected=st.selectbox("原校验结论筛选",list(labels),key="history-filter")
    records=service.records(labels[selected])
    if not records:st.info("没有符合筛选条件的归档。");return
    st.dataframe([{"归档标识":v["report_id"],"类型":"任务集" if v["kind"]=="suite" else "单项",
       "原校验结论":status_text(v["recorded_result_status"]),
       "读取状态":READ_LABELS.get(v["archive_read_status"],"未知")} for v in records],hide_index=True,width="stretch")
    report=st.selectbox("选择归档",[v["report_id"] for v in records],key="history-report")
    st.button("查看归档报告",on_click=goto_report,args=(report,))

def report_page(service):
    records=service.records()
    if not records:st.info("尚无测试报告。");return
    ids=[v["report_id"] for v in records]
    wanted=context()["archive_id"]
    if wanted in ids and st.session_state.get("_report_context")!=wanted:
        st.session_state["report_select"]=wanted;st.session_state["_report_context"]=wanted
    report_id=st.selectbox("报告归档",ids,key="report_select")
    view=service.report(report_id)
    source_badge("只读历史回放 · "+report_id+" · 原判定保持不变")
    case,case_id=selected_case(view,"report-case-"+report_id)
    if case is None:return
    tabs=st.tabs(["概览","运行配置","详细指标与检查","日志与异常"])
    with tabs[0]:
        if view["kind"]=="suite":st.dataframe(case_summary_rows(view),hide_index=True,width="stretch")
        st.write("原记录结论："+status_text(view["recorded_result_status"]))
        st.caption("归档读取状态："+READ_LABELS.get(view["archive_read_status"],"未知"))
        native_frame_panel(service,case,"report")
        cards(case);charts(case)
        st.dataframe(keyframe_rows(case),hide_index=True,width="stretch")
    with tabs[1]:
        st.caption("选中用例的原始运行配置；缺失项不回填当前配置。")
        st.json(case["raw_report"].get("plan",{}) if case["raw_report"] else {})
        raw=case.get("raw_report") or {}
        material_plan = raw.get("plan") or {}
        if "material" in material_plan:
            evidence = raw.get("material_evidence")
            st.markdown("**实际生效的布片材料**")
            if evidence is None:
                st.warning("尚无通过校验的原生材料证据。")
            st.dataframe(material_rows(material_plan["scene"], material_plan["material"],
                                       evidence["effective"] if evidence else {}), hide_index=True, width="stretch")
            if evidence:
                st.caption("证据来源 · inputs/material.json、raw/material_effective.json、raw/masses.f64")
        st.write("共用实现："+str(raw.get("implementation_commit") or "未记录"))
        if raw.get("execution_identity"):
            st.json(raw["execution_identity"])
    with tabs[2]:
        st.dataframe(metric_rows(case),hide_index=True,width="stretch")
        if case["checks"] is None:st.info("历史报告未记录检查清单，不补判通过。")
        else:st.dataframe(check_table(case["checks"]),hide_index=True,width="stretch")
    with tabs[3]:
        for name,title in (("errors","异常"),("warnings","告警")):
            st.markdown("**"+title+"**")
            values=(case["raw_report"] or {}).get(name,[])
            if not values:st.caption("无记录。")
            for message in values:st.write(diagnostic_text(message))
        for issue in [*view.get("issues",[]),*case.get("issues",[])]:st.warning(issue["message"])
        for stream,label in (("stdout","标准输出"),("stderr","标准错误")):
            with st.expander(label+"（尾部快照）"):
                st.code(service.archive_log(report_id,case_id,stream),language=None,height=180)
    from .ui_exports import export_panel
    export_panel(service,report_id,case_id,view["kind"]=="suite")
    with st.expander("已有原始Markdown文件"):
        original=service.original_markdown(report_id,case_id)
        if original:st.download_button("下载原始 Markdown",data=original,
            file_name=report_id.replace("/","_")+"-original.md",mime="text/markdown")
        else:st.caption("原始Markdown未记录；可使用上方统一导出。")

def profiles_page(service):
    profile=service.configuration()
    st.markdown("### 当前启用配置")
    args=profile["args"]
    def argument(flag):
        return str(args[args.index(flag)+1]) if flag in args and args.index(flag)+1<len(args) else "未记录"
    rows=[{"参数":"配置标识","固定值":profile["backend_id"]},{"参数":"默认场景编号","固定值":str(profile["scene"])},
          {"参数":"GPU选择","固定值":profile["gpu"]},{"参数":"TOI阈值","固定值":argument("--toi-threshold")},
          {"参数":"归一化残差阈值","固定值":argument("--residual-threshold")},{"参数":"最少接受更新数","固定值":argument("--toi-min-updates")},
          {"参数":"牛顿容差倍率","固定值":argument("--newton-tolerance-scale")},{"参数":"PCG容差倍率","固定值":argument("--pcg-tolerance-scale")},
          {"参数":"PCG执行方式","固定值":"双段CUDA Graph" if "--pcg-dual-graph" in args else "未启用"},{"参数":"矩阵转换","固定值":"indexed汇总" if "--converter-indexed-reduce" in args else "未启用"},
          {"参数":"原始提交","固定值":profile["base_commit"]},{"参数":"共用实现","固定值":profile["implementation_commit"]}]
    st.dataframe(rows,hide_index=True,width="stretch")
    st.info("物理与求解参数只读。允许的帧数与超时在智能测试页选择。")
    with st.expander("原始配置与参数数组"):st.json(profile)

def settings_page(service):
    st.markdown("### 运行环境")
    if st.button("检查运行环境",key="inspect"):
        try:service.inspect_environment()
        except Exception as exc:error(exc)
    value=service.environment()
    if value:
        st.write("就绪状态："+("检查通过" if value["ready"] else "尚未就绪"))
        st.caption("记录时间（UTC）："+value["observed_at"]+" · 来源："+value["source"])
        st.code(value.get("gpu") or "未记录GPU信息",language=None)
        for message in value.get("errors",[]):st.warning(diagnostic_text(message))
    else:st.info("尚无环境检查记录。")
    st.markdown("### 执行占用")
    health=service.health()
    st.dataframe([{"状态项":label,"当前状态":"是" if health[key] else "否"} for key,label in [
        ("worker_started","本服务工作器已启动"),("execution_lock","执行锁存在"),("manager_lock","工作器所有权记录存在"),("blocked","后续执行被阻止")]],
        hide_index=True,width="stretch")
    st.caption("未知归属和遗留锁不自动删除。关闭网页不会取消已提交的任务。")
    st.markdown("### 模型接口")
    model=service.model_status(st.session_state.get("deepseek_api_key"))
    st.info("DeepSeek V4 Pro · "+("已配置密钥" if model["ready"] else "未配置密钥"))
    st.caption("密钥仅保存在当前工作台会话内存中。重载页面或重新打开工作台后，需要重新输入。")
    with st.form("deepseek_settings", clear_on_submit=True):
        api_key=st.text_input("DeepSeek API 密钥",type="password")
        if st.form_submit_button("保存密钥",type="primary"):
            if api_key.strip():
                st.session_state["deepseek_api_key"]=api_key.strip()
                st.rerun()
            else:
                st.warning("请输入 DeepSeek API 密钥。")
    if st.session_state.get("deepseek_api_key") and st.button("清除会话密钥"):
        del st.session_state["deepseek_api_key"]
        st.rerun()

def main():
    st.set_page_config(page_title="布料仿真BenchAgent · 智能工作台",layout="wide",initial_sidebar_state="expanded")
    st.markdown("<style>"+(ROOT/"benchagent/ui_assets/workbench.css").read_text(encoding="utf-8")+"</style>",unsafe_allow_html=True)
    service=get_app_service()
    context()
    with st.sidebar:
        st.markdown('<div class="brand"><em>◇</em>布料仿真BenchAgent</div>',unsafe_allow_html=True)
        route=st.radio("主导航",NAVIGATION,key="navigation",label_visibility="collapsed")
        with st.container(key="sidebar-footer"):
            st.markdown('<div class="model-status"><strong>DeepSeek V4 Pro</strong><small>可在计划入口选择模型对话</small></div>',unsafe_allow_html=True)
            st.caption("CUDA · 本地工作区")
    environment=service.environment()
    gpu="待载入"
    if environment and environment.get("gpu"):
        fields=environment["gpu"].splitlines()[0].split(",")
        gpu=fields[2].strip() if len(fields)>2 else "已有检查记录"
    title="智能测试工作台" if route=="智能测试" else route
    subtitle = "" if route == "智能测试" else "<small>布料仿真BenchAgent · DeepSeek V4</small>"
    st.markdown('<header class="work-header"><h1>'+escape(title)+subtitle+'</h1>'
        '<div class="header-tags"><span>项目路径：D:\\benchagent</span><span>运行环境：'+escape(gpu)+'</span></div></header>',unsafe_allow_html=True)
    if environment and route != "智能测试":st.caption("环境来源："+environment["source"]+" · 记录时间（UTC）："+environment["observed_at"])
    pages={"智能测试":smart_page,"运行记录":history_page,
           "测试报告":report_page,"配置管理":profiles_page,"系统设置":settings_page}
    try:pages[route](service)
    except Exception as exc:error(exc)
