import streamlit as st
import sqlite3
import pandas as pd
import hashlib
import uuid
import os
import requests
import time
from datetime import datetime, timedelta

# --- 导入业务插件目录中的 UI 渲染函数 ---
from tools import ups_v2_6

# 数据库存储路径逻辑升级：
# 优先从环境变量 DB_PATH 获取（适用于 Docker 挂载），若无则默认 users.db
DB_PATH = os.getenv("DB_PATH", "users.db")

# 自动确保数据库父目录存在（如果是挂载路径）
db_dir = os.path.dirname(DB_PATH)
if db_dir and not os.path.exists(db_dir):
    try:
        os.makedirs(db_dir, exist_ok=True)
    except:
        pass

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
    
    # 创建操作日志表
    c.execute('''CREATE TABLE IF NOT EXISTS operation_logs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT,
                  operation_type TEXT,
                  operation_detail TEXT,
                  timestamp TEXT,
                  ip_address TEXT)''')
    
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


def get_ip_info(ip):
    """查询 IP 归属地"""
    if not ip or ip in ["127.0.0.1", "localhost", "未知", "::1"]:
        return "本地访问"
    try:
        # 使用 ip-api.com 的免费 API
        response = requests.get(f"http://ip-api.com/json/{ip}?fields=status,message,country,regionName,city", timeout=3)
        data = response.json()
        if data.get("status") == "success":
            return f"{data.get('country')} {data.get('regionName')} {data.get('city')}"
        return "本地局域网" if ip.startswith(("192.168.", "10.", "172.")) else "未知区域"
    except:
        return "查询失败"


def get_client_ip():
    """获取客户端真实 IP (兼容多种环境的终极方案)"""
    try:
        # 1. 尝试从 HTTP Headers 获取 (Streamlit 1.30+)
        if hasattr(st, "context"):
            headers = st.context.headers
            if "x-forwarded-for" in headers:
                return headers["x-forwarded-for"].split(",")[0].strip()
            return st.context.remote_ip
    except:
        pass
    
    return "127.0.0.1"


def update_usage(username, operation_type="未知操作", operation_detail=""):
    """扣除用户使用额度并记录操作日志"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET used_count = used_count + 1 WHERE username=?", (username,))
    
    # 获取 IP 及其归属地
    raw_ip = get_client_ip()
    geo_info = get_ip_info(raw_ip)
    full_ip_info = f"{raw_ip} ({geo_info})"
    
    # 记录操作日志
    from datetime import timezone
    tz_sh = timezone(timedelta(hours=8))
    timestamp = datetime.now(tz_sh).strftime("%Y-%m-%d %H:%M:%S")
    
    conn.execute("INSERT INTO operation_logs (username, operation_type, operation_detail, timestamp, ip_address) VALUES (?, ?, ?, ?, ?)",
                 (username, operation_type, operation_detail, timestamp, full_ip_info))
    
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
st.set_page_config(page_title="自动化平台 V1.2", layout="wide", page_icon="🔧")
init_db()

# Session 状态管理
if 'menu_choice' not in st.session_state:
    st.session_state.menu_choice = "UPS 工具"
if 'auth' not in st.session_state:
    st.session_state.auth = False

# 0. 从 URL token 尝试恢复登录
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
    if st.button("登录", width='stretch'):
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
    if st.sidebar.button("📦 UPS 处理工具", width='stretch'):
        st.session_state.menu_choice = "UPS 工具"

    if st.sidebar.button("🏷️ VC 处理工具", width='stretch'):
        st.session_state.menu_choice = "VC 工具"
    if st.sidebar.button("📂 BOL 处理工具", width='stretch' ):
        st.session_state.menu_choice = "BOL 工具"
    if st.sidebar.button("🔑 修改密码", width='stretch'):
        st.session_state.menu_choice = "修改密码"

    if u_info['role'] == 'admin':
        st.sidebar.markdown("---")
        st.sidebar.subheader("🛠 系统管理")
        if st.sidebar.button("测试-1次", width='stretch'):
            update_usage(st.session_state.user, "测试工具", "测试操作")
        if st.sidebar.button("⚙️ 用户管理后台", width='stretch'):
            st.session_state.menu_choice = "管理后台"

    st.sidebar.markdown("---")
    if st.sidebar.button("🚪 登出", width='stretch'):
        clear_user_auth_token(st.session_state.user)
        st.session_state.auth = False
        st.session_state.user = ''
        st.query_params.clear()
        st.rerun()

    # --- 右侧主界面渲染逻辑 ---
    cur = st.session_state.menu_choice

    if cur == "UPS 工具":
        ups_v2_6.show_ui(u_info, lambda username: update_usage(username, "UPS工具", "处理UPS面单和箱标"))
    elif cur == "VC 工具":
        from tools import vc_app_v3_1
        vc_app_v3_1.show_ui(u_info, lambda username: update_usage(username, "VC工具", "处理VC板标和箱标"))
    elif cur == "BOL 工具":
        from tools import bol_app_v2_0
        bol_app_v2_0.show_ui(u_info, lambda username: update_usage(username, "BOL工具", "处理BOL PDF和Freight Pick List"))
    # ====================== 修改密码 ======================
    elif cur == "修改密码":
        st.subheader("修改密码")

        old_pwd = st.text_input("原密码", type="password")
        new_pwd = st.text_input("新密码", type="password")
        confirm_pwd = st.text_input("确认新密码", type="password")

        if st.button("确认修改密码"):
            if not old_pwd or not new_pwd or not confirm_pwd:
                st.warning("请填写完整信息")
            elif new_pwd != confirm_pwd:
                st.error("两次输入的新密码不一致")
            else:
                def sha256(s):
                    return hashlib.sha256(s.encode()).hexdigest()

                user_data = get_user_data(st.session_state.user)
                if user_data is None:
                    st.error("无法获取当前用户信息")
                else:
                    real_pwd_hash = user_data.get("password", "")
                    if sha256(old_pwd) != real_pwd_hash:
                        st.error("原密码错误")
                    else:
                        new_pwd_hash = sha256(new_pwd)
                        conn = sqlite3.connect(DB_PATH)
                        c = conn.cursor()
                        c.execute('UPDATE users SET password = ? WHERE username = ?',
                                  (new_pwd_hash, st.session_state.user))
                        conn.commit()
                        conn.close()
                        st.success("密码修改成功！")
                        countdown_placeholder = st.empty()
                        for remaining in range(3, 0, -1):
                            countdown_placeholder.info(f"{remaining} 秒后自动登出...")
                            time.sleep(1)
                        clear_user_auth_token(st.session_state.user)
                        st.session_state.auth = False
                        st.session_state.user = ''
                        st.query_params.clear()
                        st.rerun()


    elif cur == "管理后台":
        st.title("🛠 用户管理控制台")
        tab1, tab2 = st.tabs(["👥 用户管理", "📊 操作记录"])
        
        with tab1:
            conn = sqlite3.connect(DB_PATH)
            df_users = pd.read_sql("SELECT username, role, expiry_date, total_limit, used_count FROM users", conn)
            st.dataframe(df_users, width='stretch')
            
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
                    if st.button("删除",width='stretch'):
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
        
        with tab2:
            st.subheader("📊 操作记录查看")
            filter_user = st.selectbox("筛选用户", ["全部"] + list(pd.read_sql("SELECT DISTINCT username FROM operation_logs", sqlite3.connect(DB_PATH))['username']))
            query = "SELECT username, operation_type, operation_detail, timestamp, ip_address FROM operation_logs WHERE 1=1"
            params = []
            if filter_user != "全部":
                query += " AND username = ?"
                params.append(filter_user)
            query += " ORDER BY timestamp DESC"
            conn = sqlite3.connect(DB_PATH)
            df_logs = pd.read_sql(query, conn, params=params)
            conn.close()
            if not df_logs.empty:
                df_logs['timestamp'] = pd.to_datetime(df_logs['timestamp']).dt.strftime('%Y-%m-%d %H:%M:%S')
                df_logs = df_logs.rename(columns={'username': '用户名', 'operation_type': '操作类型', 'operation_detail': '操作详情', 'timestamp': '操作时间', 'ip_address': 'IP及归属地'})
                st.dataframe(df_logs, width='stretch')
            else:
                st.info("暂无操作记录")
