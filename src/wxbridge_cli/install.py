"""Build-time media checks and the read-only doctor used by the container image."""
import getpass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import runpy
import shutil
import subprocess
import sys
import urllib.request

from wxbridge.host.system import service_account

FRIDA_URL = ('https://files.pythonhosted.org/packages/52/0d/68f5453d9baa0ae5e0038e496bded5ec1c35fd5972bb59a9f98b1c12ff59/'
             'frida-17.24.3-cp37-abi3-manylinux_2_5_x86_64.whl')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def verify_media(source, deb=None, wheel=None):
    upgrade = runpy.run_path(str(source/'scripts/adapter-wheel.py'))
    adapter = upgrade['adapter_members'](source)
    profile = json.loads(adapter['adapter_wc/'+upgrade['adapter_deployment'](source)['native_profile']])
    manifest = json.loads((source/'resources/wechat/linux-x86_64/4.1.13.26/manifest.json').read_text())
    executable = Path('/opt/wechat/wechat')
    if executable.exists():
        if sha(executable) != profile['sha256']:
            raise ValueError('已有微信程序与当前原生接口不匹配；安装器不会覆盖。')
    elif deb is None:
        raise ValueError('请通过 --wechat-deb 提供匹配项目版本的微信安装包。')
    if deb and sha(deb) != manifest['artifacts']['installer']['sha256']:
        raise ValueError('微信安装包 SHA-256 与项目记录不匹配。')
    native = runpy.run_path(str(source/'scripts/install-native-helper.py'))
    if wheel and sha(wheel) != native['WHEEL_SHA256']:
        raise ValueError('Frida wheel 校验失败。')
    return profile, native['WHEEL_SHA256']


def validate_password(value):
    if not isinstance(value,str) or not 12 <= len(value) <= 1024 or '\n' in value or '\r' in value:
        raise ValueError('管理台密码需要12至1024个字符，且不能包含换行。')
    return value


def password_input(path=None):
    if path:
        import stat
        flags=os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_NONBLOCK',0)
        with os.fdopen(os.open(path,flags),'rb') as stream:
            info=os.fstat(stream.fileno())
            if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 4096:
                raise ValueError('密码输入文件必须是私有普通文件（0600）。')
            return validate_password(stream.read(4097).decode('utf-8-sig').strip())
    if not sys.stdin.isatty():
        raise ValueError('非交互时请使用 --password-file；不要把密码放入命令参数。')
    first = getpass.getpass('设置管理台密码：')
    if first != getpass.getpass('再次输入：'):
        raise ValueError('两次密码不同。')
    return validate_password(first)


def fetch_frida(destination, expected, *, proxy=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(
        {'http':proxy,'https':proxy} if proxy else {}))
    # Create exclusively; an interrupted download never becomes installation input.
    with destination.open('xb') as stream, opener.open(FRIDA_URL, timeout=30) as response:
        total = 0
        while chunk := response.read(1024*1024):
            total += len(chunk)
            if total > 64*1024*1024:
                raise ValueError('Frida wheel 超过大小上限。')
            stream.write(chunk)
    if sha(destination) != expected:
        raise ValueError('下载的 Frida wheel 校验失败。')
    return destination


def dependency_health():
    from packaging.requirements import Requirement
    roots = ['wxbridge','model-provider','adapter_wc','openai','anthropic','google-genai','Pillow','fastapi','uvicorn','pycryptodome','zstandard','zxing-cpp','qrcode']
    seen, pending, failures = set(), [(n,frozenset()) for n in roots], []
    while pending:
        name, extras = pending.pop()
        if (name.lower(),extras) in seen:
            continue
        seen.add((name.lower(),extras))
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            failures.append(name+': missing')
            continue
        for text in importlib.metadata.requires(name) or []:
            r = Requirement(text)
            if r.marker and not any(r.marker.evaluate({'extra':e}) for e in (extras or {''})):
                continue
            try:
                found = importlib.metadata.version(r.name)
            except importlib.metadata.PackageNotFoundError:
                failures.append(r.name+': missing')
                continue
            if r.specifier and not r.specifier.contains(found, prereleases=True):
                failures.append(r.name+': incompatible')
            pending.append((r.name,frozenset(r.extras)))
    return failures


def doctor(paths):
    result = []
    try:
        from wxbridge.config import load_config
        cfg = load_config(paths.app/'config.json')
    except (OSError,ValueError):
        from wxbridge.config import AppConfig
        cfg = AppConfig()
    def add(name, ok, detail=''):
        result.append(dict(name=name,ok=bool(ok),detail=detail))
    add('Linux x86_64',sys.platform == 'linux' and platform.machine().lower() in ('x86_64','amd64'))
    try:
        failures = dependency_health()
        add('运行依赖',not failures, ', '.join(failures))
    except ImportError:
        add('运行依赖',False,'请在项目虚拟环境运行。')
    for name in ('Xvfb','dbus-launch','xdotool','xauth','fluxbox'):
        add(name,shutil.which(name) is not None)
    if cfg.account.ui.backend != 'native_service':
        add('xclip（桌面发送）',shutil.which('xclip') is not None)
    gui = subprocess.run(['/usr/bin/python3','-c',
        "import gi; gi.require_version('Atspi','2.0'); gi.require_version('Gdk','3.0'); from gi.repository import Atspi,Gdk"],
        capture_output=True,timeout=10)
    add('系统 GI / AT-SPI / Gdk',gui.returncode == 0)
    try:
        from importlib.resources import files
        from adapter_wc.deployment import NATIVE_PROFILE
        profile = json.loads(files('adapter_wc').joinpath(NATIVE_PROFILE).read_text(encoding='utf-8'))
        add('微信版本校验',sha('/opt/wechat/wechat') == profile['sha256'])
    except OSError:
        add('微信版本校验',False)
    try:
        from wxbridge.storage.memoryfs import is_noswap_tmpfs
        from wxbridge.host.layout import sensitive_root
        mounts = Path('/proc/self/mountinfo').read_text()
        add('私有内存挂载',is_noswap_tmpfs(paths.runtime.resolve(),mounts,require_noswap=False))
        add('敏感内存挂载',is_noswap_tmpfs(sensitive_root(),mounts))
    except OSError:
        add('私有内存挂载',False)
    try:
        account = service_account()
        info = paths.runtime.stat()
        add('运行目录权限',info.st_uid == account.pw_uid and not info.st_mode & 0o077)
    except (OSError,KeyError):
        add('运行目录权限',False)
    try:
        from wxbridge.config import load_config
        cfg = load_config(paths.app/'config.json')
        add('账号绑定',bool(cfg.account.db_dir), '首次扫码后运行 sudo wxbridge setup。' if not cfg.account.db_dir else '')
        from wxbridge.config import load_config
        load_config(paths.app/'config.json')
        add('实例配置',True)
    except (OSError,ValueError,KeyError):
        add('实例配置',False)
    from provider import load_config as provider_config
    from urllib.parse import urlsplit
    try:
        connections = provider_config(cfg.account.provider_env)
        for name, connection in connections.connections.items():
            if connections.api_key(name):
                for endpoint in (connection.base_url, connection.proxy_url):
                    if endpoint:
                        reachable = urlsplit(endpoint).hostname not in ('localhost','127.0.0.1','::1')
                        add('容器连接 '+name, reachable,
                            '' if reachable else '容器内 loopback 地址不能访问宿主机。')
    except (OSError, ValueError):
        add('Provider 配置',False)
    return result
