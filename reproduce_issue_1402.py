import unittest

class TestConfig(unittest.TestCase):
    def test_config_load_env_overrides(self):
        # Mock environment variables to simulate AO_ variables
        import os
        os.environ['AO_AGENT_ID'] = '12345'
        
        from src.common.config import Config
        
        # Load the configuration with overrides
        config = Config._load_env_overrides()
        
        # Check if only documented override keys are imported
        self.assertIn('AO_AGENT_ID', config)
        self.assertNotIn('UNRELATED_VARIABLE', config)

if __name__ == '__main__':
    unittest.main(argv=[''], exit=False)