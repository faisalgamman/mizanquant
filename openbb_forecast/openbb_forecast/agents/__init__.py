"""RL trading agents with risk management and transaction costs."""

from openbb_forecast.agents.double_dqn import DoubleDQNAgent
from openbb_forecast.agents.evolution_strategy import EvolutionStrategyAgent
from openbb_forecast.agents.policy_gradient import PolicyGradientAgent
from openbb_forecast.agents.classic import TurtleAgent, MovingAverageAgent, SignalRollingAgent, ABCDStrategyAgent
from openbb_forecast.agents.q_learning import QLearningAgent, DoubleQLearningAgent, CuriosityQLearningAgent
from openbb_forecast.agents.actor_critic import ActorCriticAgent
from openbb_forecast.agents.neuro_evolution import NeuroEvolutionAgent
from openbb_forecast.agents.factory import create_agent, AGENT_NAMES

__all__ = [
    "DoubleDQNAgent", "PolicyGradientAgent", "EvolutionStrategyAgent",
    "TurtleAgent", "MovingAverageAgent", "SignalRollingAgent", "ABCDStrategyAgent",
    "QLearningAgent", "DoubleQLearningAgent", "CuriosityQLearningAgent",
    "ActorCriticAgent", "NeuroEvolutionAgent",
    "create_agent", "AGENT_NAMES",
]
