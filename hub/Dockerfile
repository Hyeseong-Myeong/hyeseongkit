# hyeseongkit-hub 이미지 (설계서 §12-1, §14-3) — 표준 amd64, DS220+ 호환 (D3)
FROM python:3.11-slim

WORKDIR /app

# ① 의존성 먼저 — 이 레이어는 pyproject.toml·README.md가 바뀔 때만 다시 만들어진다.
#    src-layout(pyproject.toml [tool.setuptools.packages.find])이라 pip install에는 패키지
#    디렉터리가 있어야 하므로, 소스 대신 빈 stub를 두고 의존성만 해결한 뒤 통째로 버린다.
#    ⛔ stub를 /app 안에 만들지 않는다. 그러면 setuptools가 /app/build/lib/에 빈 __init__.py를
#    남기고, ②에서 build_py가 mtime만 비교해(COPY는 빌드 컨텍스트의 옛 mtime을 보존한다)
#    갱신을 건너뛴다 — 실제 소스 대신 빈 stub가 설치된 채로 이미지가 만들어진다.
#    extras는 따옴표로 감싼다 — 셸이 [hub]를 글롭 문자 클래스로 해석하는 것을 막는다
COPY pyproject.toml README.md /deps/
RUN mkdir -p /deps/src/hyeseongkit \
 && touch /deps/src/hyeseongkit/__init__.py \
 && pip install --no-cache-dir "/deps[hub]" \
 && rm -rf /deps

# ② 프로젝트 본체만 설치 — 의존성은 ①에서 이미 받았다
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

EXPOSE 9100

# uvicorn 워커 1개, 전 구간 async I/O (제약 L1)
CMD ["uvicorn", "hyeseongkit.hub.main:app", "--host", "0.0.0.0", "--port", "9100"]
