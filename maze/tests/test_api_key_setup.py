import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dotenv import dotenv_values
import web_app


class ApiKeySetupTests(unittest.TestCase):
    def setUp(self):
        self.client=web_app.app.test_client()

    def test_missing_key_prompts_before_any_api_request(self):
        with patch.dict(os.environ,{},clear=True), patch.object(web_app,'GPTAgent') as agent:
            page=self.client.get('/').get_data(as_text=True)
            self.assertIn('let hasSavedKey = false;',page)
            self.assertIn('API key needed for the AI agent',page)
            for endpoint in ('/api/agent/run','/api/agent/models'):
                response=self.client.post(endpoint,json={})
                self.assertEqual(response.status_code,400)
                self.assertTrue(response.json['requires_api_key'])
            agent.assert_not_called()

    def test_save_preserves_settings_and_never_returns_key(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{},clear=True):
            target=Path(folder)/'.env'
            target.write_text('MAZE_AGENT_MODEL=gpt-4o-mini\nOTHER_SETTING=keep\n',encoding='utf-8')
            dummy='test-only-not-a-real-key'
            with patch.object(web_app,'ENV_FILE',target):
                response=self.client.post('/api/agent/key',json={'api_key':dummy})
                self.assertEqual(response.status_code,200)
                self.assertNotIn(dummy,response.get_data(as_text=True))
                values=dotenv_values(target)
                self.assertEqual(values['MAZE_AGENT_API_KEY'],dummy)
                self.assertEqual(values['OTHER_SETTING'],'keep')
                self.assertEqual(os.environ['MAZE_AGENT_API_KEY'],dummy)
                page=self.client.get('/').get_data(as_text=True)
                self.assertIn('let hasSavedKey = true;',page)
                self.assertNotIn(dummy,page)
                self.client.post('/api/agent/key',json={'api_key':'another-test-placeholder'})
                self.assertEqual(target.read_text().count('MAZE_AGENT_API_KEY='),1)

    def test_invalid_values_are_not_saved(self):
        with patch.object(web_app,'set_key') as save:
            for value in ('', ' ', 'x\nOTHER=y', 'x\r', 'x\x00', 'x'*1001, None, 42):
                response=self.client.post('/api/agent/key',json={'api_key':value})
                self.assertEqual(response.status_code,400)
            save.assert_not_called()


if __name__=='__main__': unittest.main()
