import sys
import asyncio
import time
from loguru import logger
from dotenv import load_dotenv
load_dotenv()
from agent.core.agent import Agent
from agent.llm.hams_max_provider import HamsMaxLLM
from agent.tools.registry import ToolRegistry

async def main():
    logger.remove()
    logger.add(sys.stdout, level="INFO")
    
    # Init registry and tools automatically loaded?
    registry = ToolRegistry.default()
    llm = HamsMaxLLM(model="groq")
    agent = Agent(llm=llm, tool_registry=registry, verbose=True)
    
    task = "Cari framework JavaScript terpopuler 2025 dan buat laporan"
    t0 = time.time()
    
    resp = await agent.run(task)
    
    t1 = time.time()
    duration = t1 - t0
    
    print("\n" + "="*50)
    print(f"VERIFICATION RESULTS")
    print(f"Time taken: {duration:.2f} seconds")
    print(f"Success: {resp.success}")
    print(f"Final Answer:")
    print("-" * 50)
    print(resp.final_answer)
    print("=" * 50)
    
if __name__ == "__main__":
    asyncio.run(main())
