FROM python:3.11-slim

# 不生成 .pyc，并让日志立即输出到 docker logs。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY . /app

# tzdata 提供 Asia/Shanghai 等 IANA 时区；随后清理 apt 缓存以控制镜像大小。
# 不使用 root 运行 Web 服务，减少容器被攻击后的权限范围。
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /app/runtime \
    && chown -R app:app /app

USER app
EXPOSE 8000

# Docker 定期访问只读健康接口；连续失败时容器会显示 unhealthy。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"

CMD ["python", "main.py", "serve", "--host", "0.0.0.0", "--port", "8000"]
