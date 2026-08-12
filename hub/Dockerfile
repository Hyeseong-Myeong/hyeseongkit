# hyeseongkit-hub 이미지 (설계서 §12-1, §14-3) — 표준 amd64, DS220+ 호환 (D3)
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
# extras는 따옴표로 감싼다 — 셸이 [hub]를 글롭 문자 클래스로 해석하는 것을 막는다
RUN pip install --no-cache-dir ".[hub]"

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

EXPOSE 9100

# uvicorn 워커 1개, 전 구간 async I/O (제약 L1)
CMD ["uvicorn", "hyeseongkit.hub.main:app", "--host", "0.0.0.0", "--port", "9100"]
