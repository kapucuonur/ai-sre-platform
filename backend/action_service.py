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

    # Intercept skills.* calls and route them over SSH to the SRE Daemon host
    if command.strip().startswith("skills."):
        import json
        logger.info("[SSH-SKILL] Routing skill command over SSH to Pi 5 daemon host")
        
        # Prepare python script to execute on the Pi 5 host
        py_cmd = (
            "import sys; sys.path.append('/home/pi/sre'); import skills, sre_daemon; "
            "orchestrator = sre_daemon.HealingOrchestrator(); import json; "
            "match = __import__('re').match(r'^skills\\.([a-zA-Z0-9_]+)\\((.*)\\)$', sys.argv[1]); "
            "name = match.group(1); params = json.loads(match.group(2)); "
            "ok, out = skills.execute_skill(name, params, orchestrator); "
            "print(out if ok else 'Error: ' + out); sys.exit(0 if ok else 1)"
        )
        
        ssh_cmd = [
            "sshpass", "-p", "pi",
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password",
            "pi@192.168.1.116",
            f"python3 -c {json.dumps(py_cmd)} {json.dumps(command.strip())}"
        ]
        
        try:
            result = subprocess.run(
                ssh_cmd,
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
            logger.error("SSH skill command timed out: %s", command)
            return "Error: SSH skill command timed out after 45 seconds."
        except Exception as e:
            logger.error("SSH skill command failed: %s", e)
            return f"Error executing SSH skill command: {str(e)}"

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
