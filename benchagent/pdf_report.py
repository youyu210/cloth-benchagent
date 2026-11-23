"""Chinese PDF reports with embedded project font and vector plots."""
import hashlib
import json
import threading
from pathlib import Path
from html import escape
from importlib.metadata import version
from .common import ROOT,sha
from .export_document import cases
from .presentation import display_value,status_text
from .view_models import metric_rows,keyframe_rows,ARCHIVE_STATUS_TEXT

FONT_PATH=ROOT/"dependencies/fonts/simhei.ttf"
FONT_NAME="BenchAgentCJK"
_LOCK=threading.RLock()

def fingerprint():
    return {"renderer_sha256":sha(__file__),"font_sha256":sha(FONT_PATH),
             "reportlab_version":version("reportlab"),"layout_version":"chinese-a4-v1",
            "view_code_sha256":sha(Path(__file__).with_name("view_models.py")),
            "presentation_code_sha256":sha(Path(__file__).with_name("presentation.py")),
            "document_code_sha256":sha(Path(__file__).with_name("export_document.py"))}

def render_pdf(snapshot,path):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak,KeepTogether
    from reportlab.graphics.shapes import Drawing,Line,String,PolyLine,Circle
    with _LOCK:
        if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(FONT_NAME,str(FONT_PATH)))
            pdfmetrics.registerFontFamily(FONT_NAME,normal=FONT_NAME,bold=FONT_NAME,italic=FONT_NAME,boldItalic=FONT_NAME)
        glyphs=pdfmetrics.getFont(FONT_NAME).face.charToGlyph
        substitutions=set();truncated=[0]
        def clean(value,limit=6000):
            if not isinstance(value,str):value=json.dumps(value,ensure_ascii=False,sort_keys=True)
            value=value.replace("\r","").replace("\u2013","-").replace("\u2014","-")
            if len(value)>limit:
                value=value[:limit]+"（已截断，完整内容见JSON或证据包）";truncated[0]+=1
            result=[]
            for char in value:
                if char in "\n\t" or glyphs.get(ord(char),0)>0:result.append(char)
                else:
                    substitutions.add(ord(char));result.append("[U+%04X]"%ord(char))
            return escape("".join(result)).replace("\n","<br/>").replace("\t","  ")
        body=ParagraphStyle("Body",fontName=FONT_NAME,fontSize=9,leading=14,wordWrap="CJK",textColor=colors.HexColor("#302748"),spaceAfter=6)
        tiny=ParagraphStyle("Tiny",parent=body,fontSize=7.6,leading=11,spaceAfter=2)
        h1=ParagraphStyle("Title",parent=body,fontSize=22,leading=30,textColor=colors.HexColor("#5832ba"),spaceAfter=14)
        h2=ParagraphStyle("Heading",parent=body,fontSize=14,leading=22,spaceBefore=10,spaceAfter=8,keepWithNext=True)
        note=ParagraphStyle("Note",parent=body,fontSize=8,leading=12,textColor=colors.HexColor("#716383"))
        def paragraph(value,style=body):return Paragraph(clean(value),style)
        story=[]
        def text(value,style=body):story.append(paragraph(value,style))
        def table(headers,rows,widths):
            data=[[paragraph(str(v),tiny) for v in headers]]
            for row in rows:data.append([paragraph(str(v),tiny) for v in row])
            t=Table(data,colWidths=widths,repeatRows=1,hAlign="LEFT")
            t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eee7fb")),
                ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#faf8fe")]),
                ("LINEBELOW",(0,0),(-1,0),.7,colors.HexColor("#c6b9e8")),
                ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),6),
                ("RIGHTPADDING",(0,0),(-1,-1),6),("TOPPADDING",(0,0),(-1,-1),5),
                ("BOTTOMPADDING",(0,0),(-1,-1),5)]))
            if len(rows)<=6:story.append(KeepTogether([t,Spacer(1,8)]))
            else:story.append(t);story.append(Spacer(1,8))
        def plots(view):
            rows=view["frames"];drawing=Drawing(515,165)
            def plot(x,title,series):
                width=235;bottom=28;height=102
                drawing.add(String(x+width/2,150,title,fontName=FONT_NAME,fontSize=9,textAnchor="middle",fillColor=colors.HexColor("#3c2f60")))
                all_values=[r.get(key) for key,_,_ in series for r in rows if r.get(key) is not None]
                if not all_values:
                    drawing.add(String(x+width/2,78,"未采集数据",fontName=FONT_NAME,fontSize=9,textAnchor="middle"))
                    return
                maximum=max(all_values) or 1
                for frac in (0,.5,1):
                    y=bottom+height*frac
                    drawing.add(Line(x+32,y,x+width,y,strokeColor=colors.HexColor("#e3ddef"),strokeWidth=.4))
                    drawing.add(String(x+27,y-3,format(maximum*frac,".1f"),fontName=FONT_NAME,fontSize=7,textAnchor="end"))
                n=max((r["frame"] for r in rows),default=1)
                drawing.add(String(x+32,12,"1",fontName=FONT_NAME,fontSize=7))
                drawing.add(String(x+width,12,str(n),fontName=FONT_NAME,fontSize=7,textAnchor="end"))
                for key,label,color in series:
                    chunk=[]
                    def flush():
                        if len(chunk)>=4:drawing.add(PolyLine(chunk[:],strokeColor=colors.HexColor(color),strokeWidth=1))
                        elif chunk:drawing.add(Circle(chunk[0],chunk[1],1.5,fillColor=colors.HexColor(color),strokeColor=None))
                        chunk.clear()
                    for row in rows:
                        value=row.get(key)
                        if value is None:flush();continue
                        chunk.extend([x+32+(row["frame"]-1)/max(1,n-1)*(width-32),bottom+value/maximum*height])
                    flush()
                drawing.add(String(x+width/2,0,"帧序号",fontName=FONT_NAME,fontSize=7,textAnchor="middle"))
            plot(0,"帧耗时（毫秒）",[("solver_time_ms","物理步","#7951db")])
            plot(275,"迭代次数（次/帧）",[("newton_iterations","牛顿","#a16aef"),("pcg_iterations","PCG","#4267e7")])
            story.append(drawing)
            text("图例：紫色为牛顿迭代，蓝色为PCG迭代；左图为物理步耗时。",note)
        def page(canvas,doc):
            canvas.setStrokeColor(colors.HexColor("#bba9e8"));canvas.line(40,804,555,804)
            canvas.setFillColor(colors.HexColor("#4e368b"));canvas.setFont(FONT_NAME,9)
            canvas.drawString(40,816,"布料仿真BenchAgent")
            canvas.drawRightString(555,816,"基准测试报告")
            canvas.setFillColor(colors.HexColor("#8a7e9c"));canvas.setFont(FONT_NAME,7.5)
            canvas.drawString(40,20,"只读归档导出 · 物理精度未评估")
            canvas.drawRightString(555,20,"第 "+str(doc.page)+" 页")
        text("布料仿真BenchAgent 基准测试报告",h1)
        text("来源归档："+snapshot["report_id"])
        text("范围："+(snapshot["case_id"] or "整个报告"))
        text("来源摘要："+snapshot["source_digest"],note)
        view=snapshot["view"]
        text("原记录结论："+status_text(view["recorded_result_status"])+"；归档读取："+ARCHIVE_STATUS_TEXT.get(view["archive_read_status"],"未知"))
        text("此报告读取既有证据，不重跑仿真或按新版规则重判。采集完整与校验通过分开，不能据此推断无穿透、物理精度或性能加速。",note)
        if view["data_source"]["producer_kind"]=="synthetic":text("合成样例：所有数值仅用于展示，不是实际性能成绩。",h2)
        if view["kind"]=="suite":
            text("任务集概览",h2)
            summary=[]
            for case in cases(view):
                m=case["metrics"]
                summary.append([case.get("case_id") or "单项",status_text(case["recorded_result_status"]),
                    display_value(m["actual_frames"]["value"],"count"),
                    display_value(m["solver_time_mean_ms_per_frame"]["value"],m["solver_time_mean_ms_per_frame"]["format"]),
                    display_value(m["pcg_mean_per_frame"]["value"],m["pcg_mean_per_frame"]["format"])])
            table(["用例","原结论","实际帧数","平均帧耗时（毫秒）","平均PCG（次/帧）"],summary,[100,55,65,145,150])
        for issue in view.get("issues",[]):text("读取说明："+issue["message"],note)
        for index,case in enumerate(cases(view)):
            if view["kind"]=="suite" or index:story.append(PageBreak())
            text("用例："+str(case.get("case_id") or "单项"),h2)
            text("原结论："+status_text(case["recorded_result_status"])+"；读取状态："+ARCHIVE_STATUS_TEXT.get(case["archive_read_status"],"未知"))
            text("指标明细",h2)
            rows=metric_rows(case)
            table(["指标","数值","单位"],[[r["指标"],r["数值"],r["单位"]] for r in rows],[255,145,115])
            text("曲线与关键帧",h2)
            plots(case)
            keys=keyframe_rows(case)
            if keys:
                table(["帧号","耗时（毫秒）","牛顿次数","PCG次数","碰撞对计数"],
                      [[r["帧号"],r["物理步耗时（毫秒）"],r["牛顿迭代次数"],r["PCG 迭代次数（聚合）"],r["碰撞对计数"]] for r in keys],
                      [45,120,100,100,150])
            else:text("未记录逐帧数据。",note)
            text("逐项检查",h2)
            if case["checks"] is None:text("历史记录未包含有效检查清单，不补判通过。")
            else:
                for check in case["checks"]:
                    begin=len(story)
                    text(check["id"]+" "+check["name"]+"："+status_text(check["status"]))
                    for field,label in (("expected","期望"),("actual","实际"),("evidence","证据"),("message","说明")):
                        if field in check:text(label+"："+(check[field] if isinstance(check[field],str) else json.dumps(check[field],ensure_ascii=False)),tiny)
                    story.append(Spacer(1,4))
                    block=story[begin:];del story[begin:]
                    story.append(KeepTogether(block))
            text("运行配置与异常",h2)
            raw=case.get("raw_report") or {}
            text(json.dumps(raw.get("plan",{}),ensure_ascii=False,indent=2),tiny)
            for field,label in (("errors","异常"),("warnings","告警")):
                values=raw.get(field,[])
                text(label+"："+("；".join(str(v) for v in values) if values else "无记录。"),note)
            for issue in case.get("issues",[]):text("读取说明："+issue["message"],note)
            for stream,label in (("stdout","标准输出"),("stderr","标准错误")):
                source=case["report_id"]+"/"+stream+".log"
                if source in snapshot["files"]:
                    text(label+"摘要",h2)
                    content=snapshot["files"][source].decode("utf-8",errors="replace")
                    snippet=content[-6000:]
                    if len(content)>6000:text("仅显示末尾6000字符，完整日志见ZIP证据包。",note)
                    text(snippet or "空日志。",tiny)
        text("导出溯源",h2)
        text("视图版本："+view["view_schema"]+"；来源文件数："+str(len(snapshot["source_index"])),note)
        text("原始JSON与二进制证据保持原字节，统一JSON及证据包可复核来源。",note)
        doc=SimpleDocTemplate(str(path),pagesize=A4,rightMargin=40,leftMargin=40,
                             topMargin=58,bottomMargin=40,title="布料仿真BenchAgent 基准测试报告",author="BenchAgent",pageCompression=1)
        doc.build(story,onFirstPage=page,onLaterPages=page)
        return {"font_embedded":True,"unsupported_codepoints":["U+%04X"%n for n in sorted(substitutions)],
                "truncated_fields":truncated[0],"log_tail_characters":6000,"renderer":fingerprint()}
