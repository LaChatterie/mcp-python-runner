import asyncio
import subprocess
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    # Create a unique container name to track and stop it later
    container_name = "python_runner_test"

    # Set up the server parameters for stdio connection with named container
    server_params = StdioServerParameters(
        command="docker",
        args=["run", "--rm", "-i", "--name", container_name, "-v", r"c:/tmp/docker_tmp:/project",
              "-e", "HOST_PROJECT_PATH=/tmp/docker_tmp", "cmbant/python-runner"],
        env=None,
    )

    try:
        # Start the MCP client and connect to the server
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # List available tools for verification
                tools = await session.list_tools()
                print("Available tools:", [t.name for t in tools.tools])

                # Call the tool with arguments (replace as needed)
                result = await session.call_tool("execute_python_code",
                                                 arguments={"code": "print(4)", "requirements": "matplotlib scipy"})
                print("Tool result:", result)

    finally:
        # Stop the container after the test is complete
        print("Stopping container...")
        subprocess.run(["docker", "stop", container_name], check=False)


if __name__ == "__main__":
    asyncio.run(main())
