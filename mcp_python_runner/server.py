"""
A Model Context Protocol server for running python code, optionally producing output files.
"""

import os
import sys
import subprocess
import tempfile
import argparse
from pathlib import Path
import asyncio
from tempfile import mkdtemp
from mcp.server.fastmcp import FastMCP, Image

if sys.platform == "win32" and os.environ.get('PYTHONIOENCODING') is None:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

_default_working_dir = Path(tempfile.gettempdir()) / 'python_outputs'

# Parse command line arguments to get the working directory
# Use a default value that works when run via uvx
parser = argparse.ArgumentParser(description='MCP Python Runner')
parser.add_argument('--dir', type=str, default=str(_default_working_dir),
                    help='Working directory for code execution and file operations')
# parser.add_argument('--packages', type=str, default="matplotlib scipy pandas",
#                     help='default packages to install')

args, _ = parser.parse_known_args()

if not args.dir:
    default_working_dir = Path(mkdtemp('python_outputs'))
else:
    default_working_dir = Path(args.dir).absolute()

default_working_dir.mkdir(parents=True, exist_ok=True)
installed_packages = {}


async def initialize_working_dir(requirements):
    """Initialize the working directory"""
    default_working_dir.mkdir(parents=True, exist_ok=True)

    # Create cache directory if it doesn't exist
    cache_dir = default_working_dir / ".cache"
    cache_dir.mkdir(exist_ok=True)

    # Set UV_CACHE_DIR environment variable to use our cache directory
    os.environ["UV_CACHE_DIR"] = str(cache_dir)

    if requirements:
        await install_requirements(default_working_dir, requirements)
    return default_working_dir


async def install_requirements(working_dir: Path, requirements: str):
    done = installed_packages.get(working_dir) or []
    if requirements and (to_install := set(requirements.split()).difference(done)):
        installed_packages[working_dir] = to_install.union(done)

        # Use the current Python executable
        process = await asyncio.create_subprocess_exec(
            "uv", "--quiet", "pip", "install", *list(to_install),
            "--python", sys.executable,
            stdout=subprocess.PIPE,  # Capture stdout to avoid polluting MCP stdio
            stderr=subprocess.PIPE,  # Capture stderr separately
            cwd=working_dir
        )

        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                f"Failed to install requirements using uv in working directory: {stderr.decode('utf-8')}")


# Create our MCP server
mcp = FastMCP(
    "Python Runner",
    description="Execute Python code to calculate results and/or plots or files",
    dependencies=["mcp[cli]"]
)


@mcp.tool()
async def execute_python_code(
        code: str,
        requirements: str = "",
) -> tuple[str, tuple[Image]]:
    """
    Execute Python code in working_dir and return the result.
    If the code produces files or plots, they will be saved in the working directory.

    Args:
        code: Python code to execute;
            The code must be self-contained, including all necessary imports and setup.
            It should not use '.show()' for plots; instead, always save the figure to disk. Unless otherwise specified,
            save into a png file at high resolution (dpi>=300).
            For nice plots, use latex for maths in labels and titles.
            Filename for the plots should have the following format: `<plot_name>_<plot_number>_<timestamp>.<format>`.
        requirements: python package dependencies, e.g. 'matplotlib scipy'
    Returns:
        output text or error, and list of any output files
    """

    working_dir = await initialize_working_dir(requirements)

    if code.startswith("```python") and code.endswith("```"):
        # Remove the code block markers
        code = code[11:-3].strip()

    # Create a temporary file for the code
    with tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=True, delete_on_close=False,
                                     dir=working_dir, encoding='utf-8') as temp:
        temp.write(code)
        temp.close()
        temp_path = temp.name

        files_before = set(os.listdir(working_dir))

        # Use the current Python executable
        process = await asyncio.create_subprocess_exec(
            sys.executable, temp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            lines = [stdout.decode('utf-8').strip()]

            # Directories and files to exclude from output listing
            excluded_dirs = {'.cache'}

            # Get list of files after execution
            files_after = set(os.listdir(working_dir)) - excluded_dirs

            # Find new files created during execution
            new_files = files_after - files_before

            if output_files := list(sorted(new_files)):
                if len(output_files) == 1 and (os.path.splitext(output_files[0])[1].lower()
                                               in {'.png', '.jpg', '.jpeg', '.gif', '.webp'}):
                    return lines[0], (read_image_file(output_files[0]),)

                if external_path := os.environ.get('HOST_PROJECT_PATH'):
                    lines.append(f'Files created at {external_path}:')
                else:
                    lines.append('Output Files:')
                lines += output_files

        else:
            raise Exception(stderr.decode('utf-8').strip())

        return "\n".join(lines), ()


@mcp.tool()
def read_file(file_path: str, max_size_kb: int = 1024) -> str:
    """
    Read the content of any file, with size limits for safety.

    Args:
        file_path: Path to the file (relative to working directory or absolute)
        max_size_kb: Maximum file size to read in KB (default: 1024)

    Returns:
        str: File content or an error message
    """

    path = Path(file_path)
    path = default_working_dir / path

    try:
        if not path.exists():
            raise FileNotFoundError("File '{file_path}' not found")

        # Check file size
        file_size_kb = path.stat().st_size / 1024
        if file_size_kb > max_size_kb:
            raise Exception(f"File size ({file_size_kb:.2f} KB) exceeds maximum allowed size ({max_size_kb} KB)")

        # Determine file type and read accordingly
        try:
            # Try to read as text first
            with open(path, 'r', encoding='utf-8-sig') as f:
                content = f.read()

            # If it's a known source code type, use code block formatting
            source_code_extensions = ['.py', '.js', '.html', '.css', '.json', '.xml', '.md', '.txt', '.sh', '.c',
                                      '.cpp', '.java', '.rb']
            if path.suffix.lower() in source_code_extensions:
                file_type = path.suffix[1:] if path.suffix else 'plain'
                return f"File: {file_path}\n\n```{file_type}\n{content}\n```"

            # For other text files, return as-is
            return f"File: {file_path}\n\n{content}"

        except UnicodeDecodeError:
            # If text decoding fails, read as binary and show hex representation
            with open(path, 'rb') as f:
                content = f.read()
                hex_content = content.hex()
                return f"Binary file: {file_path}\nFile size: {len(content)} bytes\nHex representation (first 1024 chars):\n{hex_content[:1024]}"

    except Exception as e:
        return f"Error reading file {file_path}: {str(e)}"


@mcp.tool()
def read_image_file(file_path: str) -> Image:
    """
    Read an image file from the working directory.
    ...
    """
    # Ensure path is relative to the working directory
    full_path = default_working_dir / Path(file_path)

    # Check if the resolved path is still within the working directory (optional extra check)
    if not str(full_path.resolve()).startswith(str(default_working_dir.resolve())):
        raise ValueError("Access denied: File path is outside the allowed directory.")

    if not full_path.is_file():
        raise FileNotFoundError(f"Image file not found: {file_path}")

    return Image(str(full_path))  # Pass the validated, absolute path


if __name__ == "__main__":
    mcp.run()
