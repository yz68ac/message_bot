ARG BASE_IMAGE=debian:13-slim@sha256:1e1a22175fbf1b32157ddaf0219e1db4e5401fab2b94ed285add64d18587cd2d

# Layers run from least to most frequently changed, so a source-only change
# rebuilds only the final steps. Needs BuildKit (the default since Docker 23).

# Frontend: rebuilt only when the frontend changes; Node stays out of the runtime image.
FROM ${BASE_IMAGE} AS frontend
ARG HTTP_PROXY
ARG HTTPS_PROXY
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /frontend
COPY src/web/frontend/package.json src/web/frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY src/web/frontend/ ./
RUN npm run build

FROM ${BASE_IMAGE}
ARG HTTP_PROXY
ARG HTTPS_PROXY
ENV DEBIAN_FRONTEND=noninteractive
ENV UV_LINK_MODE=copy
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv python3-pip python3-gi python3-pycryptodome python3-zstandard gir1.2-atspi-2.0 gir1.2-gtk-3.0 at-spi2-core xvfb xauth dbus-x11 fluxbox xdotool libde265-0 ffmpeg ca-certificates curl libnss3 libnspr4 && rm -rf /var/lib/apt/lists/*
ENV PYTHONPATH=/opt/wxbridge/src
WORKDIR /opt/wxbridge

# WeChat client: rebuilt only when the pinned installer changes. The installer is
# bind-mounted, checked against the resource manifest and never stored in the image.
COPY resources/wechat/linux-x86_64/4.1.13.26/manifest.json resources/wechat/linux-x86_64/4.1.13.26/manifest.json
RUN --mount=type=bind,source=resources/wechat/linux-x86_64/4.1.13.26/installer/WeChatLinux_x86_64.deb,target=/tmp/WeChatLinux_x86_64.deb \
    python3 -c "import hashlib,json; expected=json.load(open('resources/wechat/linux-x86_64/4.1.13.26/manifest.json'))['artifacts']['installer']['sha256']; assert hashlib.file_digest(open('/tmp/WeChatLinux_x86_64.deb','rb'),'sha256').hexdigest()==expected, 'wechat_installer_sha256_mismatch'" \
    && apt-get update && apt-get install -y /tmp/WeChatLinux_x86_64.deb && rm -rf /var/lib/apt/lists/*

# Third-party Python packages and the pinned adapter wheel: rebuilt only when the lock or a wheel changes.
COPY pyproject.toml uv.lock ./
COPY src/provider/pyproject.toml src/provider/pyproject.toml
COPY dependencies/ dependencies/
RUN --mount=type=cache,target=/root/.cache/uv python3 -m venv --system-site-packages .venv && .venv/bin/pip install uv==0.12.24 \
    && .venv/bin/uv sync --locked --extra database --extra login --no-dev --no-install-project --no-install-package model-provider --python /usr/bin/python3

# Application source, the built frontend, and the local wxbridge/model-provider packages.
COPY Dockerfile MANIFEST.in README.md config.defaults.json requirements.txt ./
COPY prompts/ prompts/
COPY scripts/ scripts/
COPY src/ src/
COPY --from=frontend /frontend/dist/ src/web/frontend/dist/
RUN python3 -c "from pathlib import Path; [(p.write_bytes(p.read_bytes().replace(bytes([13,10]),bytes([10])))) for p in Path('scripts').rglob('*.sh')]"
RUN --mount=type=cache,target=/root/.cache/uv .venv/bin/uv sync --locked --extra database --extra login --no-dev --python /usr/bin/python3 \
    && .venv/bin/python -c "import runpy; from pathlib import Path; runpy.run_path('src/wxbridge_cli/install.py')['verify_media'](Path('.'))"

# Native helper. The verified Frida wheel is kept in a build cache, so rebuilds do not download it again.
RUN --mount=type=cache,target=/var/cache/wxbridge-frida groupadd -g 10001 wxbridge && useradd -u 10001 -g 10001 -d /var/lib/wxbridge -M wxbridge \
    && .venv/bin/python -c "import os,runpy; from pathlib import Path; d=runpy.run_path('src/wxbridge_cli/install.py'); expected=runpy.run_path('scripts/install-native-helper.py')['WHEEL_SHA256']; cached=Path('/var/cache/wxbridge-frida/frida.whl'); (cached.is_file() and d['sha'](cached)==expected) or (cached.unlink(missing_ok=True) or d['fetch_frida'](cached,expected,proxy=os.environ.get('HTTPS_PROXY')))" \
    && .venv/bin/python scripts/install-native-helper.py --wheel /var/cache/wxbridge-frida/frida.whl --allow filehelper \
    && .venv/bin/python -c "from importlib.resources import files; import shutil; from adapter_wc.deployment import IMAGE_ASSETS; [shutil.copyfile(files('adapter_wc.native').joinpath(n), '/opt/wxbridge-native-helper/'+n) for n in IMAGE_ASSETS]"
RUN mkdir -p /usr/local/lib/wxbridge-recovery && cp -r src/wxbridge /usr/local/lib/wxbridge-recovery/ && .venv/bin/python -c "from pathlib import Path; import shutil,adapter_wc; shutil.copytree(Path(adapter_wc.__file__).parent, '/usr/local/lib/wxbridge-recovery/adapter_wc', ignore=shutil.ignore_patterns('__pycache__','*.pyc'))" && ln -s /opt/wxbridge/.venv/bin/wxbridge /usr/local/bin/wxbridge
RUN .venv/bin/python -c "import hashlib,json; from pathlib import Path; from importlib.resources import files; from adapter_wc.deployment import NATIVE_PROFILE; expected=json.loads(files('adapter_wc').joinpath(NATIVE_PROFILE).read_text())['sha256']; assert hashlib.file_digest(open('/opt/wechat/wechat','rb'),'sha256').hexdigest()==expected"
ENV WXBRIDGE_RUNTIME=container
ENTRYPOINT ["/opt/wxbridge/.venv/bin/python", "-m", "wxbridge.host.container"]
