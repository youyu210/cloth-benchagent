"""Bound live progress and incremental log components."""
import streamlit as st
from .job_manager import TERMINAL
from .ui_models import JOB_LABELS,PHASE_LABELS,live_case_view,job_phase_markup
from .presentation import status_text
from .view_models import keyframe_rows

def incremental_log_panel(service,job_id,case_id):
    st.markdown("### 运行日志（增量）")
    follow=st.checkbox("跟随日志",value=True,key="follow-log-"+job_id)
    stream=st.radio("日志流",["stdout","stderr"],horizontal=True,key="live-stream-"+job_id,
        format_func=lambda name:"标准输出" if name=="stdout" else "标准错误")
    key="_log_cursor_"+job_id+"_"+case_id+"_"+stream
    value=service.log_slice(job_id,case_id,stream,st.session_state.get(key),follow=follow)
    st.session_state[key]=value
    st.code(value["text"] or "尚未收到可解码的日志。",language=None,height=190)
    st.caption(("正在跟随" if follow else "已暂停跟随，仿真继续运行")+
        " · 字节游标："+str(value["offset"])+" · 用例："+case_id)
    if value["display_truncated"]:st.caption("界面仅保留最近32000字符，原始日志完整保留。")
    for issue in value["issues"]:st.warning(issue["message"])

@st.fragment(run_every="2s")
def job_panel(service,job_id,*,show_results=True):
    from .ui_app import source_badge,cards,charts,results_dashboard,goto_report,error
    cache_key="_frame_cursors_"+job_id
    telemetry=service.telemetry(job_id,st.session_state.get(cache_key))
    st.session_state[cache_key]=telemetry["cursors"]
    job=telemetry["job"]
    from .conversation_service import scene_display
    source_badge("当前任务 · "+job_id)
    snapshot=job.get("execution_snapshot") or {}
    request=snapshot.get("request") or {}
    runtime=snapshot.get("native_runtime") or snapshot.get("runtime_identity") or {}
    implementation=(snapshot.get("source_identity") or {}).get("implementation_commit")
    if request.get("scene"):
        st.caption(scene_display(request["scene"]))
    if runtime.get("executable"):
        st.caption("执行程序 · "+runtime["executable"]+" · 共用实现 "+str(implementation or "未记录")[:8])
    left,right=st.columns([2,1])
    left.markdown("**任务状态："+JOB_LABELS.get(job["state"],job["state"])+"**")
    right.caption("目标："+str(telemetry["target_total"])+"帧")
    st.progress(telemetry["fraction"],text="已采集 "+str(telemetry["confirmed_total"])+" / "+
        str(telemetry["target_total"])+" 帧（采集进度）")
    st.caption("采集进度与校验结论独立；失败和取消后保持实际已采集帧数。")
    follow_case=st.checkbox("跟随当前用例",value=True,key="follow-case-"+job_id)
    ids=[case["case_id"] for case in telemetry["cases"]]
    if job["state"] in TERMINAL:
        preferred=max(telemetry["cases"],key=lambda c:c["confirmed_frames"])["case_id"]
    else:preferred=job.get("active_case_id") or ids[0]
    selected_key="_observed_case_"+job_id
    if st.session_state.get(selected_key) not in ids or follow_case:
        st.session_state[selected_key]=preferred
    selected=st.selectbox("实时分析用例",ids,key=selected_key,disabled=follow_case,
        format_func=lambda cid:str(next(c["target_frames"] for c in telemetry["cases"] if c["case_id"]==cid))+"帧 · "+cid)
    case=next(c for c in telemetry["cases"] if c["case_id"]==selected)
    st.markdown(job_phase_markup(job,selected),unsafe_allow_html=True)
    if job.get("waiting_for_lease"):st.warning("正在等待执行占用释放；尚未启动本任务。")
    st.dataframe([{"用例":str(c["target_frames"])+"帧","已采集帧数":c["confirmed_frames"],
        "目标帧数":c["target_frames"],"阶段":PHASE_LABELS.get(c["phase"],c["phase"]),
        "校验结论":status_text(c["result_status"]) if c["result_status"] else "尚未产生"} for c in telemetry["cases"]],
        hide_index=True,width="stretch")
    for issue in case["issues"]:st.warning(issue["message"])
    if case["eta_seconds"] is not None:
        st.caption("当前用例剩余物理步估算："+format(case["eta_seconds"],".1f")+"秒；不含初始化、校验和其他用例。")
    if job["state"] not in TERMINAL:
        st.info("实时观测仅基于已完整采集的连续帧，最终校验结论尚未产生。")
        observation=live_case_view(job_id,case)
        with st.container(border=True):
            st.markdown("### 实时观测 · "+str(case["confirmed_frames"])+"帧")
            cards(observation)
            if case["frames"]:
                charts(observation)
                st.dataframe(keyframe_rows(observation),hide_index=True,width="stretch")
        if st.button("请求取消",key="cancel-"+job_id):
            try:
                receipt=service.cancel(job_id,"ui-cancel-"+job_id)
                st.info({"FINALIZING":"正在收尾，将完成必要归档。","TERMINAL":"任务已结束。",
                         "REQUESTED":"已收到取消请求，等待进程退出确认。"}[receipt["status"]])
            except Exception as exc:error(exc)
    else:
        st.write("校验结论："+(status_text(job["result_status"]) if job["result_status"] else "未产生完整结论"))
        if job.get("error"):st.error(job["error"])
        if job.get("failure_kind")=="CLEANUP_REQUIRED":st.error("清理尚未确认，保留执行锁，后续任务已阻止。")
        if job.get("report_id") and show_results:
            results_dashboard(service,service.report(job["report_id"]),key="job-"+job_id,
                              show_logs=False,fixed_case_id=selected)
            if st.button("查看完整报告",on_click=goto_report,args=(job["report_id"],),key="full-"+job_id):
                st.rerun()
    with st.container(border=True):incremental_log_panel(service,job_id,selected)
