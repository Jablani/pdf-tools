import streamlit as st
import sqlite3
import pandas as pd
import hashlib
import uuid
import os
from datetime import datetime, timedelta

# --- 导入业务插件目录中的 UI 渲染函数 ---
from tools import ups_v2_6

# 数据库存储路径
DB_PATH = "users.db"


def ensure_auth_column():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("PRAGMA table_info(users)")
    cols = [row[1] for row in c.fetchall()]
    if "auth_token" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN auth_token TEXT")
    conn.commit()
    conn.close()


def init_db():
    """初始化数据库并创建管理员账号"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (username TEXT PRIMARY KEY,
                  password TEXT,
                  role TEXT,
                  expiry_date TEXT,
                  total_limit INTEGER,
                  used_count INTEGER,
                  auth_token TEXT)''')
    conn.commit()
    conn.close()
    ensure_auth_column()
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    admin_pw = hashlib.sha256("admin123".encode()).hexdigest()
    c.execute("INSERT OR IGNORE INTO users (username, password, role, expiry_date, total_limit, used_count) VALUES (?, ?, ?, ?, ?, ?)",
              ('admin', admin_pw, 'admin', '2099-12-31', 999999, 0))
    conn.commit()
    conn.close()


def check_user(username, password):
    """验证登录"""
    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM users WHERE username=? AND password=?", conn, params=(username, pw_hash))
    conn.close()
    return not df.empty


def get_user_data(username):
    """获取指定用户的所有权限信息"""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM users WHERE username=?", conn, params=(username,))
    conn.close()
    return df.iloc[0] if not df.empty else None


def update_usage(username):
    """扣除用户使用额度（回调函数，传递给 tools 脚本）"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET used_count = used_count + 1 WHERE username=?", (username,))
    conn.commit()
    conn.close()


def set_user_auth_token(username):
    token = str(uuid.uuid4())
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET auth_token=? WHERE username=?", (token, username))
    conn.commit()
    conn.close()
    return token


def clear_user_auth_token(username):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET auth_token=NULL WHERE username=?", (username,))
    conn.commit()
    conn.close()


def get_user_by_token(token):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT username FROM users WHERE auth_token=?", conn, params=(token,))
    conn.close()
    if df.empty:
        return None
    return df.loc[0, 'username']


# --- 页面配置 ---
st.set_page_config(page_title="Kirk's 自动化平台 V1.1", layout="wide", page_icon="🔧")
init_db()

# Session 状态管理
if 'menu_choice' not in st.session_state:
    st.session_state.menu_choice = "UPS 工具"
if 'auth' not in st.session_state:
    st.session_state.auth = False

# 0. 从 URL token 尝试恢复登录（刷新后保持状态）
if not st.session_state.auth:
    token = st.query_params.get("token")
    if token:
        user_from_token = get_user_by_token(token)
        if user_from_token:
            st.session_state.auth = True
            st.session_state.user = user_from_token

# 1. 登录逻辑
if not st.session_state.auth:
    st.title("🔐 登录中心")
    user = st.text_input("用户名")
    pw = st.text_input("密码", type="password")
    if st.button("登录", use_container_width=True):
        if check_user(user, pw):
            st.session_state.auth = True
            st.session_state.user = user
            token = set_user_auth_token(user)
            st.query_params["token"] = token
            st.rerun()
        else:
            st.error("用户名或密码错误")

# 2. 已登录主界面
else:
    u_info = get_user_data(st.session_state.user)

    # --- 侧边栏框架 ---
    st.sidebar.title(f"👋 你好，{u_info['username']}")
    st.sidebar.info(f"剩余: {u_info['total_limit'] - u_info['used_count']} 次\n\n过期: {u_info['expiry_date']}")
    st.sidebar.markdown("---")

    # 纵向平铺菜单区
    st.sidebar.subheader("🚀 功能中心")
    if st.sidebar.button("📦 UPS 处理工具", use_container_width=True):
        st.session_state.menu_choice = "UPS 工具"

    if st.sidebar.button("📦 VC 处理工具", use_container_width=True):
        st.session_state.menu_choice = "VC 工具"
    if st.sidebar.button("📂 BOL 插入OB", use_container_width=True):
        st.session_state.menu_choice = "BOL 工具"

    if u_info['role'] == 'admin':
        st.sidebar.markdown("---")
        st.sidebar.subheader("🛠 系统管理")
        if st.sidebar.button("⚙️ 用户管理后台", use_container_width=True):
            st.session_state.menu_choice = "管理后台"

    st.sidebar.markdown("---")
    if st.sidebar.button("🚪 登出", use_container_width=True):
        clear_user_auth_token(st.session_state.user)
        st.session_state.auth = False
        st.session_state.user = ''
        st.query_params.clear()
        st.rerun()

    # --- 右侧主界面渲染逻辑 ---
    cur = st.session_state.menu_choice

    if cur == "UPS 工具":
        # 🚀 业务逻辑完全外包给 tools/ups_v2_6.py
        ups_v2_6.show_ui(u_info, update_usage)
    elif cur == "VC 工具":
        # 🚀 业务逻辑完全外包给 tools/vc_pdf_app.py
        from tools import vc_pdf_app
        vc_pdf_app.show_ui(u_info, update_usage)
    elif cur == "BOL 工具":
        # 🚀 业务逻辑完全外包给 tools/bol_app.py
        from tools import bol_app
        bol_app.show_ui(u_info, update_usage)

    elif cur == "管理后台":
        st.title("🛠 用户管理控制台")
        conn = sqlite3.connect(DB_PATH)
        df_users = pd.read_sql("SELECT username, role, expiry_date, total_limit, used_count FROM users", conn)
        st.dataframe(df_users, use_container_width=True)
        with st.expander("➕ 编辑用户"):
            target_user = st.text_input("目标用户名 (新增或修改)")
            new_role = st.selectbox('用户组：', ['user', 'admin'])
            new_pw = st.text_input("密码 (修改时不填则保留原密码)")
            new_expiry = st.date_input("到期日期", datetime.now() + timedelta(days=365))
            new_limit = st.number_input("次数上限", value=100)
            if st.button("保存更改"):
                c = conn.cursor()
                if new_pw:
                    h = hashlib.sha256(new_pw.encode()).hexdigest()
                    c.execute("INSERT OR REPLACE INTO users (username, password, role, expiry_date, total_limit, used_count) VALUES (?, ?, ?, ?, ?, COALESCE((SELECT used_count FROM users WHERE username=?), 0))", (target_user, h, new_role, str(new_expiry), int(new_limit), target_user))
                else:
                    c.execute("UPDATE users SET role=?, expiry_date=?, total_limit=? WHERE username=?", (new_role, str(new_expiry), int(new_limit), target_user))
                conn.commit(); st.success("已更新"); st.rerun()
        
        with st.expander("❌ 删除用户"):
            delete_user = st.text_input("输入要删除的用户名")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("删除", use_container_width=True):
                    if delete_user.strip():
                        c = conn.cursor()
                        c.execute("DELETE FROM users WHERE username=?", (delete_user,))
                        conn.commit()
                        st.success(f"✅ 用户 '{delete_user}' 已删除")
                        st.rerun()
                    else:
                        st.error("请输入用户名")
            with col2:
                st.info("删除后无法恢复", icon="⚠️")
        
        conn.close()

