import json
import os

class ConfigError(Exception):
    """Base exception for configuration-related errors."""
    pass

class ConfigLoadError(ConfigError):
    """
    Exception raised for errors during config file loading.

    Includes context like file path, line, and column for better debugging.
    """
    def __init__(self, message, file_path=None, line=None, col=None):
        super().__init__(message)
        self.file_path = file_path
        self.line = line
        self.col = col

    def __str__(self):
        details = f"{self.args[0]}"
        if self.file_path:
            details += f"\n  File: {self.file_path}"
        if self.line is not None:
            details += f"\n  Line: {self.line}"
        if self.col is not None:
            details += f"\n  Column: {self.col}"
        return details


class Config:
    def __init__(self):
        self._data = {}

    def load(self, file_path):
        """
        Loads configuration data from a JSON file.

        Args:
            file_path (str): The path to the configuration file.

        Raises:
            ConfigLoadError: If the file is not found, is not a regular file,
                             contains malformed JSON, or any other error
                             occurs during loading.
        """
        if not os.path.isfile(file_path):
            # Handles both "file not found" and "path is a directory/special file"
            raise ConfigLoadError(f"Config file not found or is not a regular file: '{file_path}'", file_path=file_path)

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                self._data = json.load(f)
        except json.JSONDecodeError as e:
            # Catch JSON parsing errors and re-raise with path context
            raise ConfigLoadError(
                f"Malformed JSON in config file: {e.msg}",
                file_path=file_path,
                line=e.lineno,
                col=e.colno
            ) from e
        except Exception as e:
            # Catch other potential file reading/parsing errors (e.g., permission denied, encoding issues)
            raise ConfigLoadError(
                f"An unexpected error occurred while reading or parsing config file: {e}",
                file_path=file_path
            ) from e

    def get(self, key, default=None):
        """
        Retrieves a configuration value by key.

        Args:
            key (str): The configuration key.
            default: The default value to return if the key is not found.

        Returns:
            Any: The configuration value or the default if not found.
        """
        return self._data.get(key, default)

    def set(self, key, value):
        """
        Sets a configuration value.

        Args:
            key (str): The configuration key.
            value (Any): The value to set.
        """
        self._data[key] = value

    def to_dict(self):
        """
        Returns the entire configuration as a dictionary.

        Returns:
            dict: The configuration data.
        """
        return self._data