import streamlit as st
import os
import shutil
import zipfile
import tempfile
import fitz
import pandas as pd
from pathlib import Path
from collections import defaultdict

def get_sku_from_page(page):
    """从 PDF 页面提取 UPC/EAN 编号"""
    text = page.get_text()
    lines = text.split("\n")
    found_upc = None
    for i, line in enumerate(lines):
        clean = line.upper().replace(" ", "")
        if "EAN" in clean and i+1 < len(lines):
            val = lines[i+1].strip()
            if val.isdigit(): return val
        if "UPC" in clean and i+1 < len(lines):
            val = lines[i+1].strip()
            if val.isdigit(): found_upc = val
    return found_upc

def run_workflow_logic(zip_file, excel_df, ship_key, cart_key):
    """核心处理逻辑：重命名 -> 排序 -> 合并"""
    log = []
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td)
        ex_p = tp / "extracted"
        with zipfile.ZipFile(zip_file) as z:
            z.extractall(ex_p)
        
        input_folder = ex_p / "input"
        input_folder.mkdir(exist_ok=True)
        
        # 1. 模拟 pdf_organizer.py 的归集重命名
        all_folders = [f for f in ex_p.iterdir() if f.is_dir() and f.name != "input"]
        for folder in all_folders:
            folder_name = folder.name
            pdfs = list(folder.glob("*.pdf"))
            s_pdf, c_pdf = None, None
            for p in pdfs:
                n = p.name.lower()
                if ship_key in n: s_pdf = p
                if cart_key in n: c_pdf = p
            if s_pdf:
                new_n = f"{folder_name}-2.pdf"
                shutil.copy2(s_pdf, input_folder / new_n)
            if c_pdf:
                new_n = f"{folder_name}-1.pdf"
                shutil.copy2(c_pdf, input_folder / new_n)

        # 2. 加载 UPC->SKU 映射
        upc_to_sku_map = {}
        for _, row in excel_df.iterrows():
            u = str(int(row['productName'])) if pd.notna(row['productName']) else None
            s = str(row['productSku']) if pd.notna(row['productSku']) else "Unknown"
            if u: upc_to_sku_map[u] = s

        # 3. 建立 A/B 配对并识别 SKU
        groups = {}
        files = sorted([f for f in os.listdir(input_folder) if f.endswith(".pdf")])
        for f in files:
            if f.endswith("-1.pdf"):
                base = f.replace("-1.pdf", "")
                b_f = f"{base}-2.pdf"
                if b_f in files: groups[base] = {"A": input_folder / f, "B": input_folder / b_f}

        sku_map = defaultdict(list)
        for base, paths in groups.items():
            docA = fitz.open(paths["A"])
            for i in range(len(docA)):
                sku = get_sku_from_page(docA[i])
                if sku: sku_map[sku].append((paths["A"], paths["B"], i))
            docA.close()

        # 4. 生成最终 PDF
        out = fitz.open()
        b_page_indices = []
        sorted_skus = sorted(sku_map.keys(), key=lambda x: int(x) if x.isdigit() else 0)
        
        total_skus = len(sorted_skus)  # 获取本次任务的总 SKU 数量

        for idx_sku, sku in enumerate(sorted_skus):
            sku_count = 0
            for a_path, b_path, page_i in sku_map[sku]:
                docA = fitz.open(a_path)
                docB = fitz.open(b_path)
                
                # 删除 B 最后一页（空页）
                if len(docB) > 0:
                    docB.delete_page(len(docB)-1)

                # 插入 A 页 (箱标)
                out.insert_pdf(docA, from_page=page_i, to_page=page_i)
                # 插入 B 页 (面单)
                out.insert_pdf(docB, from_page=page_i, to_page=page_i)
                
                b_page_indices.append(len(out) - 1)
                
                docA.close()
                docB.close()
                sku_count += 1

            # 5. 插入 UPC/SKU 间隔页
            page = out.new_page()
            w, h = page.rect.width, page.rect.height
            sku_val = upc_to_sku_map.get(sku, "Unknown")
            
            fontsize = 35

            start_y = h * 0.1
            line_height = fontsize * 1.3
            box_height = line_height + 20  # 关键！

            # 上部
            page.insert_textbox(
                fitz.Rect(w*0.05, start_y, w, start_y + box_height),
                f"UPC: {sku}",
                fontsize=fontsize,
                align=0
            )

            # 中部
            page.insert_textbox(
                fitz.Rect(w*0.05, start_y + box_height, w, start_y + box_height * 2),
                f"SKU: {sku_val}",
                fontsize=fontsize,
                align=0
            )

            # 下部
            page.insert_textbox(
                fitz.Rect(w*0.05, start_y + box_height * 2, w, start_y + box_height * 3),
                f"QTY: {sku_count}",
                fontsize=fontsize,
                align=0
            )

            # 底部
            set_text = f"SET {idx_sku + 1} OF {total_skus}"
            page.insert_textbox(
                fitz.Rect(0, h * 0.9, w, h),
                set_text,
                fontsize=30,
                align=1
            )                                                           
            
            log.append(f"✓ {sku} ({set_text}) 处理完成")

        # 6. 旋转所有 B 页
        for idx in b_page_indices:
            p = out[idx]
            p.set_rotation((p.rotation + 90) % 360)

        pdf_bytes = out.tobytes()
        out.close()
        return pdf_bytes, log

def show_ui(user_info, update_usage_callback):
    """在该函数中定义 UPS 工具的所有右侧界面逻辑"""
    st.title("📦 UPS 自动化工作流v2.6 ")
    
    st.markdown("""
    **功能说明：**
    1. 上传子文件夹 ZIP 包，自动识别 carton（箱标）和 shipping（面单）PDF。
    2. 按 UPC 排序，逐组合并 carton+shipping 页面，中间插入间隔页显示 SKU 和数量。
    3. 生成统一的 UPS 处理 PDF。
    """)
    
    st.info("提示：ZIP 结构必须一级目录为订单文件夹，需配合 UPC_SKU 映射表。")
    
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    if today > user_info['expiry_date']:
        st.error("❌ 账号已过期")
        return
    if user_info['used_count'] >= user_info['total_limit']:
        st.error("❌ 使用次数已耗尽")
        return

    col1, col2 = st.columns([1, 2])
    with col1:
        st.subheader("参数配置")
        #s_key = st.text_input("面单关键词", "small")
        s_key = st.selectbox('面单关键词:',['shipping','small','自定义'])
        if s_key == "自定义":
            s_key = st.text_input('请输入面单关键词')
        #c_key = st.text_input("箱标关键词", "carton")
        c_key = st.selectbox('箱标关键词:',['carton','自定义'])
        if c_key == "自定义":
            c_key = st.text_input('请输入箱标关键词')
        zip_f = st.file_uploader("上传子文件夹 ZIP 包", type=['zip'])
        excel_f = st.file_uploader("上传 UPC_SKU 映射表 (Excel)", type=['xlsx'])
    
    with col2:
        st.subheader("执行状态")
        if zip_f and excel_f:
            if st.button("开始自动化处理", type="primary"):
                with st.spinner("处理中..."):
                    try:
                        excel_df = pd.read_excel(excel_f)
                        pdf_bytes, logs = run_workflow_logic(zip_f, excel_df, s_key.lower(), c_key.lower())
                        update_usage_callback(user_info['username'])
                        st.success("✅ 处理成功！")
                        st.download_button("📥 下载结果 PDF", data=pdf_bytes, file_name="UPS_Final_Output.pdf")
                        with st.expander("查看日志"):
                            for l in logs: st.text(l)
                    except Exception as e:
                        st.error(f"运行出错: {str(e)}")
        else:
            st.info("💡 请上传必要素材。")
