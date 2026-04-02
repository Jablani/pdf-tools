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

# 获取真实客户端 IP
def get_client_ip():
    try:
        # 优先从代理头获取（部署在服务器、Nginx、Docker 几乎都要这个）
        if 'X-Forwarded-For' in st.request.headers:
            return st.request.headers['X-Forwarded-For'].split(',')[0].strip()
        # 备用
        return st.request.connection.remote_ip
    except:
        return "未知IP"
    

def update_usage(username, operation_type="未知操作", operation_detail=""):
    """扣除用户使用额度（回调函数，传递给 tools 脚本）并记录操作日志"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET used_count = used_count + 1 WHERE username=?", (username,))
    
    # 记录操作日志 - 强制使用东八区时间
    # 如果容器环境有时区问题，这里通过 timedelta 手动补偿（如果是 UTC 则 +8）
    # 但更优雅的方式是使用环境变量，这里先做代码层补丁：
    from datetime import timezone
    tz_sh = timezone(timedelta(hours=8))
    timestamp = datetime.now(tz_sh).strftime("%Y-%m-%d %H:%M:%S")
    
    ip_address = get_client_ip()
    
    conn.execute("INSERT INTO operation_logs (username, operation_type, operation_detail, timestamp, ip_address) VALUES (?, ?, ?, ?, ?)",
                 (username, operation_type, operation_detail, timestamp, ip_address))
    
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
    if st.sidebar.button("📊 分板处理工具", width='stretch' ):
        st.session_state.menu_choice = "分板工具"
    

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
        # 🚀 业务逻辑完全外包给 tools/ups_v2_6.py
        ups_v2_6.show_ui(u_info, lambda username: update_usage(username, "UPS工具", "处理UPS面单和箱标"))
    elif cur == "VC 工具":
        # 🚀 业务逻辑完全外包给 tools/vc_app_v3_1.py
        from tools import vc_app_v3_1
        vc_app_v3_1.show_ui(u_info, lambda username: update_usage(username, "VC工具", "处理VC板标和箱标"))
    elif cur == "BOL 工具":
        # 🚀 业务逻辑完全外包给 tools/bol_app_v2_0.py
        from tools import bol_app_v2_0
        bol_app_v2_0.show_ui(u_info, lambda username: update_usage(username, "BOL工具", "处理BOL PDF和Freight Pick List"))
    elif cur == "分板工具":
        # 🚀 业务逻辑完全外包给 tools/fenban_v1_0.py
        from tools import fenban_v1_0
        fenban_v1_0.show_ui(u_info, lambda username: update_usage(username, "分板工具", "处理OBC压缩包和SKU映射"))

    elif cur == "管理后台":
        st.title("🛠 用户管理控制台")
        
        # 创建选项卡
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
            
            # 筛选器
            col1, col2, col3 = st.columns(3)
            with col1:
                filter_user = st.selectbox("筛选用户", ["全部"] + list(pd.read_sql("SELECT DISTINCT username FROM operation_logs", sqlite3.connect(DB_PATH))['username']))
            with col2:
                filter_operation = st.selectbox("筛选操作类型", ["全部"] + list(pd.read_sql("SELECT DISTINCT operation_type FROM operation_logs", sqlite3.connect(DB_PATH))['operation_type']))
            with col3:
                days_back = st.selectbox("时间范围", ["全部", "今天", "最近7天", "最近30天"], index=2)
            
            # 构建查询
            query = "SELECT username, operation_type, operation_detail, timestamp, ip_address FROM operation_logs WHERE 1=1"
            params = []
            
            if filter_user != "全部":
                query += " AND username = ?"
                params.append(filter_user)
            
            if filter_operation != "全部":
                query += " AND operation_type = ?"
                params.append(filter_operation)
            
            if days_back != "全部":
                if days_back == "今天":
                    days = 1
                elif days_back == "最近7天":
                    days = 7
                elif days_back == "最近30天":
                    days = 30
                
                from datetime import datetime, timedelta
                start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
                query += " AND timestamp >= ?"
                params.append(start_date + " 00:00:00")
            
            query += " ORDER BY timestamp DESC"
            
            conn = sqlite3.connect(DB_PATH)
            df_logs = pd.read_sql(query, conn, params=params)
            conn.close()
            
            if not df_logs.empty:
                # 格式化时间显示
                df_logs['timestamp'] = pd.to_datetime(df_logs['timestamp']).dt.strftime('%Y-%m-%d %H:%M:%S')
                
                # 重命名列为中文
                df_logs = df_logs.rename(columns={
                    'username': '用户名',
                    'operation_type': '操作类型',
                    'operation_detail': '操作详情',
                    'timestamp': '操作时间',
                    'ip_address': 'IP地址'
                })
                
                st.dataframe(df_logs, width='stretch')
                
                # 统计信息
                st.markdown("---")
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("总操作次数", len(df_logs))
                with col2:
                    unique_users = df_logs['用户名'].nunique()
                    st.metric("活跃用户数", unique_users)
                with col3:
                    today_ops = len(df_logs[df_logs['操作时间'].str.startswith(datetime.now().strftime('%Y-%m-%d'))])
                    st.metric("今日操作", today_ops)
            else:
                st.info("暂无操作记录")

