"""Explicit export generation; opening a page does not create artifacts."""
import json
import streamlit as st

FORMATS={"PDF":"pdf","Markdown":"markdown","JSON":"json","CSV":"csv","ZIP 证据包":"zip"}

def export_panel(service,report_id,case_id,is_suite):
    from .ui_app import error
    st.markdown("### 报告导出")
    scope=st.radio("导出范围",["当前用例","整个任务集"] if is_suite else ["当前报告"],
                   horizontal=True,key="export-scope")
    selected_case=case_id if is_suite and scope=="当前用例" else None
    label=st.selectbox("导出格式",list(FORMATS),key="export-format")
    format=FORMATS[label]
    capabilities=service.export_capabilities()
    disabled=format=="pdf" and not capabilities["pdf_ready"]
    if disabled:st.info(capabilities["pdf_reason"])
    signature=(report_id,selected_case,format)
    if st.session_state.get("_export_selection")!=signature:
        st.session_state["_export_selection"]=signature
        st.session_state["_export_artifact"]=None
    st.caption("统一读取当前报告视图；CSV缺项留空，ZIP保留原目录和源文件。PDF单独导出。")
    if selected_case is not None:
        st.caption("当前用例导出保留任务集摘要用于溯源，仅打包所选用例的详细证据。")
    if st.button("生成导出文件",type="primary",disabled=disabled,key="generate-export"):
        try:
            receipt=service.export_report(report_id,format,selected_case)
            st.session_state["_export_artifact"]=receipt["artifact_id"]
        except Exception as exc:error(exc)
    artifact_id=st.session_state.get("_export_artifact")
    if artifact_id:
        try:
            manifest,data=service.export_content(artifact_id)
            st.success("导出已就绪 · "+label)
            a,b=st.columns(2)
            a.download_button("下载 "+label,data=data,file_name=manifest["download_name"],
                              mime=manifest["mime"],width="stretch",key="download-export")
            b.download_button("下载产物清单",data=(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n").encode("utf-8"),
                              file_name=manifest["download_name"]+".manifest.json",mime="application/json",width="stretch")
            st.caption("来源摘要："+manifest["source_digest"])
            st.caption("文件SHA256："+manifest["file"]["sha256"])
        except Exception as exc:
            st.session_state["_export_artifact"]=None
            error(exc)
