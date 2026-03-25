FROM python:3.11-slim
WORKDIR /app
# 预安装所有业务依赖，这样以后容器重启就是“秒开”
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
    streamlit \
    streamlit-authenticator \
    pyyaml \
    pymupdf \
    pandas \
    openpyxl
