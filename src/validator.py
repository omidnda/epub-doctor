import json
import subprocess
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
EPUBCHECK_JAR = PROJECT_ROOT / "vendor" / "epubcheck-5.4.0" / "epubcheck.jar"
EPUBCHECK_TIMEOUT_SECONDS = 60
EPUBCHECK_MAX_HEAP = "512m"


class EPUBCheckError(RuntimeError):
    """EPUBCheck could not run or did not produce a usable report."""


def validate_epub(epub_path: str) -> dict:
    epub_path = Path(epub_path)
    if not epub_path.is_file():
        raise FileNotFoundError(f"EPUB file not found: {epub_path}")
    if not EPUBCHECK_JAR.is_file():
        raise EPUBCheckError(f"EPUBCheck JAR not found: {EPUBCHECK_JAR}")

    # A closed report path also works on platforms that lock open temp files.
    with tempfile.TemporaryDirectory() as directory:
        report_path = Path(directory) / "report.json"
        try:
            result = subprocess.run(
                ["java", f"-Xmx{EPUBCHECK_MAX_HEAP}", "-jar", str(EPUBCHECK_JAR), str(epub_path),
                 "--json", str(report_path)],
                capture_output=True,
                text=True,
                timeout=EPUBCHECK_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            # run() kills and waits for the child before raising; omit captured output.
            raise EPUBCheckError(
                f"EPUBCheck timed out after {EPUBCHECK_TIMEOUT_SECONDS} seconds"
            ) from exc
        except FileNotFoundError as exc:
            raise EPUBCheckError("Java executable not found") from exc
        except OSError as exc:
            raise EPUBCheckError(f"Could not execute EPUBCheck: {exc}") from exc

        if result.returncode not in (0, 1):
            detail = result.stderr.strip() or result.stdout.strip()
            raise EPUBCheckError(
                f"EPUBCheck execution failed (exit {result.returncode}): {detail}"
            )
        try:
            with report_path.open(encoding="utf-8") as report_file:
                report = json.load(report_file)
            checker = report["checker"]
            counts = [checker[key] for key in ("nFatal", "nError", "nWarning")]
            if any(type(count) is not int or count < 0 for count in counts):
                raise ValueError("Invalid report counts")
            messages = report["messages"]
            publication = report["publication"]
            if not isinstance(messages, list) or not isinstance(publication, dict):
                raise ValueError("Invalid report fields")
            if any(not isinstance(msg, dict) or not isinstance(msg.get("ID"), str)
                   for msg in messages):
                raise ValueError("Invalid report messages")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise EPUBCheckError(f"Missing or malformed EPUBCheck JSON: {exc}") from exc

    return {
        "valid": result.returncode == 0 and counts[0] == 0 and counts[1] == 0,
        "return_code": result.returncode,
        "fatal_count": counts[0],
        "error_count": counts[1],
        "warning_count": counts[2],
        "messages": messages,
        "publication": publication,
    }
