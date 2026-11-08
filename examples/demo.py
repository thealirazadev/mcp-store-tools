"""Call mcp-store-tools the way an agent does, against Shopify's public demo store.

uv run python examples/demo.py
"""

import asyncio
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = StdioServerParameters(
    command=sys.executable, args=["-m", "mcp_store_tools.server", "--demo"]
)


def show(call: str) -> None:
    print(f"\n\033[1;36m> {call}\033[0m")


async def main() -> None:
    async with stdio_client(SERVER) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        async def tool(name: str, **arguments: Any) -> Any:
            result = await session.call_tool(name, arguments)
            content = result.structured_content or {}
            return content.get("result", content)

        show('search_products(query="shirt", max_price=45, in_stock_only=True)')
        for product in await tool(
            "search_products", query="shirt", max_price=45, in_stock_only=True
        ):
            print(f"  {product['title']:<22} {product['price']:<12} in stock")

        handle = "men-t-shirt"
        show(f'get_product(handle="{handle}")')
        product = await tool("get_product", handle=handle)
        for variant in product["variants"][:4]:
            options = ", ".join(f"{k}: {v}" for k, v in variant["options"].items())
            stock = "in stock" if variant["available"] else "sold out"
            print(f"  {options:<28} {variant['price']:<10} {stock}")

        show('browse_products(sort="price_high_to_low", limit=3)')
        page = await tool("browse_products", sort="price_high_to_low", limit=3)
        for product in page["products"]:
            print(f"  {product['title']:<22} {product['price']}")

        show('get_policy(kind="refund")')
        policy = await tool("get_policy", kind="refund")
        print(f"  {policy['text'].splitlines()[0][:76]}...")


if __name__ == "__main__":
    asyncio.run(main())
