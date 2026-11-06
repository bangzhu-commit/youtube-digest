#!/usr/bin/env python3
"""Inspect or explicitly install pinned local speaker-diarization dependencies."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

VERSION = '2.0.1'
SEGMENTATION_REVISION = '9403a6902bb58e3d5ae8c7e77c3422de279db2e0'
EMBEDDING_REVISION = '0743f301363dec56491a490f6d6cbc9d67f9a3bf'
ARTIFACTS = (
    dict(name='sherpa_onnx-2.0.1-cp314-cp314-macosx_11_0_arm64.whl', kind='wheel', size=2087413,
         sha256='26ba1d3e9ddd43853fb021999fe83648358fc6d61cd48f8150beb8c3bbc0ed10', license='Apache-2.0',
         url='https://files.pythonhosted.org/packages/29/e8/fbaffc8f1965e68812137c9ddade41ee9951c1547d21b3084b42d46da822/sherpa_onnx-2.0.1-cp314-cp314-macosx_11_0_arm64.whl'),
    dict(name='sherpa_onnx_core-2.0.1-py3-none-macosx_11_0_arm64.whl', kind='wheel', size=9518462,
         sha256='82ab505a835e616f568ba6804555969e1b5699341d2b18eb27f0083b068b61ff', license='Apache-2.0',
         url='https://files.pythonhosted.org/packages/ec/81/5d7f89b4c3dc64e9100590609d5c168448fc379d89a08c6498aad46b6feb/sherpa_onnx_core-2.0.1-py3-none-macosx_11_0_arm64.whl'),
    dict(name='segmentation-model.int8.onnx', kind='model', size=1540506,
         sha256='d582f4b4c6b48205de7e0643c57df0df5615a3c176189be3fc461e9d18827b5d', license='MIT (Copyright 2022 CNRS)',
         url='https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/' + SEGMENTATION_REVISION + '/model.int8.onnx'),
    dict(name='3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx', kind='model', size=39593761,
         sha256='1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b', license='Apache-2.0 (3D-Speaker)',
         url='https://huggingface.co/csukuangfj/speaker-embedding-models/resolve/' + EMBEDDING_REVISION + '/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx'),
)
LICENSES = (
    dict(name='LICENSE-segmentation-MIT.txt', url='https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/' + SEGMENTATION_REVISION + '/LICENSE', marker='MIT License'),
    dict(name='LICENSE-3D-Speaker-Apache-2.0.txt', url='https://raw.githubusercontent.com/modelscope/3D-Speaker/main/LICENSE', marker='Apache License'),
)
REDIRECT_HOSTS = {'files.pythonhosted.org', 'huggingface.co', 'cdn-lfs.huggingface.co',
                  'cdn-lfs.hf.co', 'cas-bridge.xethub.hf.co', 'raw.githubusercontent.com',
                  'us.aws.cdn.hf.co'}  # HF's documented download CDN; exact host, SHA-256 still required.


class SetupError(ValueError):
    pass


def default_state():
    return Path.home() / 'Library/Application Support/YouTube Digest'


def installation_root(state):
    return Path(state) / 'speaker-diarization-2.0.1-py314'


def config_fields(root):
    return dict(diarizationPython=str(root / 'venv/bin/python'),
                segmentationModel=str(root / 'models' / ARTIFACTS[2]['name']),
                speakerEmbeddingModel=str(root / 'models' / ARTIFACTS[3]['name']))


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def plan(state):
    root = installation_root(state)
    return dict(mode='dry-run', installRoot=str(root), downloadBytes=sum(item['size'] for item in ARTIFACTS),
                additionalLicenseBytesAtMost=65536, artifacts=list(ARTIFACTS), licenses=list(LICENSES),
                pythonRequirement='CPython 3.14, macOS ARM64',
                reusedDependencies=['existing numpy through --system-site-packages', 'existing FFmpeg'],
                newPackages=['sherpa-onnx==2.0.1', 'sherpa-onnx-core==2.0.1'],
                configUpdates=config_fields(root), changesGlobalPython=False, uploadsAudio=False,
                downloadsOnRecognition=False, approvalRequired='Run --install only after approval for this download and isolated installation.')


def check(state):
    root = installation_root(state)
    missing = []
    for item in ARTIFACTS:
        path = root / ('wheels' if item['kind'] == 'wheel' else 'models') / item['name']
        if not path.is_file() or path.is_symlink() or path.stat().st_size != item['size'] or sha256(path) != item['sha256']:
            missing.append(item['name'])
    installed = root / 'installation.json'
    if not installed.is_file() or not (root / 'venv/bin/python').is_file():
        missing.append('isolated Python environment')
    return dict(mode='check', installed=not missing, missingOrInvalid=missing, installRoot=str(root), configUpdates=config_fields(root))


def safe_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in REDIRECT_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise SetupError('下载重定向离开了已审核的 HTTPS 来源。')
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download(item, destination):
    safe_url(item['url'])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirect())
    limit = item.get('size', 32768)
    temporary = destination.with_suffix(destination.suffix + '.part')
    digest, count, created = hashlib.sha256(), 0, False
    try:
        with temporary.open('xb') as output:
            created = True
            response = opener.open(urllib.request.Request(item['url'], headers={'User-Agent': 'YouTube-Digest/1.6'}), timeout=40)
            with response:
                while True:
                    block = response.read(256 * 1024)
                    if not block:
                        break
                    count += len(block)
                    if count > limit:
                        raise SetupError('下载大小超过已审核清单，未采用文件。')
                    output.write(block)
                    digest.update(block)
            output.flush()
            os.fsync(output.fileno())
        if 'sha256' in item and (count != item['size'] or digest.hexdigest() != item['sha256']):
            raise SetupError('下载大小或 SHA-256 与已审核清单不一致，未采用文件。')
        if 'marker' in item and item['marker'] not in temporary.read_text(encoding='utf-8'):
            raise SetupError('许可证文件内容与预期不符。')
        os.replace(temporary, destination)
    finally:
        if created and temporary.exists():
            temporary.unlink()


def write_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.digest-setup-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(state):
    if sys.platform != 'darwin' or platform.machine() not in ('arm64', 'aarch64') or sys.version_info[:2] != (3, 14):
        raise SetupError('此固定清单仅适用于 macOS ARM64 的 Python 3.14；未自动替换安装版本。')
    if importlib.util.find_spec('numpy') is None:
        raise SetupError('当前 Python 缺少要复用的 numpy；未自动安装额外依赖。')
    state = Path(state).expanduser().resolve()
    config = state / 'obsidian.json'
    if not config.is_file():
        raise SetupError('请先安装 Digest 本机连接；未创建新的素材库配置。')
    previous = json.loads(config.read_text(encoding='utf-8'))
    if not isinstance(previous, dict):
        raise SetupError('本机连接配置无效，未修改。')
    root = installation_root(state)
    if check(state)['installed']:
        merge_config(config, root)
        return dict(success=True, installed=True, reused=True, installRoot=str(root))
    if root.exists():
        raise SetupError('目标目录已有不完整或不同版本文件，已保留；请检查后再安装，未覆盖。')
    root.mkdir(mode=0o700)
    for folder in ('wheels', 'models', 'licenses'):
        (root / folder).mkdir(mode=0o700)
    # No shell, source builds, dependency resolution, user pip config or global writes.
    for item in ARTIFACTS:
        download(item, root / ('wheels' if item['kind'] == 'wheel' else 'models') / item['name'])
    for item in LICENSES:
        download(item, root / 'licenses' / item['name'])
    subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages', str(root / 'venv')], check=True)
    python = root / 'venv/bin/python'
    subprocess.run([str(python), '-m', 'pip', '--isolated', '--disable-pip-version-check', 'install',
                    '--no-index', '--no-deps', '--no-cache-dir',
                    *[str(root / 'wheels' / item['name']) for item in ARTIFACTS if item['kind'] == 'wheel']], check=True)
    subprocess.run([str(python), '-c', 'import sherpa_onnx,numpy; assert hasattr(sherpa_onnx,"OfflineSpeakerDiarization")'], check=True)
    write_json(root / 'installation.json', dict(version=VERSION, artifacts=list(ARTIFACTS), licenses=list(LICENSES)))
    merge_config(config, root)
    return dict(success=True, installed=True, reused=False, installRoot=str(root), configUpdated=True)


def merge_config(config, root):
    # Download/install can take time. Preserve settings changed in the meantime.
    current = json.loads(config.read_text(encoding='utf-8'))
    if not isinstance(current, dict):
        raise SetupError('本机连接配置在安装期间变得无效，已保留；未覆盖。')
    write_json(config, {**current, **config_fields(root)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='read-only installed artifact check (default)')
    mode.add_argument('--dry-run', action='store_true', help='print exact download/install plan without writing files')
    mode.add_argument('--install', action='store_true', help='explicitly download and install the reviewed manifest')
    parser.add_argument('--state-dir', type=Path, default=default_state())
    args = parser.parse_args()
    try:
        result = install(args.state_dir) if args.install else plan(args.state_dir) if args.dry_run else check(args.state_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(success=False, error=str(error)), ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
