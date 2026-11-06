"""Verify the pinned adapter_wc wheel and read its deployment contract.

Used by the Docker build (install-native-helper.py, wxbridge_cli/install.py) and
by update-wechat-adapter.py. Load with runpy.run_path; it imports nothing from src.
"""
import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

ADAPTER_CONTRACT = 'adapter_wc/deployment.json'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def deployment_contract(body):
    """Validate adapter_wc/deployment.json, which lists the files installers copy or verify."""
    try:
        contract = json.loads(body)
    except ValueError:
        raise ValueError('invalid_adapter_contract') from None
    def relative(name):
        return (isinstance(name,str) and re.fullmatch(r'[A-Za-z0-9_./-]+',name) is not None
                and not name.startswith('/') and '..' not in Path(name).parts)
    lists = ('helper_assets','image_assets','recovery_modules')
    if (not isinstance(contract,dict) or contract.get('schema_version') != 1
            or any(not isinstance(contract.get(key),list) or not contract[key]
                   or not all(map(relative,contract[key])) for key in lists)
            or any('/' in name for name in contract['helper_assets']+contract['image_assets'])
            or not relative(contract.get('native_profile'))
            or not isinstance(contract.get('image_runtime_directory'),str)
            or not re.fullmatch(r'/run/[A-Za-z0-9._-]+',contract['image_runtime_directory'])):
        raise ValueError('invalid_adapter_contract')
    return contract


def contract_files(contract):
    """Package-relative files the contract promises are in the wheel."""
    return {'deployment.json', contract['native_profile'], *contract['recovery_modules'],
            *('native/'+name for name in contract['helper_assets']+contract['image_assets'])}


def adapter_wheel(source):
    """Verify the independently released adapter before consuming its assets."""
    source = Path(source).resolve()
    manifest = json.loads((source/'dependencies/adapter_wc.json').read_text(encoding='utf-8'))
    version, filename = manifest.get('version'), manifest.get('wheel')
    if (manifest.get('name') != 'adapter_wc' or not isinstance(version,str)
            or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version)
            or filename != f'adapter_wc-{version}-py3-none-any.whl'
            or not re.fullmatch(r'[a-f0-9]{64}',manifest.get('sha256',''))):
        raise ValueError('invalid_adapter_manifest')
    path = source/'dependencies'/filename
    if path.is_symlink() or digest(path) != manifest['sha256']:
        raise ValueError('adapter_wheel_hash_mismatch')
    prefix = f'adapter_wc-{version}.dist-info/'
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if (len(names) != len(set(names)) or len(names)>500 or sum(i.file_size for i in archive.infolist())>16*1024*1024
                or any(n.startswith('/') or '\\' in n or ':' in n or '..' in Path(n).parts
                       or not n.startswith(('adapter_wc/',prefix)) for n in names)):
            raise ValueError('invalid_adapter_inventory')
        metadata = BytesParser().parsebytes(archive.read(prefix+'METADATA'))
        if metadata['Name'] != manifest['name'] or metadata['Version'] != version:
            raise ValueError('adapter_metadata_mismatch')
        rows = list(csv.reader(io.StringIO(archive.read(prefix+'RECORD').decode('utf-8'))))
        if len(rows)!=len(names) or {r[0] for r in rows if len(r)==3}!=set(names):
            raise ValueError('invalid_adapter_record')
        for name, checksum, size in rows:
            if name == prefix+'RECORD':
                if checksum or size:raise ValueError('invalid_adapter_record')
                continue
            body=archive.read(name)
            encoded=base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b'=').decode()
            if checksum!='sha256='+encoded or size!=str(len(body)):
                raise ValueError('adapter_record_mismatch')
        if ADAPTER_CONTRACT not in names:
            raise ValueError('adapter_assets_missing')
        contract = deployment_contract(archive.read(ADAPTER_CONTRACT))
        if not {'adapter_wc/'+name for name in contract_files(contract)}<=set(names):
            raise ValueError('adapter_assets_missing')
    return path


def adapter_members(source):
    with zipfile.ZipFile(adapter_wheel(source)) as archive:
        return {name:archive.read(name) for name in archive.namelist() if name.startswith('adapter_wc/')}


def adapter_deployment(source):
    """The verified deployment contract of the adapter wheel pinned in source."""
    with zipfile.ZipFile(adapter_wheel(source)) as archive:
        return deployment_contract(archive.read(ADAPTER_CONTRACT))
