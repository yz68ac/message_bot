"""开机准备与只读登录/账号验证；容器重建后以最新消息为基线。日志仅输出安全状态。"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from wxbridge.host.layout import config_root, sensitive_root, environment
APP = config_root()
CODE = Path('/opt/wxbridge')
SENSITIVE = sensitive_root()
RUN = Path('/run/wxbridge-trial')
SETTINGS = Path('/etc/wxbridge/boot.json')
SCANNER = Path('/usr/local/lib/wxbridge-recovery')
USER = 'wxbridge'


def atomic_json(path, value, *, owner=None):
    fd, temporary = tempfile.mkstemp(prefix='.boot-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        if owner:
            os.chown(temporary, owner.pw_uid, owner.pw_gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def owner():
    import pwd
    return pwd.getpwnam(USER)


def safe_status(phase, **fields):
    result = dict(phase=phase, updated_at=int(time.time()), **fields)
    if RUN.is_dir():
        atomic_json(RUN/'boot-status.json', result, owner=owner() if os.geteuid() == 0 else None)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def validate_runtime():
    account = owner()
    if RUN.is_symlink() or not RUN.is_dir():
        raise ValueError('runtime_missing_or_symlink')
    sys.path.insert(0, str(SCANNER))
    from wxbridge.storage.memoryfs import is_noswap_tmpfs
    if not is_noswap_tmpfs(RUN.resolve(), Path('/proc/self/mountinfo').read_text(), require_noswap=False):
        raise ValueError('runtime_not_private_tmpfs')
    info = RUN.stat()
    if info.st_uid != account.pw_uid or info.st_mode & 0o077:
        raise ValueError('runtime_permissions')


def runtime():
    validate_runtime()
    account = owner()
    for path in (SENSITIVE/'decrypted',):
        if path.is_symlink():
            raise ValueError('recovery_directory_symlink')
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.stat()
        if info.st_uid not in (0, account.pw_uid) or info.st_mode & 0o077:
            raise ValueError('recovery_directory_permissions')
        os.chown(path, account.pw_uid, account.pw_gid)
        os.chmod(path, 0o700)
    safe_status('runtime_ready')


def probe(keys=None):
    # 只在主监听停止、协调器持有两把锁时运行；整个数据库访问留在此单进程。
    sys.path.insert(0, str(CODE/'src' if (CODE/'src').is_dir() else CODE))
    from wxbridge.config import load_config
    from wxbridge.wechat.store import WeChatStore
    cfg = load_config(APP/'config.json')
    if keys:
        from wxbridge.config import prepare_runtime
        cfg = prepare_runtime(cfg, keys_file=keys)
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink):
        store = WeChatStore(cfg.account.db_dir, cfg.account.keys_file, cfg.account.decrypted_dir)
        groups = [(e.id, e, False) for e in cfg.instances.values() if e.enabled]
        if not groups:
            raise ValueError('no_enabled_instances')
        states = []
        for identifier, active_cfg, reuse in groups:
            chat, title = store.resolve_chat(active_cfg.chat)
            identity = dict(account=store.self_wxid, chat=chat)
            from wxbridge.instances import CHAT_ID
            if not identity['account'] or not CHAT_ID.fullmatch(chat):
                raise ValueError('wechat_account_not_ready')
            if identifier is not None and chat != active_cfg.chat:
                raise ValueError('instance_chat_identity_mismatch')
            maximum = store.last_local_id(chat)
            states.append((identifier, active_cfg, reuse, identity, maximum))
        if cfg.account.ui.backend=='native_service':
            from wxbridge.native.sender import readiness
            readiness(cfg)
        else:
            # Desktop backends still require a discoverable target chat.
            from wxbridge.wechat.ui import A11y
            ui = A11y(cfg.account.ui)
            if ui.chat_is_selected(title) is not True and ui.find_chat_item(title) is None:
                raise ValueError('wechat_login_or_target_chat_not_visible')
    results = {}
    for identifier, active_cfg, reuse, identity, maximum in states:
        queue_file = Path(active_cfg.paths.queue_file)
        if queue_file.exists():
            from wxbridge.storage.inspection import inspect_queue
            if inspect_queue(queue_file).identity != identity:
                raise ValueError('existing_queue_identity_mismatch')
        result = dict(ready=True, latest_local_id=maximum)
        if identifier is None:
            return result
        results[identifier] = result
    return dict(ready=True, instances=results)


def as_user(args):
    account = owner()
    def drop():
        os.initgroups(account.pw_name, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    env = dict(**environment(), PATH='/usr/local/bin:/usr/bin:/bin', HOME=account.pw_dir, PYTHONPATH=str(CODE/'src' if (CODE/'src').is_dir() else CODE))
    allowed = {'DISPLAY','XAUTHORITY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','QT_ACCESSIBILITY',
               'QT_LINUX_ACCESSIBILITY_ALWAYS_ON','GTK_MODULES','NO_AT_BRIDGE'}
    for line in (RUN/'env').read_text().splitlines():
        key, sep, value = line.partition('=')
        if sep and key in allowed:
            env[key] = value.strip().strip('\'"')
    return subprocess.run(args, cwd=APP, env=env, preexec_fn=drop, capture_output=True, text=True, timeout=150)


def process_token(settings):
    account = owner()
    pid = int((RUN/'wechat.pid').read_text())
    proc = Path('/proc')/str(pid)
    if proc.stat().st_uid != account.pw_uid or (proc/'exe').resolve() != Path('/opt/wechat/wechat').resolve():
        raise ValueError('wechat_process_not_ready')
    start = (proc/'stat').read_text().rsplit(')',1)[1].split()[19]
    expected = Path(settings['db_dir']).resolve()
    if not expected.is_relative_to(Path(account.pw_dir)) or expected.name != 'db_storage':
        raise ValueError('unexpected_database_path')
    opened = False
    for fd in (proc/'fd').iterdir():
        try:
            target = fd.resolve(strict=True)
            opened |= target.is_relative_to(expected) and target.suffix == '.db'
        except OSError:
            pass
    if not opened:
        raise ValueError('waiting_for_wechat_login')
    return dict(pid=pid, start=start, boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())


@contextlib.contextmanager
def database_locks():
    import fcntl
    account = owner()
    with contextlib.ExitStack() as stack:
        for path in sorted({RUN/'queue.sqlite3.lock', SENSITIVE/'decrypted/.listener.lock'}):
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            stack.callback(os.close,fd)
            os.fchown(fd, account.pw_uid, account.pw_gid)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def prepare(*, verify=False, metadata_only=False):
    validate_runtime()
    settings = json.loads(SETTINGS.read_text())
    token = process_token(settings)
    marker = RUN/'keyscan-session.json'
    cached = json.loads(marker.read_text()) if marker.exists() else None
    if not metadata_only and (SENSITIVE/'keys.json').exists():
        try:
            if 'message/message_0.db' not in json.loads((SENSITIVE/'keys.json').read_text()):cached=None
        except (ValueError,OSError):cached=None
    # The supervisor waits for listener exit before entering preparation.
    with database_locks():
        if verify or cached != token or not (SENSITIVE/'keys.json').exists():
            scratch = SENSITIVE/'root-keyscan'
            if scratch.is_symlink():
                raise ValueError('keyscan_directory_symlink')
            scratch.mkdir(mode=0o700, exist_ok=True)
            if scratch.stat().st_uid != 0 or scratch.stat().st_mode & 0o077:
                raise ValueError('keyscan_directory_permissions')
            output = scratch/'candidate.json'
            code = 'from adapter_wc.storage import extract_keys; import sys; extract_keys(sys.argv[1],sys.argv[2],pid=int(sys.argv[3]),show_keys=False)'
            proc = subprocess.run(['/usr/bin/python3','-c',code,settings['db_dir'],str(output),str(token['pid'])],
                cwd='/', env=dict(PATH='/usr/bin:/bin', PYTHONPATH=str(SCANNER)),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
            if proc.returncode or not output.exists():
                raise ValueError('key_extraction_waiting_or_failed')
            # 校验所有密钥对应文件，至少覆盖联系人、会话与主消息库。
            keys = json.loads(output.read_text())
            required={'contact/contact.db','session/session.db'}
            if not metadata_only:required.add('message/message_0.db')
            if not required <= set(keys):
                raise ValueError('required_database_keys_missing')
            account = owner()
            os.chown(output,account.pw_uid,account.pw_gid)
            os.chmod(output,0o600)
            destination = SENSITIVE/('verification-keys.json' if verify else 'keys.json')
            os.replace(output,destination)
            if not verify:
                atomic_json(marker,token,owner=owner())
        else:
            destination = SENSITIVE/'keys.json'
        if metadata_only:
            return safe_status('contacts_prepared',ready=False,no_api=True,no_message_sent=True)
        args = [str(CODE/'.venv/bin/python'),str(CODE/'scripts/boot-recovery.py'),'probe','--keys',str(destination)]
        result = as_user(args)
        if result.returncode:
            try:
                reason = json.loads(result.stdout).get('error','probe_failed')
            except (ValueError,TypeError):
                reason = 'probe_failed'
            raise ValueError('probe_'+reason)
        details = json.loads(result.stdout)
        if verify:
            destination.unlink(missing_ok=True)
            return safe_status('verified', key_scan=True, no_api=True, no_message_sent=True, **details)
        atomic_json(marker,token,owner=owner())
    return safe_status('prepared', **details)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode',choices=['runtime','prepare','directory','verify','probe','status'])
    parser.add_argument('--keys',type=Path)
    args = parser.parse_args()
    try:
        if args.mode == 'runtime':
            runtime()
        elif args.mode in ('prepare','directory','verify'):
            prepare(verify=args.mode=='verify',metadata_only=args.mode=='directory')
        elif args.mode == 'probe':
            print(json.dumps(probe(args.keys)))
        else:
            print((RUN/'boot-status.json').read_text() if (RUN/'boot-status.json').exists() else '{"phase":"not_started"}')
    except Exception as exc:
        # 不打印含路径/账号/密钥/聊天正文的异常内容。
        if args.mode in ('runtime','prepare','directory','verify'):
            category = str(exc) if isinstance(exc,ValueError) else type(exc).__name__
            safe_status('waiting_for_login_or_repair', reason=category)
        else:
            import re
            reason=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[a-z_]{1,90}',str(exc)) else type(exc).__name__
            print(json.dumps(dict(ready=False,error=reason)))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
