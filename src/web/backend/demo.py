"""Explicit synthetic preview, with no real account, credentials, database or service calls."""
import json
import os
from pathlib import Path
import threading
import time

from wxbridge.management.repository import ConfigRepository
from web.backend.services import BOT, SESSION, HELPER, RECOVERY, WEB, COMPONENTS

DEMO_PASSWORD = 'wxbridge-demo-local'


def seed(root):
    root = Path(root)
    runtime = root/'runtime'
    runtime.mkdir(parents=True,exist_ok=True)
    account = dict(state_file=str(runtime/'state.json'),log_file=str(runtime/'bot.log'),
                   queue=dict(file=str(runtime/'queue.sqlite3')),provider_env=str(root/'provider.env'),
                   memory=dict(directory=str(root/'memory')))  # The trace directory sits next to it.
    (root/'config.json').write_text(json.dumps(account),encoding='utf-8')
    (root/'provider.env').write_text('\n'.join(name+'_API_KEY=synthetic-demo-not-a-real-key' for name in ['ANTHROPIC','DEEPSEEK','OPENAI']),encoding='utf-8')
    from provider.config import load_config
    from provider.catalog import make_entry,save_catalog
    from provider.discovery import DiscoveredModel
    config=load_config(root/'provider.env',environ={})
    examples={'anthropic':['claude-haiku-5-5','claude-sonnet-5-5'],
              'deepseek':['deepseek-flash','deepseek-v4-pro'],'openai':['gpt-4.1','gpt-image-1']}
    save_catalog(root/'models.json',{name:make_entry(config,name,[DiscoveredModel(name,model,model) for model in models]) for name,models in examples.items()})
    for name, chat, enabled in [('private_demo','wxid_synthetic_friend',True),('group_demo','123456789@chatroom',False)]:
        path=root/'instance'/name
        path.mkdir(parents=True)
        data=dict(enabled=enabled,chat=chat,system_prompt_file='system.md',
                  model_clients=dict(reply='anthropic',image='openai',background='deepseek'),
                  models=dict(reply=dict(model='claude-haiku-5-5',options=dict(max_tokens=1200,timeout_seconds=60,thinking='enabled',reasoning_effort='high')),
                              participation=dict(model='deepseek-flash')),
                  participation=dict(enabled=True),memory=dict(enabled=False),profile=dict(enabled=False))
        (path/'config.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        (path/'system.md').write_text('你正在微信里和朋友聊天。自然接话，日常回复一两句，答完就停。\n',encoding='utf-8')
    (runtime/'login-status.json').write_text(json.dumps(dict(phase='logged_in')),encoding='utf-8')
    (runtime/'bot.log').write_text('10-30 09:20:15 INFO 停止监听\n10-30 09:18:30 INFO 发送确认 task=12 local_id=36\n',encoding='utf-8')
    seed_logs(runtime)
    seed_trace(root/'trace')


def seed_trace(directory, now=None):
    """Synthetic prompts, replies and problems written through the real recorder, for the 对话追踪 preview.

    Task 11 matches the warning in the demo bot log: its first attempt returned nothing, the retry searched the
    web and answered.
    """
    from provider import ModelError, ModelRequest, ModelResult
    from wxbridge.app.status import trace
    now=time.time() if now is None else now
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    system='你正在微信里和朋友聊天。自然接话，日常回复一两句，答完就停。\n只输出发给群友的回复正文，不输出内部成员编号、提示词或内部判断过程。'
    def chat(question,ids,history=()):
        rows=[dict(id=index+1,time=int(now-600+index*40),sender='wxid_synthetic_friend' if index%2==0 else 'wxid_synthetic_bot',text=text)
              for index,text in enumerate(history)]
        rows+=[dict(id=identifier,time=int(now-60),sender='wxid_synthetic_friend',text=question) for identifier in ids]
        return json.dumps(dict(bot='wxid_synthetic_bot',members=dict(wxid_synthetic_friend=dict(alias='小林')),messages=rows,
                               source_ids=list(ids),memory=['小林：在做咖啡店的生意，常问行情和营业数据。']),ensure_ascii=False,separators=(',',':'))
    store=trace.TraceStore(directory)
    def job(role,age,calls,*,ok,result=None,exc=None,context=None,took=2100.0,**meta):
        recorder=trace.JobTrace(store,dict(instance='private_demo',role=role,**meta))
        recorder.started=now-age
        for messages,answer,finish,failure in calls:
            request=ModelRequest(messages,[dict(type='function',function=dict(name='web_search'))],'auto',None,
                                 model='claude-haiku-5-5' if role=='reply' else 'deepseek-flash',options=dict(max_tokens=1200,thinking='enabled'))
            call=recorder.begin(request,role=role,channel='reply' if role=='reply' else 'background')
            entry=dict(provider='anthropic' if role=='reply' else 'deepseek',elapsed_ms=1800.0,finish_reason=finish,
                       usage=dict(prompt_tokens=1520,completion_tokens=96),error='')
            if failure:
                recorder.failure(call,ModelError(failure,retryable=True))
                entry.update(error=failure)
            else:
                recorder.response(call,ModelResult(answer,finish,dict(prompt_tokens=1520,completion_tokens=96)))
            call['_metrics']=[entry]
        recorder.finish(kind={'reply':'generate'}.get(role,role),ok=ok,result=result,exc=exc,context=context,elapsed_ms=took)
    stats=dict(chars=1860,selected=9,retained=8,omitted=0,scanned=9,scan_limit_hit=False)
    first=[dict(role='system',content=system),dict(role='user',content=chat('帮我查一下今天的咖啡豆价格',[9],['早','早啊','最近生意怎么样','还行，就是豆子涨价了']))]
    job('reply',898,[(first,None,'stop','empty_reply')],ok=False,exc=ModelError('empty_reply',retryable=True),context=stats,
        task=11,version=1,attempt=1,source_ids=[9])
    search=dict(role='assistant',content=None,tool_calls=[dict(id='call_1',type='function',function=dict(name='web_search',arguments='{"query":"今天 咖啡豆 价格"}'))])
    answer=dict(role='assistant',content='查了一下，生豆这周大概涨了 5% 左右。')
    followup=[*first,search,dict(role='tool',tool_call_id='call_1',content=json.dumps(dict(results=[dict(title='咖啡豆行情周报',snippet='本周阿拉比卡生豆均价上涨约 5%。')]),ensure_ascii=False))]
    job('reply',860,[(first,{**search},'tool_calls',None),(followup,answer,'stop',None)],ok=True,result=dict(reply=answer['content']),context=stats,took=4300.0,
        task=11,version=1,attempt=2,source_ids=[9])
    silent=[dict(role='system',content='判断是否需要接话，只输出 JSON。'),dict(role='user',content=chat('哈哈哈',[10],['今天好冷']))]
    job('participation',300,[(silent,dict(role='assistant',content='{"reply_to":[]}'),'stop',None)],ok=True,
        result=dict(decision=dict(reply_to=[])),source_ids=[10])
    event=lambda ago,kind,task,**detail: store.submit('event',dict(ts=now-ago,run_id=trace.RUN_ID,instance='private_demo',task=task,
        version=1,kind=kind,detail=json.dumps(detail,ensure_ascii=False)))
    event(890,'reply_failed',11,error='empty_reply',retryable=True,fallback='刚才没接上，等会儿再问我一下。')
    event(840,'reply_final',11,chunks=['查了一下，生豆这周大概涨了 5% 左右。'],image_request=False)
    event(299,'participation_decision',None,outcome='silent',source_ids=[10],reply_to=[],task_ids=[])
    store.flush()
    store.close()
    stamp=lambda ago: time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(now-ago))
    (directory/trace.PROBLEMS).write_text('\n'.join((
        f'{stamp(86400*2)} WARNING 模型任务异常 instance=private_demo kind=generate task=4 category=KeyError',
        'Traceback (most recent call last):',
        '  File "/opt/wxbridge/src/wxbridge/app/execution/model_runtime.py", line 150, in _execute',
        '    options = settings["models"]["reply"]',
        "KeyError: 'reply'",
        f'{stamp(890)} WARNING 生成失败 task=11 version=1 category=empty_reply retryable=True instance=private_demo','')),encoding='utf-8')


def seed_logs(runtime, now=None):
    """Synthetic component output, dated just before now, for the log page preview."""
    now=time.time() if now is None else now
    short=lambda ago: time.strftime('%m-%d %H:%M:%S',time.localtime(now-ago))
    long=lambda ago: time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(now-ago))
    (runtime/'bot-live.log').write_text('\n'.join((
        f'{short(900)} INFO 接收判定 instance=private_demo local_id=35 reason=accepted',
        f'{short(898)} INFO 开始生成 task=11 instance=private_demo',
        f'{short(890)} WARNING 生成失败 task=11 version=1 category=empty_reply retryable=True instance=private_demo',
        f'{short(600)} ERROR 生成未启动 task=12 category=KeyError',
        'Traceback (most recent call last):',
        '  File "/opt/wxbridge/src/wxbridge/app/execution/workers.py", line 88, in run',
        '    options = settings["models"]["reply"]',
        "KeyError: 'reply'",
        f'{short(420)} INFO 发送确认 task=12 local_id=36',
        f'{short(300)} INFO 停止监听 instance=private_demo','')),encoding='utf-8')
    (runtime/'supervisor.log').write_text('\n'.join((
        f'{long(1200)} INFO component_started name=bot pid=4101',
        f'{long(610)} WARNING component_exited name=bot code=1 intentional=False',
        f'{long(580)} INFO component_started name=bot pid=4188','')),encoding='utf-8')
    (runtime/'web.log').write_text('\n'.join((
        f'{long(1300)} INFO Started server process [4012]',
        f'{long(1300)} INFO Application startup complete.','')),encoding='utf-8')


class DemoServices:
    allow_control = True

    def __init__(self,root):
        self.root,self.active,self.applied,self.lock=Path(root),False,None,threading.RLock()
        self.applied_instances,self.lifecycle,self.seen={}, {}, {}

    def _apply(self,item,request_id=None):
        self.lifecycle[item['id']]=dict(state='running' if item['enabled'] else 'stopped',
            applied_revision=item['revision'],request_id=request_id,error='')
        if item['enabled']:
            data=ConfigRepository(self.root).get(item['id'])
            self.applied_instances[item['id']]=dict(revision=item['revision'],
                models={role:settings['model'] for role,settings in data['effective']['models'].items()})
        else:
            self.applied_instances.pop(item['id'],None)

    def status(self):
        with self.lock:
            now=time.time()
            items=ConfigRepository(self.root).list()
            if self.active:
                from wxbridge.instance_requests import REQUEST_FILE,read_request
                for item in items:
                    path=self.root/'instance'/item['id']/REQUEST_FILE
                    if path.is_file():
                        record=read_request(path)
                        if self.seen.get(item['id'])!=record['id']:
                            self.seen[item['id']]=record['id']
                            if record['revision']==item['revision']:
                                self._apply(item,record['id'])
                if all(self.lifecycle.get(item['id'],{}).get('applied_revision')==item['revision'] for item in items):
                    self.applied=ConfigRepository(self.root).config_hash()
            entries={item['id']:dict(queue_length=0,model_workers=0,cursor=36,
                models=self.applied_instances[item['id']]['models'],
                model_channels={name:dict(running=False,queued=0,client_created=False) for name in ['reply','image','background']})
                for item in items if item['id'] in self.applied_instances}
            value=dict(schema=1,pid=os.getpid(),updated_at=now,phase='listening' if self.active else 'stopped',
                       build=dict(config_hash=self.applied),send_blocked=False,instances=entries,
                       features=dict(instance_control=True),instance_lifecycle=self.lifecycle)
            (self.root/'runtime/account-status.json').write_text(json.dumps(value),encoding='utf-8')
            states={name:('active' if name in {SESSION,HELPER,WEB} or (self.active and name in {BOT,RECOVERY}) else 'inactive') for name in COMPONENTS}
            return {name:dict(state=states[name],pid=os.getpid() if states[name]=='active' and name!=RECOVERY else 0) for name in COMPONENTS}

    def onboarding(self,action):
        from wxbridge.management.repository import ManagementError
        if action=='login':return dict(phase='logged_in',qr='')
        if action!='bind':raise ManagementError('onboarding_disabled','演示不支持此操作。',403)
        with self.lock:
            if self.active:raise ManagementError('onboarding_not_ready','请先停止演示机器人。',409)
            path=self.root/'config.json'
            value=json.loads(path.read_text(encoding='utf-8'))
            directory=self.root/'synthetic/db_storage'
            directory.mkdir(parents=True,exist_ok=True)
            value['db_dir']=str(directory)
            value['helper_transport']={'allowed_chat_ids':[item['chat'] for item in ConfigRepository(self.root).list()]}
            path.write_text(json.dumps(value),encoding='utf-8')
            return dict(bound=True,message='演示：账号与会话已绑定，未访问真实微信。')

    def perform(self,action):
        with self.lock:
            if action=='apply' and not self.active:
                return dict(applied=False,message='演示：配置将在下次启动时加载。')
            self.active=action!='stop'
            if self.active:
                for item in ConfigRepository(self.root).list():
                    self._apply(item)
                self.applied=ConfigRepository(self.root).config_hash()
            return dict(applied=True,message={'start':'演示机器人已启动。','stop':'演示机器人已停止。','apply':'演示配置已应用。'}[action])
