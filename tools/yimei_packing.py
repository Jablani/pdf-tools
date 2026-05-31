import streamlit as st
import os
import shutil
import zipfile
import tempfile
import fitz
import pandas as pd
import re
from pathlib import Path


def parse_detail_pdf(pdf_path):
    """从 detail PDF 中提取 PKG 号、PO 号、Item 列表及其数量"""
    doc = fitz.open(pdf_path)
    text = doc[0].get_text()
    doc.close()

    lines = [line.strip() for line in text.split("\n") if line.strip()]

    pkg_number = None
    po_number = None
    items = []  # [(item_code, qty), ...]

    in_item_section = False
    prev_line_is_item_header = False

    for i, line in enumerate(lines):
        upper = line.upper()
        # 提取 PKG 号
        if pkg_number is None and ("PKG#" in upper or "PKG-" in upper):
            pkg_number = line
        # 提取 PO 号
        if po_number is None and ("PO#" in upper or "PO #" in upper or upper.startswith("PO")):
            po_number = line
        # 检测 Item/Qty 表头
        if upper == "ITEM":
            in_item_section = True
            continue
        if upper == "QTY" and in_item_section and not prev_line_is_item_header:
            prev_line_is_item_header = True
            continue
        # 解析 Item/Qty 数据对
        if in_item_section:
            if prev_line_is_item_header:
                # 第一个数字是 Item 后面的数字是 Qty
                clean = line.rstrip(" ").rstrip(".")
                if clean.isdigit() and int(clean) > 0:
                    items.append((clean, 0))  # 先记 qty=0，下次更新
                    prev_line_is_item_header = False
                else:
                    in_item_section = False
            elif items and items[-1][1] == 0:
                # 这是 Qty 值
                match = re.search(r"(\d+)", line)
                if match:
                    items[-1] = (items[-1][0], int(match.group(1)))
                else:
                    in_item_section = False
            else:
                # 新的 Item
                match = re.search(r"(\d+)", line)
                if match and in_item_section:
                    # 可能是 Item + Qty 在同一行，或 Item 单独一行
                    val = match.group(1)
                    # 检查该行是否有多个数字（Item和Qty）
                    nums = re.findall(r"\d+", line)
                    if len(nums) >= 2:
                        items.append((nums[0], int(nums[1])))
                    else:
                        items.append((val, 0))
                        # 下一行应该是 Qty
                        if i + 1 < len(lines):
                            next_match = re.search(r"(\d+)", lines[i + 1])
                            if next_match:
                                items[-1] = (val, int(next_match.group(1)))

    return pkg_number, po_number, items


def parse_detail_pdf_v2(pdf_path):
    """更健壮的 detail PDF 解析器"""
    doc = fitz.open(pdf_path)
    text = doc[0].get_text()
    doc.close()

    lines = [line.strip() for line in text.split("\n")]

    pkg_number = None
    po_number = None

    # 提取 PKG 号
    for line in lines:
        clean = line.strip()
        if "PKG#" in clean or "PKG-" in clean:
            # 提取完整的 PKG 号
            match = re.search(r"PKG[#\-]\d+", clean)
            if match:
                pkg_number = match.group()
                break

    # 提取 PO 号
    for line in lines:
        clean = line.strip()
        if clean.upper().startswith("PO"):
            # 清理 PO 前缀和多余符号
            po_clean = re.sub(r"^PO\s*[#\-]?\s*", "", clean, flags=re.IGNORECASE)
            po_clean = po_clean.strip(" -#")
            # 处理跨行情况 (如 "R10368668-" 和 "P11559722")
            po_number = po_clean
            break

    # 提取 Item-Qty 对
    # 找到 Item 和 Qty 表头之后的数据
    item_start_idx = None
    for i, line in enumerate(lines):
        if line.strip().upper() == "ITEM":
            item_start_idx = i
            break

    items = []
    if item_start_idx is not None:
        # 从 Item 后面开始找数据
        # 格式通常是交替的: Item1, Qty1, Item2, Qty2, ...
        data_lines = []
        for line in lines[item_start_idx + 1:]:
            clean = line.strip().rstrip(" .")
            if clean.isdigit() and int(clean) > 0:
                data_lines.append(clean)
            # 也匹配 "Qty11" 这种总数量行（跳过）
            if re.match(r"^Qty\s*\d+", clean, re.IGNORECASE):
                continue
            if re.match(r"^Total\s*Qty", clean, re.IGNORECASE):
                continue

        # 成对读取: Item, Qty
        i = 0
        while i + 1 < len(data_lines):
            items.append((data_lines[i], int(data_lines[i + 1])))
            i += 2

    return pkg_number, po_number, items


def get_pkg_from_filename(filename):
    """从文件名中提取 PKG 号，如 detail_PKG#20264144.pdf -> PKG#20264144"""
    s = str(filename)
    match = re.search(r"PKG[#\-]\d+", s)
    return match.group() if match else None


def process_single_pkg(detail_path, label_path, excel_df, output_dir):
    """处理单个 PKG 的所有文件"""
    logs = []
    pkg_number = get_pkg_from_filename(detail_path)
    if not pkg_number:
        raise ValueError(f"无法从文件名提取 PKG 号: {detail_path}")

    # 1. 解析 detail PDF
    parsed_pkg, po_number, items = parse_detail_pdf_v2(detail_path)
    if not items:
        raise ValueError(f"detail PDF 中未找到 Item 数据: {detail_path}")

    total_qty = sum(qty for _, qty in items)
    logs.append(f"PKG: {pkg_number}, PO: {po_number}, Items: {items}, Total Qty: {total_qty}")

    # 2. 验证 label 页数
    label_doc = fitz.open(label_path)
    label_page_count = label_doc.page_count
    label_doc.close()

    if label_page_count != total_qty:
        raise ValueError(
            f"PKG {pkg_number}: detail 中 Qty 总和 ({total_qty}) 与 label PDF 页数 ({label_page_count}) 不一致"
        )
    logs.append(f"验证通过: label 页数 ({label_page_count}) = Qty 总和 ({total_qty})")

    # 3. 从 Excel 中获取该 PKG 对应的行数据（# 和 - 视为可互换）
    pkg_normalized = pkg_number.replace("-", "#")
    pkg_rows = excel_df[
        excel_df["Order Number"].astype(str).str.replace("-", "#", regex=False).str.contains(pkg_normalized, na=False)
    ]
    if pkg_rows.empty:
        raise ValueError(f"Excel 中未找到 PKG {pkg_number} 对应的行")

    # 获取该 PKG 的 Provider, Agent, Carrier（取第一行）
    first_row = pkg_rows.iloc[0]
    provider = str(first_row.get("Provider", ""))
    agent = str(first_row.get("Agent", ""))
    carrier = str(first_row.get("Carrier", ""))

    # 统一使用 PKG# 格式作为输出文件夹名
    pkg_display = pkg_normalized
    # 4. 创建输出文件夹
    out_folder = Path(output_dir) / pkg_display
    out_folder.mkdir(parents=True, exist_ok=True)

    # 5. 拆分 label PDF，按 Item 顺序分配页面（全局序号）
    label_doc = fitz.open(label_path)
    current_page = 0
    global_seq = 0
    label_files = []  # [(filename, page_index_in_original), ...]

    for item_code, item_qty in items:
        for _ in range(item_qty):
            new_doc = fitz.open()
            new_doc.insert_pdf(label_doc, from_page=current_page, to_page=current_page)

            global_seq += 1
            label_filename = f"label_{pkg_display}_{item_code}_{global_seq:02d}.pdf"
            label_path_out = out_folder / label_filename
            new_doc.save(str(label_path_out))
            new_doc.close()

            label_files.append((label_filename, item_code))
            current_page += 1

    label_doc.close()

    # 6. 验证拆分后的 label 文件数
    if current_page != label_page_count:
        raise ValueError(
            f"PKG {pkg_number}: 拆分出的 label 数 ({current_page}) 与原 label 页数 ({label_page_count}) 不一致"
        )
    logs.append(f"验证通过: 拆分 label 数 ({current_page}) = 原 label 页数 ({label_page_count})")
    logs.append(f"生成 {len(label_files)} 个 label PDF")

    # 7. 复制并重命名 detail PDF（使用第一个 Item 作为后缀）
    first_item_code = items[0][0]
    detail_out_name = f"detail_{pkg_display}_{first_item_code}_01.pdf"
    detail_out_path = out_folder / detail_out_name
    shutil.copy2(detail_path, detail_out_path)
    logs.append(f"detail PDF -> {detail_out_name}")

    # 8. 生成输出 Excel
    excel_rows = []
    for idx, (label_filename, item_code) in enumerate(label_files, 1):
        excel_rows.append({
            "No.": idx,
            "Provider": provider,
            "Order Number": f"{pkg_display}_{item_code}_{idx:02d}",
            "Agent": agent,
            "Carrier": carrier,
            "Tracking Number": "",
            "FBA": item_code,
            "QTY": 1,
        })

    # 获取上传 Excel 的文件名（去掉扩展名）
    excel_name = "output"
    for col in excel_df.columns:
        if "No." in str(col):
            break
    # 使用原始 Excel 文件名作为前缀
    excel_out_name = f"{excel_name}_{pkg_display}.xlsx"
    excel_out_path = out_folder / excel_out_name

    out_df = pd.DataFrame(excel_rows)
    out_df.to_excel(excel_out_path, index=False)
    logs.append(f"Excel -> {excel_out_name}")

    return logs


def show_ui(user_info, update_usage_callback):
    """医美 Packing List 工具的 Streamlit UI"""
    st.title("🏥 医美 Packing List 工具 v1.0")

    st.markdown("""
    **功能说明：** 自动解析 detail PDF 中的 Item 和 Qty 信息，按 Item 拆分 label PDF，生成重命名后的 detail PDF、拆分的 label PDF 和对应的 Excel 文件。

    **支持的上传格式：** `.zip` 压缩包

    **压缩包结构要求：**
    ```
    任意文件夹名/
    ├── xxx.xlsx                     ← Excel 映射表（必需）
    ├── detail_PKG#xxxxxxx.pdf       ← 一个或多个 detail PDF
    ├── label_PKG#xxxxxxx.pdf        ← 与 detail 同名的 label PDF
    ├── detail_PKG#yyyyyyy.pdf
    ├── label_PKG#yyyyyyy.pdf
    └── ...
    ```
    **注意事项：**
    - Excel 必须包含列：No.、Provider、Order Number、Agent、Carrier、Tracking Number、FBA、QTY
    - detail 和 label 文件名中的 PKG 号必须一致（如 `detail_PKG#20264144.pdf` 与 `label_PKG#20264144.pdf`）
    - label PDF 的页数必须等于 detail 中所有 Item 的 Qty 总和
    """)

    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    if today > user_info["expiry_date"]:
        st.error("❌ 账号已过期")
        return
    if user_info["used_count"] >= user_info["total_limit"]:
        st.error("❌ 使用次数已耗尽")
        return

    col1, col2 = st.columns([1, 2])
    with col1:
        st.subheader("参数配置")
        zip_file = st.file_uploader("上传 ZIP 包", type=["zip"])

    with col2:
        st.subheader("执行状态")
        if zip_file:
            if st.button("开始处理", type="primary"):
                with st.spinner("处理中..."):
                    try:
                        with tempfile.TemporaryDirectory() as td:
                            td_path = Path(td)
                            extract_path = td_path / "extracted"

                            # 解压 ZIP
                            zip_path = td_path / "upload.zip"
                            with open(zip_path, "wb") as f:
                                f.write(zip_file.getbuffer())

                            with zipfile.ZipFile(zip_path) as z:
                                z.extractall(extract_path)

                            # 查找所有 PDF、Excel 文件（排除 macOS 元数据）
                            all_files = [f for f in extract_path.rglob("*") if f.is_file() and "__MACOSX" not in str(f) and f.name != ".DS_Store"]
                            pdf_files = [f for f in all_files if f.suffix.lower() == ".pdf"]
                            excel_files = [f for f in all_files if f.suffix.lower() in (".xlsx", ".xls")]

                            if not excel_files:
                                st.error("❌ 未找到 Excel 映射文件")
                                return

                            # 读取 Excel
                            excel_df = pd.read_excel(excel_files[0])
                            required_cols = ["No.", "Provider", "Order Number", "Agent", "Carrier", "Tracking Number", "FBA", "QTY"]
                            missing = [c for c in required_cols if c not in excel_df.columns]
                            if missing:
                                st.error(f"❌ Excel 缺少列: {missing}")
                                return

                            # 查找 detail PDF 并配对
                            detail_files = [f for f in pdf_files if f.name.lower().startswith("detail_")]
                            label_files = [f for f in pdf_files if f.name.lower().startswith("label_")]

                            if not detail_files:
                                st.error("❌ 未找到 detail PDF 文件")
                                return

                            all_logs = []
                            output_dir = td_path / "output"
                            output_dir.mkdir()

                            processed_pkgs = 0
                            for detail_path in sorted(detail_files):
                                pkg_number = get_pkg_from_filename(detail_path.name)
                                if not pkg_number:
                                    continue

                                # 查找对应的 label PDF
                                matching_label = None
                                for label_path in label_files:
                                    if get_pkg_from_filename(label_path.name) == pkg_number:
                                        matching_label = label_path
                                        break

                                if not matching_label:
                                    all_logs.append(f"⚠️ PKG {pkg_number}: 未找到对应的 label PDF")
                                    continue

                                try:
                                    pkg_logs = process_single_pkg(
                                        str(detail_path), str(matching_label), excel_df, str(output_dir)
                                    )
                                    all_logs.append(f"=== {pkg_number} ===")
                                    all_logs.extend(pkg_logs)
                                    processed_pkgs += 1
                                except Exception as e:
                                    all_logs.append(f"❌ {pkg_number}: {str(e)}")

                            if processed_pkgs > 0:
                                update_usage_callback(user_info["username"])
                                st.success(f"✅ 成功处理 {processed_pkgs} 个 PKG")

                                # 打包输出文件为 ZIP 供下载
                                zip_out = td_path / "output.zip"
                                with zipfile.ZipFile(zip_out, "w", zipfile.ZIP_DEFLATED) as zout:
                                    for f in output_dir.rglob("*"):
                                        if f.is_file():
                                            arcname = f.relative_to(output_dir)
                                            zout.write(f, arcname)

                                with open(zip_out, "rb") as f:
                                    st.download_button(
                                        "📥 下载结果 ZIP",
                                        data=f.read(),
                                        file_name="yimei_output.zip",
                                        mime="application/zip",
                                    )

                                with st.expander("查看日志"):
                                    for log_line in all_logs:
                                        st.text(log_line)
                            else:
                                st.error("❌ 没有成功处理的 PKG")
                                with st.expander("查看日志"):
                                    for log_line in all_logs:
                                        st.text(log_line)

                    except Exception as e:
                        st.error(f"运行出错: {str(e)}")
        else:
            st.info("💡 请上传 ZIP 包。")
