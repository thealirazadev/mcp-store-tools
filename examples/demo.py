"""Call mcp-store-tools the way an agent does, against Shopify's public demo store.

uv run python examples/demo.py
"""

import asyncio
import os
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
    with open(os.devnull, "w") as quiet:
        async with (
            stdio_client(SERVER, errlog=quiet) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()

            async def tool(name: str, **arguments: Any) -> Any:
                result = await session.call_tool(name, arguments)
                content = result.structured_content or {}
                return content.get("result", content)

            show('search_products(query="shirt", max_price=45, in_stock_only=True)')
            found = await tool("search_products", query="shirt", max_price=45, in_stock_only=True)
            for product in found:
                print(f"  {product['title']:<22} {product['price']:<12} in stock")

            handle = "men-t-shirt"
            show(f'get_product(handle="{handle}")')
            product = await tool("get_product", handle=handle)
            for variant in product["variants"][:3]:
                options = ", ".join(f"{k}: {v}" for k, v in variant["options"].items())
                print(f"  {options:<28} {variant['price']:<10} id {variant['id']}")

            show(f'get_recommendations(handle="{handle}", kind="goes_with", limit=3)')
            for match in await tool(
                "get_recommendations", handle=handle, kind="goes_with", limit=3
            ):
                print(f"  {match['title']:<22} {match['price']}")

            variant = product["variants"][0]
            show(f"build_cart_link(items=[{{handle, variant_id: {variant['id']}, quantity: 2}}])")
            items = [{"handle": handle, "variant_id": variant["id"], "quantity": 2}]
            link = await tool("build_cart_link", items=items)
            print(f"  {link['url']}")
            print(f"  subtotal {link['subtotal']} (before shipping and taxes)")

            show('get_policy(kind="refund")')
            policy = await tool("get_policy", kind="refund")
            print(f"  {policy['text'].splitlines()[0][:76]}...")


if __name__ == "__main__":
    asyncio.run(main())
