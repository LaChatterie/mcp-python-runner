"""
A Model Context Protocol server for running python code, optionally producing output files.
"""

import os
import sys
import subprocess
import tempfile
import argparse
from pathlib import Path
from typing import List, Optional
# from contextlib import asynccontextmanager
# from collections.abc import AsyncIterator
import asyncio
from tempfile import mkdtemp
from pydantic import BaseModel
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
args, _ = parser.parse_known_args()

if not args.dir:
    default_working_dir = Path(mkdtemp('python_outputs'))
else:
    default_working_dir = Path(args.dir).absolute()

default_working_dir.mkdir(parents=True, exist_ok=True)

# Create our MCP server
mcp = FastMCP(
    "Python Runner",
    description=f"Execute Python code to calculate results and/or plots or files",
    dependencies=["mcp[cli]", "scipy", "pandas", "matplotlib", "sympy"]
)


class CodeExecutionResponse(BaseModel):
    stdout: str
    stderr: str
    output_files: List[str] = []


installed_packages = {}


def initialize_working_dir():
    """Initialize the working directory"""
    default_working_dir.mkdir(parents=True, exist_ok=True)
    if not any(default_working_dir.glob("*.toml")):
        # Create a new toml file if it doesn't exist
        process = subprocess.run(
            ["uv", "init"],
            capture_output=True, text=True,
            cwd=default_working_dir, check=False
        )
        if process.returncode != 0:
            raise RuntimeError(f"Failed to initialize using uv in working directory: {process.stderr}")
    return default_working_dir


@mcp.tool()
async def execute_python_code(
        code: str,
        requirements: str = "",
) -> CodeExecutionResponse:
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
        Dict with stdout, stderr, and list of any output files
    """

    working_dir = initialize_working_dir()

    if code.startswith("```python") and code.endswith("```"):
        # Remove the code block markers
        code = code[11:-3].strip()

    # Create a temporary file for the code
    with tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False,
                                     dir=working_dir, encoding='utf-8') as temp:
        temp.write(code)
        temp.close()
        temp_path = temp.name

        if requirements and (
                to_install := set(requirements.split()).difference(installed_packages.get(working_dir) or [])):
            installed_packages[working_dir] = to_install.union(installed_packages.get(working_dir, []))

            process = await asyncio.create_subprocess_exec(
                "uv", "add", *list(to_install),
                stdout=subprocess.PIPE,  # Capture stdout to avoid polluting MCP stdio
                stderr=subprocess.PIPE,  # Capture stderr separately
                cwd=working_dir
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                return CodeExecutionResponse(
                    stdout="",
                    stderr=stderr.decode('utf-8'),
                    output_files=[]
                )

        files_before = set(os.listdir(working_dir))

        process = await asyncio.create_subprocess_exec(
            *["uv", "run", temp_path],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            # Get list of files after execution
            files_after = set(os.listdir(working_dir)) - {'uv.lock'}

            # Find new files created during execution
            new_files = files_after - files_before

            output_files = [os.path.normpath(os.path.join(working_dir, file)) for file in sorted(new_files)]
        else:
            output_files = []

        return CodeExecutionResponse(
            stdout=stdout.decode('utf-8'),
            stderr=stderr.decode('utf-8'),
            output_files=output_files
        )


# ============================================================================
# Resources
# ============================================================================

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
            return f"Error: File '{file_path}' not found"

        # Check file size
        file_size_kb = path.stat().st_size / 1024
        if file_size_kb > max_size_kb:
            return f"Error: File size ({file_size_kb:.2f} KB) exceeds maximum allowed size ({max_size_kb} KB)"

        # Determine file type and read accordingly
        try:
            # Try to read as text first
            with open(path, 'r', encoding='utf-8') as f:
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
