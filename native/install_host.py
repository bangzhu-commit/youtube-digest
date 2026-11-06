#!/usr/bin/env python3
"""Install only the scoped host for one Chrome extension and one user-selected vault."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--extension-id', required=True)
    parser.add_argument('--vault', required=True)
    parser.add_argument('--config-dir', type=Path)
    parser.add_argument('--host-dir', type=Path)
    parser.add_argument('--allow-fake-ip-dns', action=argparse.BooleanOptionalAction, default=None,
                        help='Allow 198.18.0.0/15 proxy Fake-IP for the exact supported HTTPS hosts; default preserves the previous setting.')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-p]{32}', args.extension_id):
        parser.error('extension-id must be the 32-character ID shown in Chrome extensions')
    vault = Path(args.vault).expanduser().resolve()
    if not vault.is_dir():
        parser.error('vault directory does not exist')
    if sys.platform != 'darwin' and (args.config_dir is None or args.host_dir is None):
        parser.error('provide config-dir and host-dir on platforms other than macOS')
    state = args.config_dir or Path.home() / 'Library/Application Support/YouTube Digest'
    hosts = args.host_dir or Path.home() / 'Library/Application Support/Google/Chrome/NativeMessagingHosts'
    state.mkdir(parents=True, exist_ok=True)
    hosts.mkdir(parents=True, exist_ok=True)
    state.chmod(0o700)
    config = state / 'obsidian.json'
    previous = {}
    if config.is_file():
        try:
            previous = json.loads(config.read_text(encoding='utf-8'))
            if not isinstance(previous, dict):
                raise ValueError('configuration is not an object')
        except (ValueError, UnicodeError) as error:
            parser.error('existing bridge configuration is invalid; preserve and inspect it before reinstalling')
    allow_fake_ip = previous.get('allowFakeIpDns') is True if args.allow_fake_ip_dns is None else args.allow_fake_ip_dns
    config.write_text(json.dumps({**previous, 'vault': str(vault),
                                 'ffmpeg': shutil.which('ffmpeg') or previous.get('ffmpeg'),
                                 'codex': shutil.which('codex') or previous.get('codex'),
                                 'python': sys.executable, 'allowFakeIpDns': allow_fake_ip},
                                ensure_ascii=False), encoding='utf-8')
    config.chmod(0o600)
    launcher = state / 'podcast-host'
    script = Path(__file__).with_name('podcast_host.py').resolve()
    launcher.write_text('#!/bin/sh\nexport YTD_OBSIDIAN_CONFIG=' + shlex.quote(str(config)) + '\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(script)) + '\n', encoding='utf-8')
    launcher.chmod(0o700)
    manifest = hosts / 'com.youtube_digest.obsidian.json'
    manifest.write_text(json.dumps({
        'name': 'com.youtube_digest.obsidian', 'description': 'Scoped podcast sources and Obsidian annotations',
        'path': str(launcher), 'type': 'stdio',
        'allowed_origins': ['chrome-extension://' + args.extension_id + '/'],
    }, indent=2), encoding='utf-8')
    manifest.chmod(0o600)
    print('Registered scoped Obsidian bridge for extension ' + args.extension_id)

if __name__ == '__main__':
    main()
