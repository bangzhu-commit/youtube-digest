import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('codex_bridge', Path(__file__).parents[1] / 'native/codex_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class CodexBridgeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='digest-codex-test-')
        self.addCleanup(temporary.cleanup)
        self.temp_root = Path(temporary.name)
        # Preserve real flock behavior, but never contend with a user's live job.
        patcher = patch.object(bridge.tempfile, 'gettempdir', return_value=temporary.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_completion_lock_contends_and_releases_inside_test_directory(self):
        with patch.object(bridge, 'LOCK_WAIT_SECONDS', 0.01):
            with bridge.completion_lock():
                self.assertTrue((self.temp_root / f'youtube-digest-codex-{os.getuid()}.lock').is_file())
                with self.assertRaises(bridge.CodexError) as error:
                    with bridge.completion_lock():
                        self.fail('a second completion must not acquire the held lock')
                self.assertEqual(error.exception.code, 'CODEX_BUSY')
            with bridge.completion_lock():
                pass

    def test_api_credentials_and_provider_environment_are_not_inherited(self):
        with patch.dict(os.environ, {'CODEX_API_KEY': 'fake-key', 'OPENAI_API_KEY': 'fake-key',
                                     'CODEX_ACCESS_TOKEN': 'fake-token', 'OPENAI_BASE_URL': 'https://invalid.example',
                                     'CODEX_HOME': '/different/home', 'NODE_OPTIONS': '--inspect'}):
            env = bridge.clean_environment()
        for key in ('CODEX_API_KEY', 'OPENAI_API_KEY', 'CODEX_ACCESS_TOKEN', 'OPENAI_BASE_URL', 'CODEX_HOME', 'NODE_OPTIONS'):
            self.assertNotIn(key, env)

    def test_api_key_login_is_rejected_without_echoing_status(self):
        fake = subprocess.CompletedProcess([], 0, '', 'Logged in using an API key - fake-credential')
        with patch.object(bridge, 'codex_binary', return_value='/bin/codex'), patch.object(bridge, 'short_command', return_value=fake):
            result = bridge.handle({}, {'action': 'codexStatus'})
        self.assertEqual(result['code'], 'CODEX_CHATGPT_LOGIN_REQUIRED')
        self.assertNotIn('fake-credential', json.dumps(result))

    def test_chatgpt_status_has_only_public_fields(self):
        replies = [subprocess.CompletedProcess([], 0, '', 'Logged in using ChatGPT\n'),
                   subprocess.CompletedProcess([], 0, 'codex-cli 0.166.0\n', '')]
        with patch.object(bridge, 'codex_binary', return_value='/bin/codex'), patch.object(bridge, 'short_command', side_effect=replies):
            result = bridge.handle({}, {'action': 'codexStatus'})
        self.assertEqual(result, {'success': True, 'available': True, 'authenticated': True,
                                  'authMode': 'chatgpt', 'version': '0.166.0'})

    def test_invalid_audio_large_text_and_argument_injection_fail_before_auth(self):
        for message in (
            {'messages': [{'role': 'user', 'content': [{'type': 'input_audio'}]}]},
            {'messages': [{'role': 'user', 'content': 'x' * (bridge.MAX_INPUT_BYTES + 1)}]},
            {'messages': [{'role': 'user', 'content': 'x'}], 'model': '--dangerously-bypass-approvals-and-sandbox'},
        ):
            with patch.object(bridge, 'subscription_status') as status:
                result = bridge.handle({}, {'action': 'codexComplete', **message})
            self.assertFalse(result['success'])
            status.assert_not_called()

    def test_command_keeps_prompt_off_argv_and_disables_external_capabilities(self):
        command = bridge.build_command('/bin/codex', 'gpt-6.1-sol', Path('/tmp/job'), Path('/tmp/schema'))
        self.assertIn('--ignore-user-config', command)
        self.assertIn('--ephemeral', command)
        self.assertIn('read-only', command)
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertIn('features.shell_tool=false', command)
        self.assertIn('features.hooks=false', command)
        self.assertIn('features.apps=false', command)
        self.assertIn('features.plugins=false', command)
        self.assertIn('web_search="disabled"', command)
        self.assertIn('project_doc_max_bytes=0', command)
        self.assertEqual(command[-1], '-')

    def run_fixture(self, body, require_json=True, timeout=180):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / 'fake-codex'
            executable.write_text('#!/usr/bin/env python3\nimport json,sys,time\nfrom pathlib import Path\n' + body)
            executable.chmod(0o700)
            with (patch.object(bridge, 'subscription_status', return_value=(str(executable), bridge.clean_environment(), '0.166.0')),
                  patch.object(bridge, 'MAX_RUNTIME_SECONDS', timeout)):
                return bridge.handle({}, {'action': 'codexComplete', 'messages': [{'role': 'user', 'content': 'non-private fixture'}],
                                          **({'responseFormat': {'type': 'json_object'}} if require_json else {})})

    def test_valid_json_is_returned_without_logs(self):
        result = self.run_fixture("sys.stdin.read()\nPath(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps({'text':'{\"ok\":true}'}))\nprint(json.dumps({'type':'turn.completed'}))\n")
        self.assertTrue(result['success'])
        self.assertEqual(json.loads(result['text']), {'ok': True})

    def test_malformed_json_and_unexpected_tools_are_not_accepted(self):
        invalid = self.run_fixture("sys.stdin.read()\nPath(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps({'text':'not JSON'}))\n")
        self.assertEqual(invalid['code'], 'CODEX_INVALID_OUTPUT')
        tool = self.run_fixture("sys.stdin.read()\nPath(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps({'text':'{\"ok\":true}'}))\nprint(json.dumps({'item':{'type':'web_search'}}))\n")
        self.assertEqual(tool['code'], 'CODEX_TOOL_BLOCKED')

    def test_timeout_terminates_the_process(self):
        result = self.run_fixture('sys.stdin.read()\ntime.sleep(10)\n', timeout=0.1)
        self.assertEqual(result['code'], 'CODEX_TIMEOUT')


if __name__ == '__main__':
    unittest.main()
