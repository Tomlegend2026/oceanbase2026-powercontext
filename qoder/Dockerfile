FROM python:3.12-slim

WORKDIR /srv/recallcoach/app

# 先拷打包元数据，让依赖层可缓存
COPY app/pyproject.toml app/requirements.txt ./
COPY app/src ./src

# 应用按 <app>/../seed 找种子题库，所以 seed 要放在上一层
COPY seed /srv/recallcoach/seed

RUN pip install --no-cache-dir -e .

EXPOSE 8080

# 没有 PowerContext 也能启动：页面会显示"记忆离线"，其余功能正常
CMD ["uvicorn", "recallcoach.main:app", "--host", "0.0.0.0", "--port", "8080"]
