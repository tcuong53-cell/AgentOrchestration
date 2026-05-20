import sys

# --- Output Helpers ---
def print_error(message: str):
    """
    Prints an error message to stderr, ensuring it's not mixed with stdout data.
    Automatically flushes the stream.
    """
    print(f"Error: {message}", file=sys.stderr, flush=True)

def print_data(message: str):
    """
    Prints standard output data to stdout.
    Automatically flushes the stream.
    """
    print(message, file=sys.stdout, flush=True)
# --- End Output Helpers ---

def main(args: list[str]) -> int:
    """
    Main entry point for the CLI tool.
    Dispatches commands and ensures proper stderr/stdout usage as per the bug report.
    
    Args:
        args: A list of command-line arguments, where args[0] is the script name.
    
    Returns:
        An exit code (0 for success, non-zero for failure).
    """
    if len(args) < 2:
        print_error("No command provided. Usage: cli_tool [command] [args...]")
        return 1

    command_name = args[1]
    command_args = args[2:]

    # Example: Command validation failure
    if command_name == "validate_fail":
        print_error(f"Command '{command_name}' failed internal validation checks.")
        # Additional detailed error messages also go to stderr
        print_error("Reason: Required parameter 'config-file' was not provided.")
        return 1

    # Example: Command that produces data
    if command_name == "get_info":
        if not command_args:
            print_error("Command 'get_info' requires an argument (e.g., 'user', 'system').")
            return 1
        
        target = command_args[0]
        print_data(f"Retrieving information for: {target}")
        # Simulate data output to stdout
        if target == "user":
            print_data("User ID: 12345")
            print_data("Username: bounty_hunter_ai")
            print_data("Status: Active")
        elif target == "system":
            print_data("OS: Linux")
            print_data("Kernel: 5.15.0")
            print_data("Architecture: x86_64")
        else:
            print_error(f"Unknown information target: '{target}'.")
            return 1
        return 0

    # Example: Another command that processes data
    if command_name == "process_data":
        if not command_args:
            print_error("Command 'process_data' requires input data.")
            return 1
        
        processed_output = f"Processed: {' '.join(command_args).upper()}"
        print_data(processed_output)
        print_data("Data processing complete.")
        return 0

    # Default case for unknown commands
    print_error(f"Unknown command: '{command_name}'.")
    print_error("Available commands: validate_fail, get_info, process_data.")
    return 1


if __name__ == "__main__":
    # The `sys.argv` list contains the script name itself as the first element.
    # We pass it directly to `main` to mimic how a CLI tool receives arguments.
    exit_code = main(sys.argv)
    sys.exit(exit_code)