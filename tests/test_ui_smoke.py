import subprocess, time, os, signal

def test_ui_starts():
    """Spin up the Streamlit UI in headless mode and ensure it launches without error."""
    cwd = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    cmd = ["streamlit", "run", "ui/dashboard.py", "--server.headless", "true", "--server.port", "8502"]
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Give it a few seconds to start up
    time.sleep(5)
    # Terminate gracefully
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    # Return code can be 0 (normal exit) or -15 (terminated by SIGTERM)
    assert proc.returncode in (0, -15)
