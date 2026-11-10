"""Fixed local supervisor operations; HTTP never supplies commands or paths."""
from concurrent.futures import ThreadPoolExecutor
import os
import socket
import threading
import time
import uuid

from wxbridge.management.repository import ManagementError
from wxbridge.control import BOT, SESSION, HELPER, RECOVERY, WEB, COMPONENTS, DEFAULT_SOCKET, request

class ServiceManager:
    def __init__(self, *, allow_control=False, control_socket=DEFAULT_SOCKET):
        self.allow_control = allow_control and os.name == 'posix'
        self.control_socket = control_socket
        self.status_error = ''

    def status(self):
        try:
            if os.name != 'posix':raise OSError()
            values = request('status', self.control_socket)['components']
            if not isinstance(values, dict):
                raise ValueError('invalid_control_components')
            self.status_error = ''
            return {name:values.get(name,dict(state='unavailable',pid=0)) for name in COMPONENTS}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status_error = ('control_permission_denied' if isinstance(exc, PermissionError)
                                 else 'control_timeout' if isinstance(exc, (TimeoutError, socket.timeout))
                                 else 'control_unreachable' if isinstance(exc, OSError)
                                 else 'control_invalid_response')
            return {name:dict(state='unavailable',pid=0) for name in COMPONENTS}

    def perform(self, action):
        if action not in {'start','stop','apply'}:
            raise ManagementError('invalid_service_action', '不支持的服务操作。')
        if not self.allow_control:
            raise ManagementError('service_control_disabled', '当前环境未开放服务控制。', 403)
        try:
            return request(action, self.control_socket)
        except (OSError, ValueError):
            raise ManagementError('supervisor_unavailable', '主控暂时不可用，请在宿主机用 docker compose logs account 查看。', 503) from None

    def onboarding(self, action):
        if not self.allow_control or action not in ('login','bind'):
            raise ManagementError('onboarding_disabled','当前环境未开放账号引导。',403)
        try:
            return request('account_'+action,self.control_socket)
        except (OSError,ValueError):
            raise ManagementError('onboarding_not_ready','微信尚未就绪，或账号服务仍在运行；请稍后重试，绑定前先停止机器人。',409) from None


AUTHORIZE_ACTIONS = {'authorize', 'authorize_start'}


def bot_idle(states):
    """Binding needs both the bot and the recovery helper stopped."""
    return states.get(BOT, {}).get('state') == 'inactive' and not states.get(RECOVERY, {}).get('enabled', False)


class Operations:
    # The bot drains in-flight sends before it stops; Compose allows ten minutes for that.
    drain_timeout = 660
    poll_interval = 1.0
    bind_attempts = 3

    def __init__(self, services, repository):
        self.services, self.repository = services, repository
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='web-service')
        self.lock, self.items, self.running = threading.RLock(), [], False

    def submit(self, action):
        if action not in {'start','stop','apply',*AUTHORIZE_ACTIONS}:
            raise ManagementError('invalid_service_action', '不支持的服务操作。')
        if not self.services.allow_control:
            raise ManagementError('service_control_disabled', '当前环境未开放服务控制。', 403)
        if action == 'authorize':
            # Only a restart needs a startable configuration; a stopped bot stays stopped.
            if not bot_idle(self.services.status()):
                self.repository.validate_start()
        elif action != 'stop':
            self.repository.validate_start()
        with self.lock:
            if self.running:
                raise ManagementError('operation_busy', '已有服务操作正在执行，请等待完成。', 409)
            item = dict(id=uuid.uuid4().hex, action=action, state='running', started_at=time.time(), message='正在执行服务操作…')
            self.items = [item, *self.items[:19]]
            self.running = True
            self.executor.submit(self._run, item)
            return dict(item)

    def _progress(self, item, message):
        with self.lock:
            item['message'] = message

    def _authorize(self, item):
        """Stop if needed, bind the account and all instance chats, then restore or start the bot."""
        was_running = not bot_idle(self.services.status())
        if was_running:
            self._progress(item, '正在停止机器人，等待进行中的发送完成…')
            self.services.perform('stop')
            deadline = time.monotonic() + self.drain_timeout
            while not bot_idle(self.services.status()):
                if time.monotonic() >= deadline:
                    raise ManagementError('authorize_stop_timeout', '机器人未能在限定时间内停止，未修改授权。请稍后重试。', 504)
                time.sleep(self.poll_interval)
        self._progress(item, '正在核对微信账号并授权实例会话…')
        for attempt in range(self.bind_attempts):
            try:
                bound = self.services.onboarding('bind')
                break
            except ManagementError as exc:
                # The sender helper restarts after an authorization change; give it a moment.
                if exc.code != 'onboarding_not_ready' or attempt == self.bind_attempts-1:
                    suffix = '机器人已停止，处理后请在服务管理中启动。' if was_running else ''
                    raise ManagementError(exc.code, exc.message+suffix, exc.status) from None
                time.sleep(self.poll_interval*2)
        start = was_running or item['action'] == 'authorize_start'
        if start:
            self._progress(item, '授权完成，正在启动机器人…')
            self.services.perform('start')
        count = bound.get('authorized_count')
        authorized = f'已授权 {count} 个实例会话' if isinstance(count, int) else '账号与会话授权已保存'
        return dict(message=authorized + ('，机器人正在启动。' if start else '；机器人保持停止，可在服务管理中启动。'),
                    restarted=start)

    def _run(self, item):
        try:
            if item['action'] in AUTHORIZE_ACTIONS:
                result = self._authorize(item)
            else:
                result = self.services.perform(item['action'])
            with self.lock:
                item.update(state='done', **result)
        except ManagementError as exc:
            with self.lock:
                item.update(state='error', code=exc.code, message=exc.message)
        except Exception:
            with self.lock:
                item.update(state='error', code='service_failed', message='服务操作失败，请检查服务器状态。')
        finally:
            with self.lock:
                item['finished_at'], self.running = time.time(), False

    def snapshot(self):
        with self.lock:
            return [dict(item) for item in self.items]

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
