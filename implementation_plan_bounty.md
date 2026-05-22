# Implementation Plan

## Root Cause Analysis
The bug arises from the fact that the `_load_env_overrides` function imports all AO_ variables, which can lead to runtime-only values leaking into config snapshots. This is because the function does not have a mechanism to filter or scope these variables.

## Planned Modifications
1. **Add an Allowlist for Config Overrides**: Implement a list of allowed override keys that should be imported by default. The `_load_env_overrides` function will now only import these specified keys, ensuring that runtime-only values do not leak into config snapshots.
2. **Update the Function Logic**: Modify the `_load_env_overrides` function to check against this allowlist and only import the allowed keys.

### Code Changes
```python
# src/common/config.py

class Config:
    @staticmethod
    def _load_env_overrides():
        # Define a list of allowed override keys
        ALLOWED_OVERRIDES = ['AO_AGENT_ID']
        
        # Load environment variables
        env_vars = {k: v for k, v in os.environ.items() if k.startswith('AO_')}
        
        # Filter and import only the allowed overrides
        filtered_env_vars = {key: value for key, value in env_vars.items() if key in ALLOWED_OVERRIDES}
        
        return filtered_env_vars
```

### Regression Test
Create a new test case to verify that the `_load_env_overrides` function correctly filters and imports only the allowed override keys. This can be done by setting up a mock environment with AO_ variables and checking the output of the function.

```python
# tests/test_config.py

import unittest
from src.common.config import Config

class TestConfig(unittest.TestCase):
    def test_load_env_overrides(self):
        # Set up a mock environment with AO_ variables
        os.environ['AO_AGENT_ID'] = '12345'
        
        # Call the function and check the output
        result = Config._load_env_overrides()
        
        # Assert that only the allowed override key is imported
        self.assertEqual(result, {'AO_AGENT_ID': '12345'})

if __name__ == '__main__':
    unittest.main()
```

### Documentation
Document the changes in the `Config` class to ensure future developers understand the new behavior and how it affects the config loading process.

```python
class Config:
    @staticmethod
    def _load_env_overrides():
        # Define a list of allowed override keys
        ALLOWED_OVERRIDES = ['AO_AGENT_ID']
        
        # Load environment variables
        env_vars = {k: v for k, v in os.environ.items() if k.startswith('AO_')}
        
        # Filter and import only the allowed overrides
        filtered_env_vars = {key: value for key, value in env_vars.items() if key in ALLOWED_OVERRIDES}
        
        return filtered_env_vars
        
    # Documentation
    """
    Loads environment variables that start with 'AO_'.
    
    Only the keys specified in ALLOWED_OVERRIDES are imported to prevent runtime-only values from leaking into config snapshots.
    """
```

### Bounty Gate Verification
Ensure that the repository has at least one star before submitting the pull request. This can be done by checking the star count using the GitHub API or CLI as described in the bounty gate instructions.

By following these steps, we ensure that the bug is fixed and that the implementation meets the acceptance criteria.