import subprocess
import logging

logger = logging.getLogger("action_service")

def execute_command(command: str) -> str:
    """
    Executes a shell command safely, captures stdout/stderr, and returns the result.
    """
    if not command.strip():
        return "No command provided."

    logger.info("Executing recovery command: %s", command)
    try:
        # Run command with 45 second timeout
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=45
        )
        
        output = []
        if result.stdout:
            output.append("=== STDOUT ===")
            output.append(result.stdout.strip())
        if result.stderr:
            output.append("=== STDERR ===")
            output.append(result.stderr.strip())
        
        status_code = result.returncode
        output.append(f"\nExit Code: {status_code}")
        
        return "\n".join(output)
    except subprocess.TimeoutExpired:
        logger.error("Command timed out: %s", command)
        return "Error: Command timed out after 45 seconds."
    except Exception as e:
        logger.error("Command execution failed: %s", e)
        return f"Error executing command: {str(e)}"
