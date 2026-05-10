"""AgentFactory — create any trading agent by name.

Port of AgentFactory from Stock-Prediction-Models, adapted to work
alongside existing openbb_forecast agents.
"""

from __future__ import annotations

from openbb_forecast.agents.base import BaseAgent
from openbb_forecast.agents.double_dqn import DoubleDQNAgent
from openbb_forecast.agents.policy_gradient import PolicyGradientAgent
from openbb_forecast.agents.evolution_strategy import EvolutionStrategyAgent
from openbb_forecast.agents.classic import CLASSIC_AGENTS
from openbb_forecast.agents.q_learning import Q_LEARNING_AGENTS
from openbb_forecast.agents.actor_critic import ACTOR_CRITIC_AGENTS
from openbb_forecast.agents.neuro_evolution import NEURO_EVOLUTION_AGENTS


AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    # Original openbb_forecast agents
    "double_dqn": DoubleDQNAgent,
    "policy_gradient": PolicyGradientAgent,
    "evolution_strategy": EvolutionStrategyAgent,
}

# Merge ported agents
AGENT_REGISTRY.update(CLASSIC_AGENTS)
AGENT_REGISTRY.update(Q_LEARNING_AGENTS)
AGENT_REGISTRY.update(ACTOR_CRITIC_AGENTS)
AGENT_REGISTRY.update(NEURO_EVOLUTION_AGENTS)

AGENT_NAMES = list(AGENT_REGISTRY.keys())


def create_agent(agent_name: str, **kwargs) -> BaseAgent:
    """Create a trading agent by name.

    Args:
        agent_name: One of AGENT_NAMES.
        **kwargs: Passed to the agent constructor.

    Returns:
        A BaseAgent instance.
    """
    key = agent_name.lower().replace("-", "_")
    factory = AGENT_REGISTRY.get(key)
    if factory is None:
        raise ValueError(
            f"Unknown agent '{agent_name}'. "
            f"Available: {sorted(AGENT_NAMES)}"
        )
    return factory(**kwargs)
