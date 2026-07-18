from mcp.server.fastmcp import FastMCP

# Create the MCP server instance
mcp = FastMCP("Age Database")

# Simulated database
AGE_DATABASE = {
    "Alice": 30,
    "Bob": 25,
    "Charlie": 35,
    "Diana": 28,
}

@mcp.tool()
def get_age(name: str) -> str:
    """
    Retrieve the age of a person by their name.
    
    Args:
        name: The name of the person to look up.
        
    Returns:
        The age of the person as a string, or a message if the person is not found.
    """
    if name in AGE_DATABASE:
        return f"{name} is {AGE_DATABASE[name]} years old."
    else:
        return "User not found"

if __name__ == "__main__":
    # Run the server using stdio transport
    mcp.run(transport="stdio")
