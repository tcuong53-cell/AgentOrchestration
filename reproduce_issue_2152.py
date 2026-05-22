import unittest
from src.api.routes import run_detail_api

class TestReproduceIssue2152(unittest.TestCase):
    def test_run_detail_api_with_invalid_input(self):
        # Arrange
        invalid_input = {"tenant_id": "invalid", "run_id": 123}
        
        # Act & Assert
        with self.assertRaises(Exception) as context:
            run_detail_api(invalid_input)
        
        self.assertEqual(context.exception.status_code, 400)

if __name__ == '__main__':
    unittest.main()