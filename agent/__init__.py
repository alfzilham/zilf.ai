"""
Zilf AI â€” autonomous coding assistant.

Public API::

    from agent import Agent, run_agent

    result = await Agent.create().run("Fix the failing tests in auth.py")
"""

from agent.core.agent import Agent, AgentResponse
from agent.core.state import AgentState, AgentStatus

__all__ = ["Agent", "AgentResponse", "AgentState", "AgentStatus"]
__version__ = "0.1.0"
