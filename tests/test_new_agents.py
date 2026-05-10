"""Tests for newly ported agents: classic, Q-learning, actor-critic, neuro-evolution."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

np = pytest.importorskip("numpy")

from openbb_forecast.agents.factory import create_agent, AGENT_NAMES
from openbb_forecast.agents.base import BaseAgent
from openbb_forecast.agents.classic import RLAgentBase


@pytest.fixture
def tiny_prices():
    np.random.seed(42)
    return (100 + np.cumsum(np.random.randn(200) * 0.5)).astype(np.float32)


class TestClassicAgents:
    @pytest.mark.parametrize("name", ["turtle", "moving_average", "signal_rolling", "abcd_strategy"])
    def test_classic_agents_create(self, name):
        agent = create_agent(name)
        assert isinstance(agent, RLAgentBase)

    @pytest.mark.parametrize("name", ["turtle", "moving_average"])
    def test_classic_agents_act(self, name, tiny_prices):
        agent = create_agent(name)
        action = agent.act(float(tiny_prices[0]), 0, tiny_prices)
        assert action in (-1, 0, 1)

    def test_classic_backtest(self, tiny_prices):
        agent = create_agent("turtle")
        result = agent.backtest(tiny_prices)
        assert "final_value" in result
        assert "profit" in result


class TestQLearningAgents:
    @pytest.mark.parametrize("name", ["q_learning", "double_q_learning", "duel_q_learning"])
    def test_qlearning_create(self, name):
        agent = create_agent(name)
        assert isinstance(agent, BaseAgent)

    @pytest.mark.parametrize("name", ["q_learning"])
    def test_qlearning_select_action(self, name):
        agent = create_agent(name)
        state = np.random.randn(5).astype(np.float32)
        action = agent.select_action(state)
        assert int(action) in (-1, 0, 1)


class TestActorCriticAgents:
    @pytest.mark.parametrize("name", ["actor_critic", "actor_critic_duel", "actor_critic_recurrent"])
    def test_actor_critic_create(self, name):
        agent = create_agent(name)
        assert isinstance(agent, BaseAgent)

    def test_actor_critic_select_action(self):
        agent = create_agent("actor_critic")
        state = np.random.randn(5).astype(np.float32)
        action = agent.select_action(state)
        assert int(action) in (-1, 0, 1)


class TestNeuroEvolutionAgents:
    @pytest.mark.parametrize("name", ["neuro_evolution", "neuro_evolution_novelty"])
    def test_neuro_evolution_create(self, name):
        agent = create_agent(name)
        assert isinstance(agent, BaseAgent)


class TestAgentFactory:
    def test_all_agents_registered(self):
        assert len(AGENT_NAMES) >= 15
        assert "turtle" in AGENT_NAMES
        assert "q_learning" in AGENT_NAMES
        assert "actor_critic" in AGENT_NAMES
        assert "neuro_evolution" in AGENT_NAMES

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError):
            create_agent("nonexistent_agent")

    def test_create_original_agents(self):
        for name in ["double_dqn", "policy_gradient", "evolution_strategy"]:
            agent = create_agent(name, state_size=5, action_size=3)
            assert isinstance(agent, BaseAgent)
